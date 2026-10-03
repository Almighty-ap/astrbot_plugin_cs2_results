from __future__ import annotations

from datetime import datetime, timedelta, timezone

from astrbot_plugin_cs2_results import render
from astrbot_plugin_cs2_results.hltv import (
    EventSchedule,
    Matchup,
    ScheduledMatch,
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
    assert "Falcons" in html
    assert "TYLOO" in html
    assert "第 1 轮 · 进行中" not in html


def test_help_footer_uses_current_repository_without_prefix_duplicate() -> None:
    html = render.build_help_html(False, "2026-10-02 05:14")

    assert "github.com/Almighty-ap/astrbot_plugin_cs2_results" in html
    assert "github.com/canxiaocai/cs2-event-bot" not in html
    assert "或 cs2 均可触发" not in html


def test_previous_match_day_recap_renders_above_current_events() -> None:
    recap = ScheduledMatch(
        "1",
        "8244",
        "Previous Event",
        None,
        "Falcons",
        "TYLOO",
        1_783_524_000_000,
        "bo3",
        status="finished",
        score1=2,
        score2=0,
        winner="team1",
    )
    current = ScheduledMatch(
        "2",
        "8244",
        "Current Event",
        None,
        "Vitality",
        "Spirit",
        1_783_612_800_000,
        "bo3",
        status="upcoming",
    )

    html = render.build_schedule_html(
        [current],
        "DEBUG",
        title="下个比赛日",
        recap=[recap],
        recap_label="上个比赛日 · 7月24日 周五 20:04 — 次日 05:34",
    )

    assert html.index("上个比赛日") < html.index("Current Event")
    assert "Falcons" in html
    assert "Current Event" in html
