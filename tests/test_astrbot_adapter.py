from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
