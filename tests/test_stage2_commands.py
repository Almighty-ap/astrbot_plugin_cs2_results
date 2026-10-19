from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_cs2_results import main, store


def test_stage2_command_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    event = SimpleNamespace(
        is_group=True,
        group_id=12345,
        user_id="10001",
    )

    h2h_calls: list[tuple[str, str]] = []
    mode_calls: list[str] = []
    mute_calls: list[str] = []
    morning_calls: list[str] = []
    settings_calls: list[int] = []

    async def fake_h2h(t1: str, t2: str) -> None:
        h2h_calls.append((t1, t2))
        raise main._CommandFinished

    async def fake_mode(ev: Any, mode: str) -> None:
        mode_calls.append(mode)
        raise main._CommandFinished

    async def fake_mute(ev: Any, mute: str) -> None:
        mute_calls.append(mute)
        raise main._CommandFinished

    async def fake_morning(ev: Any, rep: str) -> None:
        morning_calls.append(rep)
        raise main._CommandFinished

    async def fake_settings(ev: Any) -> None:
        settings_calls.append(ev.group_id)
        raise main._CommandFinished

    monkeypatch.setattr(main, "_cooldown_left", lambda _event: 0)
    monkeypatch.setattr(main, "_admin_ctx", lambda _event: False)
    monkeypatch.setattr(main, "_handle_h2h", fake_h2h)
    monkeypatch.setattr(main, "_handle_group_mode", fake_mode)
    monkeypatch.setattr(main, "_handle_group_mute", fake_mute)
    monkeypatch.setattr(main, "_handle_group_morning_report", fake_morning)
    monkeypatch.setattr(main, "_handle_group_settings", fake_settings)

    test_cases = [
        ("交手 Vitality Spirit", h2h_calls, [("Vitality", "Spirit")]),
        ("h2h FaZe vs NaVi", h2h_calls, [("Vitality", "Spirit"), ("FaZe", "NaVi")]),
        ("对阵历史 MOUZ vs Astralis", h2h_calls, [("Vitality", "Spirit"), ("FaZe", "NaVi"), ("MOUZ", "Astralis")]),
        ("模式 精简", mode_calls, ["精简"]),
        ("mode home", mode_calls, ["精简", "home"]),
        ("静音 23:00-08:00", mute_calls, ["23:00-08:00"]),
        ("免打扰 开启", mute_calls, ["23:00-08:00", "开启"]),
        ("晨报 开启", morning_calls, ["开启"]),
        ("morning 关闭", morning_calls, ["开启", "关闭"]),
        ("设置", settings_calls, [12345]),
        ("本群设置", settings_calls, [12345, 12345]),
    ]

    for raw, target_list, expected in test_cases:
        with pytest.raises(main._CommandFinished):
            asyncio.run(main.handle_cs2(event, raw))
        assert target_list == expected


