from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_cs2_results import main


def _scheduled_match(
    timestamp_ms: int,
    *,
    status: str = "finished",
    team1: str = "Alpha",
    team2: str = "Beta",
) -> main.hltv.ScheduledMatch:
    return main.hltv.ScheduledMatch(
        match_id=str(timestamp_ms),
        event_id="9001",
        event_name="Test Event",
        event_logo=None,
        team1=team1,
        team2=team2,
        start_unix=timestamp_ms,
        best_of="bo3",
        status=status,
    )


def _cn_timestamp(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> int:
    cn = timezone(timedelta(hours=8))
    return int(datetime(year, month, day, hour, minute, tzinfo=cn).timestamp() * 1000)


def test_matches_scan_interval_uses_idle_and_live_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main, "_followed", {}, raising=False)

    assert main._matches_scan_interval_seconds() == 10 * 60

    monkeypatch.setattr(
        main,
        "_followed",
        {"1": main.hltv.LiveMatch("1", "https://www.hltv.org/matches/1/x", "9", "Event")},
        raising=False,
    )
    assert main._matches_scan_interval_seconds() == 5 * 60


def test_failure_retry_never_retries_inside_cloudflare_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "fetcher",
        SimpleNamespace(backoff_remaining_seconds=lambda: 30 * 60),
        raising=False,
    )

    assert main._failure_retry_seconds(5 * 60) == 5 * 60


def test_previous_match_day_recap_is_available_for_current_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    previous = [
        _scheduled_match(_cn_timestamp(2026, 10, 3, 20)),
        _scheduled_match(_cn_timestamp(2026, 10, 4, 1, 30)),
    ]
    current = [_scheduled_match(_cn_timestamp(2026, 10, 4, 18), status="upcoming")]
    now_ms = _cn_timestamp(2026, 10, 4, 18)

    recap, label = main._previous_match_day_recap([previous, current], 1, now_ms)

    assert recap == previous
    assert label == "上个比赛日 · 10月3日 周六 20:00 — 次日 01:30"


def test_previous_match_day_recap_hides_stale_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "cfg",
        main.Config(cs2_recap_max_age_hours=12),
        raising=False,
    )
    previous = [_scheduled_match(_cn_timestamp(2026, 10, 3, 1))]
    current = [_scheduled_match(_cn_timestamp(2026, 10, 4, 18), status="upcoming")]

    recap, label = main._previous_match_day_recap(
        [previous, current],
        1,
        _cn_timestamp(2026, 10, 4, 18),
    )

    assert recap == []
    assert label is None


def test_previous_match_day_recap_requires_an_earlier_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    current = [_scheduled_match(_cn_timestamp(2026, 10, 4, 18), status="upcoming")]

    recap, label = main._previous_match_day_recap(
        [current],
        0,
        _cn_timestamp(2026, 10, 4, 18),
    )

    assert recap == []
    assert label is None


def test_member_role_resolves_owner_and_admin_from_group_api() -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})

    owner_event = SimpleNamespace(
        get_sender_id=lambda: "42",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )
    admin_event = SimpleNamespace(
        get_sender_id=lambda: "7",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )
    member_event = SimpleNamespace(
        get_sender_id=lambda: "9",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )

    assert asyncio.run(plugin._resolve_member_role(owner_event)) == "owner"
    assert asyncio.run(plugin._resolve_member_role(admin_event)) == "admin"
    assert asyncio.run(plugin._resolve_member_role(member_event)) == "member"


def test_group_origin_falls_back_to_aiocqhttp_session() -> None:
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp", id="napcat-main")
    )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[platform]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})

    original = main.store.get_group_origin
    main.store.get_group_origin = lambda _group_id: None
    try:
        assert plugin._resolve_group_origin(123456) == (
            "napcat-main:GroupMessage:123456"
        )
        assert plugin._resolve_user_origin(9988) == "napcat-main:FriendMessage:9988"
    finally:
        main.store.get_group_origin = original


def test_html_render_requests_local_file_from_astrbot() -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    plugin.html_render = AsyncMock(return_value="C:/tmp/card.png")

    result = asyncio.run(plugin._html_render("<html></html>"))

    assert result == "C:/tmp/card.png"
    assert plugin.html_render.await_args.args[0] == "<html></html>"
    assert plugin.html_render.await_args.kwargs["return_url"] is False
    assert plugin.html_render.await_args.kwargs["options"]["type"] == "png"


