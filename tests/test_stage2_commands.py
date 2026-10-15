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
    bp_calls: list[str | None] = []
    mode_calls: list[str] = []
    mute_calls: list[str] = []
    morning_calls: list[str] = []
    settings_calls: list[int] = []

    async def fake_h2h(t1: str, t2: str) -> None:
        h2h_calls.append((t1, t2))
        raise main._CommandFinished

    async def fake_bp(arg: str | None) -> None:
        bp_calls.append(arg)
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
    monkeypatch.setattr(main, "_handle_bp", fake_bp)
    monkeypatch.setattr(main, "_handle_group_mode", fake_mode)
    monkeypatch.setattr(main, "_handle_group_mute", fake_mute)
    monkeypatch.setattr(main, "_handle_group_morning_report", fake_morning)
    monkeypatch.setattr(main, "_handle_group_settings", fake_settings)

    test_cases = [
        ("交手 Vitality Spirit", h2h_calls, [("Vitality", "Spirit")]),
        ("h2h FaZe vs NaVi", h2h_calls, [("Vitality", "Spirit"), ("FaZe", "NaVi")]),
        ("对阵历史 MOUZ vs Astralis", h2h_calls, [("Vitality", "Spirit"), ("FaZe", "NaVi"), ("MOUZ", "Astralis")]),
        ("bp", bp_calls, [None]),
        ("bp Vitality", bp_calls, [None, "Vitality"]),
        ("选图 2395449", bp_calls, [None, "Vitality", "2395449"]),
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