def test_group_settings_handlers(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_stage2.db"
    monkeypatch.setattr(store, "_DB", db_file)
    store._drop_thread_conn()
    store._initialize_database()

    group_id = 99999
    store.subscribe(group_id)

    event = SimpleNamespace(
        is_group=True,
        group_id=group_id,
        user_id="10001",
        sender_role="admin",
    )

    finished_msgs: list[str] = []

    async def fake_finish(msg: Any) -> None:
        finished_msgs.append(str(msg))
        raise main._CommandFinished

    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main, "_can_manage_sub", lambda _ev: True)

    # 1. 模式
    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_mode(event, "精简"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["mode"] == "concise"
    assert "精简模式" in finished_msgs[-1]

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_mode(event, "主队"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["mode"] == "home"

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_mode(event, "全量"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["mode"] == "full"

    # 2. 静音
    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_mute(event, "22:30-07:30"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None
    assert sub["mute_enabled"] == 1
    assert sub["mute_start"] == "22:30"
    assert sub["mute_end"] == "07:30"

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_mute(event, "关闭"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["mute_enabled"] == 0

    # 3. 晨报
    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_morning_report(event, "关闭"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["morning_report"] == 0

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_morning_report(event, "开启"))
    sub = store.get_group_subscription(group_id)
    assert sub is not None and sub["morning_report"] == 1

    # 4. 设置概况
    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_group_settings(event))
    last_msg = finished_msgs[-1]
    assert "本群 CS2 订阅设置概况" in last_msg
    assert "全量模式" in last_msg
    assert "每日晨报: 已开启" in last_msg


def test_resolve_recipients_with_stage2_rules(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_recipients.db"
    monkeypatch.setattr(store, "_DB", db_file)
    store._drop_thread_conn()
    store._initialize_database()

    # Group 1: full mode, not muted
    g1 = 101
    store.subscribe(g1)
    store.set_group_subscription_mode(g1, "full")

    # Group 2: home mode, no targets yet
    g2 = 102
    store.subscribe(g2)
    store.set_group_subscription_mode(g2, "home")

    # Group 3: muted now
    g3 = 103
    store.subscribe(g3)
    store.set_group_mute(g3, True, "00:00", "23:59")

    # Group 4: home mode with matching team target
    g4 = 104
    store.subscribe(g4)
    store.set_group_subscription_mode(g4, "home")
    store.add_target(g4, 2001, "team", "vitality", "Vitality")

    monkeypatch.setattr(main, "target_groups", lambda: {g1, g2, g3, g4})
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(main, "_top_tier", lambda _eid: True)

    recipients = main.resolve_recipients("1", "Vitality", "FaZe", set())
    # g1 is full (included)
    # g2 is home but no targets for Vitality or FaZe (excluded)
    # g3 is muted (excluded)
    # g4 is home and has target Vitality (included)
    assert g1 in recipients
    assert g2 not in recipients
    assert g3 not in recipients
    assert g4 in recipients


def test_push_map_concise_mode_filtering(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_push_map.db"
    monkeypatch.setattr(store, "_DB", db_file)
    store._drop_thread_conn()
    store._initialize_database()

    g_full = 201
    g_concise = 202
    store.subscribe(g_full)
    store.subscribe(g_concise)
    store.set_group_subscription_mode(g_full, "full")
    store.set_group_subscription_mode(g_concise, "concise")

    monkeypatch.setattr(main, "target_groups", lambda: {g_full, g_concise})
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)

    # 1. Non-final map (map 1 of bo3, score 1-0)
    map1 = SimpleNamespace(
        finished=True,
        map_name="Mirage",
        team1="Vitality",
        team2="FaZe",
        score1=13,
        score2=9,
        team1_players=[],
        team2_players=[],
        first_half1=8,
        first_half2=4,
        second_half1=5,
        second_half2=5,
        ot1=0,
        ot2=0,
        key=lambda: "map1",
    )
    match_mid = SimpleNamespace(
        match_id="9999",
        best_of=3,
        series_over=False,
        series1=1,
        series2=0,
        team1="Vitality",
        team2="FaZe",
        event_name="Major",
        event_id="1",
        maps=[map1],
        url="https://www.hltv.org/matches/9999/x",
        logo_urls=lambda: [],
        vrs=None,
    )

    monkeypatch.setattr(main, "_top_tier", lambda _eid: True)
    monkeypatch.setattr(main, "ensure_logos", AsyncMock())
    monkeypatch.setattr(main, "note_match_vrs", lambda _m: None)
    monkeypatch.setattr(main.card, "render_map_card", AsyncMock(return_value=b"fake_png"))

    prepared: list[tuple[str, str, list[int]]] = []

    def fake_prepare(mid: str, mkey: str, gids: list[int], mentions: Any = None) -> None:
        prepared.append((mid, mkey, list(gids)))

    monkeypatch.setattr(store, "prepare_deliveries", fake_prepare)
    monkeypatch.setattr(store, "set_delivery_payload", lambda *a, **k: None)

    recips = {g_full: set(), g_concise: set()}
    asyncio.run(main._push_map(match_mid, 0, recips))

    assert len(prepared) == 1
    _p_mid, _p_mkey, p_groups = prepared[0]
    assert g_full in p_groups
    assert g_concise not in p_groups  # concise skipped for non-final map

    prepared.clear()

    # 2. Final map (map 2 of bo3, score 2-0, series_over=True)
    match_final = SimpleNamespace(
        match_id="9999",
        best_of=3,
        series_over=True,
        series1=2,
        series2=0,
        team1="Vitality",
        team2="FaZe",
        event_name="Major",
        event_id="1",
        maps=[map1, map1],
        url="https://www.hltv.org/matches/9999/x",
        logo_urls=lambda: [],
        vrs=None,
    )

    asyncio.run(main._push_map(match_final, 1, recips))

    assert len(prepared) == 1
    _, _, p_groups_final = prepared[0]
    assert g_full in p_groups_final
    assert g_concise in p_groups_final  # concise included for final map


def test_handle_test_morning_report(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_msgs: list[str] = []
    finished_cards: list[Any] = []

    async def fake_send(msg: str) -> None:
        sent_msgs.append(msg)

    async def fake_finish(png: Any) -> None:
        finished_cards.append(png)
        raise main._CommandFinished

    monkeypatch.setattr(main.cs2, "send", fake_send)
    monkeypatch.setattr(main.cs2, "finish", fake_finish)
    monkeypatch.setattr(main, "_render_morning_report_card_data", AsyncMock(return_value=b"fake_morning_png"))

    with pytest.raises(main._CommandFinished):
        asyncio.run(main._handle_test("晨报"))

    assert len(sent_msgs) == 1
    assert "正在生成每日晨报测试卡片" in sent_msgs[0]
    assert finished_cards == [b"fake_morning_png"]


def test_is_top_tier_match_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = main.Config()
    cfg.cs2_force_exclude_events = ["9003"]
    monkeypatch.setattr(main, "cfg", cfg, raising=False)
    monkeypatch.setattr(store, "whitelist_event_ids", lambda: {"9002", "9003"})
    monkeypatch.setattr(
        store,
        "whitelist_view",
        lambda: {
            "9002": {"name": "Thunderpick World Championship 2026", "slug": "thunderpick-world-championship-2026"},
            "9003": {"name": "Major 2026", "slug": "major-2026"},
        },
    )

    # 1. Direct event_id in whitelist
    m1 = main.hltv.ScheduledMatch("1", "9002", "Some Event", None, "A", "B", 1000, "bo3")
    assert main._is_top_tier_match(m1) is True

    # 2. Direct event_id excluded
    m2 = main.hltv.ScheduledMatch("2", "9003", "Major 2026", None, "A", "B", 1000, "bo3")
    assert main._is_top_tier_match(m2) is False

    # 3. No event_id, match by slug in url
    m3 = main.hltv.ScheduledMatch(
        "3",
        None,
        "Thunderpick WC",
        None,
        "A",
        "B",
        1000,
        "bo3",
        url="https://www.hltv.org/matches/123/a-vs-b-thunderpick-world-championship-2026",
    )
    assert main._is_top_tier_match(m3) is True
    assert m3.event_id == "9002"

    # 4. No event_id, match by event_name
    m4 = main.hltv.ScheduledMatch(
        "4",
        None,
        "Thunderpick World Championship 2026",
        None,
        "A",
        "B",
        1000,
        "bo3",
        url="https://www.hltv.org/matches/124/a-vs-b",
    )
    assert main._is_top_tier_match(m4) is True
    assert m4.event_id == "9002"

    # 5. Unknown event
    m5 = main.hltv.ScheduledMatch(
        "5",
        None,
        "Local Cup",
        None,
        "A",
        "B",
        1000,
        "bo3",
        url="https://www.hltv.org/matches/125/a-vs-b-local-cup",
    )
    assert main._is_top_tier_match(m5) is False


def test_render_morning_report_card_data_overnight_and_next_day(monkeypatch: pytest.MonkeyPatch) -> None:
    now_dt = main.datetime(2026, 10, 16, 8, 30, tzinfo=main.CN)

    class MockDateTime(main.datetime):
        @classmethod
        def now(cls, tz=None):
            return now_dt

    monkeypatch.setattr(main, "datetime", MockDateTime)
    monkeypatch.setattr(main, "cfg", main.Config(), raising=False)
    monkeypatch.setattr(store, "whitelist_event_ids", lambda: {"9002"})
    monkeypatch.setattr(
        store,
        "whitelist_view",
        lambda: {
            "9002": {"name": "Thunderpick World Championship 2026", "slug": "thunderpick-world-championship-2026"},
        },
    )
    mock_fetcher = SimpleNamespace(
        get_html=AsyncMock(return_value="<html>mock</html>"),
        get_logos=AsyncMock(),
    )
    monkeypatch.setattr(main, "fetcher", mock_fetcher, raising=False)

    now_ts = now_dt.timestamp() * 1000

    # Finished match yesterday night (22:00 Oct 15) and this early morning (02:00 Oct 16)
    m_night = main.hltv.ScheduledMatch(
        "1",
        None,
        "Thunderpick World Championship 2026",
        None,
        "MOUZ",
        "NAVI",
        int(now_ts - 10 * 3600 * 1000),  # 10h ago: 22:30 Oct 15
        "bo3",
        status="finished",
        score1=2,
        score2=1,
        winner="team1",
        url="https://www.hltv.org/matches/1/mouz-vs-navi-thunderpick-world-championship-2026",
    )
    m_morning = main.hltv.ScheduledMatch(
        "2",
        None,
        "Thunderpick World Championship 2026",
        None,
        "FaZe",
        "Vitality",
        int(now_ts - 6 * 3600 * 1000),  # 6h ago: 02:30 Oct 16
        "bo3",
        status="finished",
        score1=1,
        score2=2,
        winner="team2",
        url="https://www.hltv.org/matches/2/faze-vs-vitality-thunderpick-world-championship-2026",
    )
    # Old finished match (30 hours ago, should be excluded)
    m_old = main.hltv.ScheduledMatch(
        "3",
        None,
        "Thunderpick World Championship 2026",
        None,
        "Astralis",
        "Heroic",
        int(now_ts - 30 * 3600 * 1000),
        "bo3",
        status="finished",
        url="https://www.hltv.org/matches/3/astralis-vs-heroic-thunderpick-world-championship-2026",
    )

    # Upcoming match tonight (22:00 Oct 16)
    m_today = main.hltv.ScheduledMatch(
        "4",
        "9002",
        "Thunderpick World Championship 2026",
        None,
        "Aurora",
        "9z",
        int(now_ts + 13.5 * 3600 * 1000),  # 22:00 Oct 16
        "bo3",
        status="upcoming",
    )
    # Upcoming match tomorrow early morning (01:30 Oct 17 - 第二天凌晨)
    m_next_early = main.hltv.ScheduledMatch(
        "5",
        "9002",
        "Thunderpick World Championship 2026",
        None,
        "G2",
        "Liquid",
        int(now_ts + 17 * 3600 * 1000),  # 01:30 Oct 17
        "bo3",
        status="upcoming",
    )
    # Upcoming match tomorrow afternoon (15:00 Oct 17, should be excluded from today's focus)
    m_next_afternoon = main.hltv.ScheduledMatch(
        "6",
        "9002",
        "Thunderpick World Championship 2026",
        None,
        "Spirit",
        "Virtus.pro",
        int(now_ts + 30.5 * 3600 * 1000),  # 15:00 Oct 17
        "bo3",
        status="upcoming",
    )

    monkeypatch.setattr(main.hltv, "parse_results", lambda _html: [m_old, m_night, m_morning])
    monkeypatch.setattr(main.hltv, "parse_upcoming_matches", lambda _html: [m_today, m_next_early, m_next_afternoon])
    monkeypatch.setattr(main.hltv, "parse_live_matches", lambda _html: [])

    rendered_args: dict[str, Any] = {}

    async def fake_render(date_disp: str, y_matches: list[Any], t_matches: list[Any], when: str) -> bytes:
        rendered_args["date_disp"] = date_disp
        rendered_args["y_matches"] = y_matches
        rendered_args["t_matches"] = t_matches
        rendered_args["when"] = when
        return b"fake_png_report"

    monkeypatch.setattr(main.card, "render_morning_report_card", fake_render)

    res = asyncio.run(main._render_morning_report_card_data())
    assert res == b"fake_png_report"

    # Verify overnight results include yesterday night and this morning (m_old excluded)
    assert rendered_args["y_matches"] == [m_night, m_morning]

    # Verify today's matches include tonight AND next-day early morning (m_next_afternoon excluded)
    assert rendered_args["t_matches"] == [m_today, m_next_early]