def test_group_leave_and_mute_notices_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    dropped: list[tuple[int, str]] = []
    pruned: list[tuple[int, int]] = []
    monkeypatch.setattr(
        main,
        "drop_unreachable_subscription",
        lambda group_id, *, reason: dropped.append((group_id, reason)),
    )
    monkeypatch.setattr(
        main.store,
        "prune_user_targets",
        lambda group_id, user_id: pruned.append((group_id, user_id)) or 1,
    )
    leave_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "kick_me",
                "group_id": 10001,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )
    member_leave_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "leave",
                "group_id": 10001,
                "user_id": 123,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )

    class _Worker:
        def __init__(self) -> None:
            self.muted: list[tuple[int, int]] = []
            self.cleared: list[int] = []

        def note_group_muted(self, group_id: int, *, seconds: int) -> None:
            self.muted.append((group_id, seconds))

        def clear_group_mute(self, group_id: int) -> bool:
            self.cleared.append(group_id)
            return True

    worker = _Worker()
    monkeypatch.setattr(main, "delivery_worker", worker, raising=False)
    ban_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 10001,
                "duration": 60,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )
    lift_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "lift_ban",
                "group_id": 10001,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )

    asyncio.run(plugin.handle_onebot_notice(leave_event))
    asyncio.run(plugin.handle_onebot_notice(member_leave_event))
    asyncio.run(plugin.handle_onebot_notice(ban_event))
    asyncio.run(plugin.handle_onebot_notice(lift_event))

    assert dropped == [(10001, "机器人离群 notice sub_type=kick_me")]
    assert pruned == [(10001, 123)]
    assert worker.muted == [(10001, 60)]
    assert worker.cleared == [10001]


def test_ongoing_event_detection_refreshes_empty_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"whitelist": set(), "refreshed": False}

    def whitelist_event_ids() -> set[str]:
        return set(state["whitelist"])

    async def refresh_whitelist() -> None:
        state["refreshed"] = True
        state["whitelist"] = {"8244"}

    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main.store, "whitelist_event_ids", whitelist_event_ids)
    monkeypatch.setattr(main, "refresh_whitelist", refresh_whitelist)
    monkeypatch.setattr(
        main,
        "fetcher",
        SimpleNamespace(get_html=AsyncMock(return_value="<html></html>")),
        raising=False,
    )
    monkeypatch.setattr(main.hltv, "tree", lambda html: html)
    monkeypatch.setattr(main.hltv, "featured_event_ids", lambda _tree: {"8244"})
    monkeypatch.setattr(main.hltv, "parse_events", lambda _tree: [])

    assert asyncio.run(main._ongoing_event_ids()) == {"8244"}
    assert state["refreshed"] is True


def test_llm_tool_command_reuses_public_query_and_limits_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    monkeypatch.setattr(
        main,
        "cfg",
        main.Config(cs2_llm_tool_max_image_calls=1),
        raising=False,
    )
    query = AsyncMock(side_effect=main._CommandFinished)
    monkeypatch.setattr(main, "handle_cs2", query)

    class _Event:
        def __init__(self) -> None:
            self.extra: dict[str, object] = {}

        def get_extra(self, key: str, default: object = None) -> object:
            return self.extra.get(key, default)

        def set_extra(self, key: str, value: object) -> None:
            self.extra[key] = value

    event = _Event()
    assert (
        asyncio.run(
            plugin._run_llm_tool_command(event, "赛事", "已发送赛事卡片。")
        )
        == "已发送赛事卡片。"
    )
    assert query.await_count == 1
    event.extra["cs2_llm_tool_calls"] = 1
    assert "上限" in asyncio.run(
        plugin._run_llm_tool_command(event, "日程", "已发送日程卡片。")
    )
    assert query.await_count == 1


