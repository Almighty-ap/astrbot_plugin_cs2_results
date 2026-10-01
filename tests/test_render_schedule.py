from __future__ import annotations

from datetime import datetime, timedelta, timezone

from astrbot_plugin_cs2_results import render
from astrbot_plugin_cs2_results.hltv import (
    EventSchedule,
    Matchup,
    SlotTeam,
    SwissCell,
    SwissColumn,
    SwissStage,
)


def test_upcoming_swiss_matchup_renders_beijing_time_bo_and_status() -> None:
    cn = timezone(timedelta(hours=8))
    start_ms = int(datetime(2026, 10, 3, 18, 0, tzinfo=cn).timestamp() * 1000)
    matchup = Matchup(
        "2398717",
        "",
        start_ms,
        3,
        SlotTeam("Falcons"),
        SlotTeam("TYLOO"),
    )
    stage = SwissStage(
        columns=[
            SwissColumn(
                status="active",
                cells=[SwissCell(record="0:0", kind="normal", matchups=[matchup])],
            )
        ]
    )
    schedule = EventSchedule(
        event_id="8244",
        name="ESL Pro League Season 24",
        logo=None,
        date_text="Oct 3rd - Oct 11th 2026",
        prize="$1,000,000",
        location="Katowice, Poland",
        status="Upcoming",
        swiss=stage,
    )

    html = render.build_event_schedule_html(schedule, "DEBUG")

    assert "第 1 轮 · 即将开始" in html
    assert "10月3日 周六 18:00" in html
    assert "BO3" in html
    assert "第 1 轮 · 进行中" not in html