def test_llm_intent_hint_only_applies_to_cs2_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)

    cs2_event = SimpleNamespace(message_str="FaZe 今晚比赛战况怎么样")
    cs2_req = SimpleNamespace(system_prompt="")
    asyncio.run(plugin.add_cs2_llm_tool_hint(cs2_event, cs2_req))
    assert "query_cs2_" in cs2_req.system_prompt

    other_req = SimpleNamespace(system_prompt="")
    asyncio.run(
        plugin.add_cs2_llm_tool_hint(
            SimpleNamespace(message_str="今天天气怎么样"),
            other_req,
        )
    )
    assert other_req.system_prompt == ""


def test_query_subcommand_routes_player_and_team_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        is_group=False,
        group_id=None,
        user_id="10001",
    )
    calls: list[tuple[str, str]] = []

    async def player(name: str) -> None:
        calls.append(("player", name))
        raise main._CommandFinished

    async def team(name: str) -> None:
        calls.append(("team", name))
        raise main._CommandFinished

    monkeypatch.setattr(main, "_cooldown_left", lambda _event: 0)
    monkeypatch.setattr(main, "_admin_ctx", lambda _event: False)
    monkeypatch.setattr(main, "_handle_player_detail", player)
    monkeypatch.setattr(main, "_handle_team_detail", team)

    for raw in ("查询 选手 s1mple", "查询 战队 NAVI"):
        with pytest.raises(main._CommandFinished):
            asyncio.run(main.handle_cs2(event, raw))

    assert calls == [("player", "s1mple"), ("team", "NAVI")]


def test_ranking_subcommand_routes_hltv_and_vrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        is_group=False,
        group_id=None,
        user_id="10001",
    )
    calls: list[str] = []

    async def fake_ranking(source: str) -> None:
        calls.append(source)
        raise main._CommandFinished

    monkeypatch.setattr(main, "_cooldown_left", lambda _event: 0)
    monkeypatch.setattr(main, "_admin_ctx", lambda _event: False)
    monkeypatch.setattr(main, "_handle_ranking", fake_ranking)

    for raw in ("排名", "rank vrs", "排行榜 hltv", "查询 排名", "查询 排名 hltv"):
        with pytest.raises(main._CommandFinished):
            asyncio.run(main.handle_cs2(event, raw))

    assert calls == ["vrs", "vrs", "hltv", "vrs", "hltv"]


def test_handle_ranking_executes_and_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished_messages: list[Any] = []

    async def fake_finish(msg: Any) -> None:
        finished_messages.append(msg)
        raise main._CommandFinished

    monkeypatch.setattr(main.cs2, "finish", fake_finish)

    # 1. When teams exist
    mock_teams = [{"rank": 1, "name": "Spirit", "players": ["donk"]}]
    mock_meta = {"source": "hltv", "total_teams": 1}
    monkeypatch.setattr(main.store, "get_ranking_overview", lambda source, limit: (mock_teams, mock_meta))
    monkeypatch.setattr(main.card, "render_ranking_card", AsyncMock(return_value=b"fake-png-ranking"))

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_ranking("hltv"))
    assert finished_messages[-1] == b"fake-png-ranking"

    # 2. When empty
    spawned = []
    def fake_spawn(coro, name):
        spawned.append(name)
        coro.close()
    monkeypatch.setattr(main, "_spawn_background", fake_spawn)
    monkeypatch.setattr(main.store, "get_ranking_overview", lambda source, limit: ([], {"source": "vrs", "total_teams": 0}))

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_ranking("vrs"))
    assert "正在初始化同步中" in str(finished_messages[-1])
    assert "vrs-refresh" in spawned


def test_handle_player_detail_prioritizes_local_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    finished_messages: list[Any] = []

    async def fake_send(msg: str) -> None:
        sent_messages.append(msg)

    async def fake_finish(msg: Any) -> None:
        finished_messages.append(msg)
        raise main._CommandFinished

    live_resolve_called: list[str] = []

    async def fake_live_resolve(fetcher, query, players=None):
        live_resolve_called.append(query)
        return main.names.PlayerResolution("none")

    mock_fetcher = SimpleNamespace(
        get_html=AsyncMock(return_value="<html><div class='playerNickname'>NiKo</div></html>"),
        fetch_impersonated_bytes=AsyncMock(return_value=b"avatar-bytes"),
    )

    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main.cs2, "send", fake_send)
    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main.names, "resolve_player", fake_live_resolve)
    monkeypatch.setattr(main, "fetcher", mock_fetcher, raising=False)
    monkeypatch.setattr(
        main.store,
        "all_index_players",
        lambda: [{"player_id": "3741", "nick": "NiKo", "team": "G2", "team_key": "g2"}],
    )
    monkeypatch.setattr(
        main.card,
        "render_player_profile_card",
        AsyncMock(return_value=b"fake-player-card"),
    )

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_player_detail("小科维奇"))

    # 验证走本地 SQLite 优先命中：
    # 1. 0ms 命中本地名录，直接下发提示语
    assert any("正在查询 NiKo 的近期数据" in m for m in sent_messages)
    # 2. 阻断了在线 live 搜索
    assert live_resolve_called == []
    # 3. 正常完成出图
    assert finished_messages[-1] == b"fake-player-card"


def test_handle_player_detail_fallbacks_to_live_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    finished_messages: list[Any] = []

    async def fake_send(msg: str) -> None:
        sent_messages.append(msg)

    async def fake_finish(msg: Any) -> None:
        finished_messages.append(msg)
        raise main._CommandFinished

    live_resolve_called: list[str] = []

    async def fake_live_resolve(fetcher, query, players=None):
        live_resolve_called.append(query)
        return main.names.PlayerResolution(
            "ok",
            player=main.hltv.SearchPlayer("3741", "NiKo", "", "G2", None),
        )

    mock_fetcher = SimpleNamespace(
        get_html=AsyncMock(return_value="<html><div class='playerNickname'>NiKo</div></html>"),
        fetch_impersonated_bytes=AsyncMock(return_value=b"avatar-bytes"),
    )

    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main.cs2, "send", fake_send)
    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main.names, "resolve_player", fake_live_resolve)
    monkeypatch.setattr(main, "fetcher", mock_fetcher, raising=False)
    monkeypatch.setattr(main.store, "all_index_players", lambda: [])
    monkeypatch.setattr(
        main.card,
        "render_player_profile_card",
        AsyncMock(return_value=b"fake-player-card"),
    )

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_player_detail("NiKo"))

    # 本地未命中时成功 fallback 在线搜索
    assert live_resolve_called == ["NiKo"]
    assert any("正在查询 NiKo 的近期数据" in m for m in sent_messages)
    assert finished_messages[-1] == b"fake-player-card"


def test_handle_team_detail_prioritizes_local_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    finished_messages: list[Any] = []

    async def fake_send(msg: str) -> None:
        sent_messages.append(msg)

    async def fake_finish(msg: Any) -> None:
        finished_messages.append(msg)
        raise main._CommandFinished

    live_resolve_called: list[str] = []

    async def fake_live_resolve(fetcher, query, teams=None):
        live_resolve_called.append(query)
        return main.names.TeamResolution("none")

    mock_fetcher = SimpleNamespace(
        get_html=AsyncMock(return_value="<html><div class='profile-team-name'>FaZe</div></html>"),
    )

    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main.cs2, "send", fake_send)
    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main.names, "resolve_team", fake_live_resolve)
    monkeypatch.setattr(main, "fetcher", mock_fetcher, raising=False)
    monkeypatch.setattr(
        main.store,
        "all_index_teams",
        lambda: [{"team_id": "6667", "name": "FaZe", "team_key": "faze", "rank": 6}],
    )
    monkeypatch.setattr(
        main.card,
        "render_team_profile_card",
        AsyncMock(return_value=b"fake-team-card"),
    )

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_team_detail("法则"))

    # 验证走本地 SQLite 优先命中：
    # 1. 0ms 命中本地名录，直接下发提示语
    assert any("正在查询 FaZe 的阵容与排名" in m for m in sent_messages)
    # 2. 阻断了在线 live 搜索
    assert live_resolve_called == []
    # 3. 正常完成出图
    assert finished_messages[-1] == b"fake-team-card"


def test_handle_team_detail_fallbacks_to_live_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[str] = []
    finished_messages: list[Any] = []

    async def fake_send(msg: str) -> None:
        sent_messages.append(msg)

    async def fake_finish(msg: Any) -> None:
        finished_messages.append(msg)
        raise main._CommandFinished

    live_resolve_called: list[str] = []

    async def fake_live_resolve(fetcher, query, teams=None):
        live_resolve_called.append(query)
        return main.names.TeamResolution(
            "ok",
            team=main.hltv.SearchTeam("6667", "FaZe"),
        )

    mock_fetcher = SimpleNamespace(
        get_html=AsyncMock(return_value="<html><div class='profile-team-name'>FaZe</div></html>"),
    )

    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main.cs2, "send", fake_send)
    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main.names, "resolve_team", fake_live_resolve)
    monkeypatch.setattr(main, "fetcher", mock_fetcher, raising=False)
    monkeypatch.setattr(main.store, "all_index_teams", lambda: [])
    monkeypatch.setattr(
        main.card,
        "render_team_profile_card",
        AsyncMock(return_value=b"fake-team-card"),
    )

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_team_detail("FaZe"))

    # 本地未命中时成功 fallback 在线搜索
    assert live_resolve_called == ["FaZe"]
    assert any("正在查询 FaZe 的阵容与排名" in m for m in sent_messages)
    assert finished_messages[-1] == b"fake-team-card"


def test_recent_finished_event_id_picks_latest_whitelist_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(
        main.store,
        "whitelist_view",
        lambda: {
            "8932": {
                "name": "Thunderpick World Championship 2026",
                "slug": "thunderpick-world-championship-2026",
            }
        },
    )
    monkeypatch.setattr(main.store, "cache_get", lambda _url, _age: "<html>results</html>")
    monkeypatch.setattr(
        main.hltv,
        "parse_results",
        lambda _html: [
            main.hltv.ScheduledMatch(
                "1",
                None,
                "CCT Europe",
                None,
                "A",
                "B",
                1000,
                "bo3",
                url="https://www.hltv.org/matches/1/a-vs-b-cct",
            ),
            main.hltv.ScheduledMatch(
                "2",
                None,
                "Thunderpick World Championship 2026",
                None,
                "FURIA",
                "BETBOOM",
                900,
                "bo5",
                url="https://www.hltv.org/matches/2/furia-vs-betboom-thunderpick-world-championship-2026",
            ),
        ],
    )

    assert asyncio.run(main._recent_finished_event_id()) == "8932"


def test_llm_tools_accept_extra_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    run_cmd = AsyncMock(return_value="ok")
    monkeypatch.setattr(plugin, "_run_llm_tool_command", run_cmd)
    event = SimpleNamespace()

    assert asyncio.run(plugin.query_cs2_events(event, reason="check events")) == "ok"
    assert asyncio.run(plugin.query_cs2_schedule(event, reason="check schedule")) == "ok"
    assert (
        asyncio.run(
            plugin.query_cs2_bracket(event, event_name="IEM", reason="check bracket")
        )
        == "ok"
    )
    assert asyncio.run(plugin.query_cs2_news(event, reason="check news")) == "ok"
    assert (
        asyncio.run(
            plugin.query_cs2_match_status(event, team="FaZe", reason="check match")
        )
        == "ok"
    )
    assert (
        asyncio.run(
            plugin.query_cs2_ranking(event, source="vrs", reason="check ranking")
        )
        == "ok"
    )
    assert (
        asyncio.run(
            plugin.query_cs2_h2h(
                event, team1="FaZe", team2="Vitality", reason="check h2h"
            )
        )
        == "ok"
    )
    assert run_cmd.await_count == 7


def test_alert_skips_non_numeric_admin_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, object]] = []

    async def send_message(origin: str, chain: object) -> None:
        sent.append((origin, chain))

    monkeypatch.setattr(
        main,
        "bot_context",
        SimpleNamespace(send_message=send_message),
        raising=False,
    )
    monkeypatch.setattr(main, "admin_ids", {"astrbot", "10001"}, raising=False)
    monkeypatch.setattr(
        main,
        "user_origin_resolver",
        lambda uid: f"onebot:FriendMessage:{uid}" if uid == 10001 else None,
        raising=False,
    )

    asyncio.run(main._alert("测试告警"))
    assert len(sent) == 1
    assert sent[0][0] == "onebot:FriendMessage:10001"
