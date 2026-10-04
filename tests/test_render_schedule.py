from __future__ import annotations

from datetime import datetime, timedelta, timezone

from astrbot_plugin_cs2_results import render
from astrbot_plugin_cs2_results.hltv import (
    EventSchedule,
    Matchup,
    PlayerProfile,
    RecentMatch,
    ScheduledMatch,
    SlotTeam,
    SwissCell,
    SwissColumn,
    SwissStage,
    TeamProfile,
    TeamRosterPlayer,
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


def test_player_profile_card_contains_stats_roles_and_major_history() -> None:
    profile = PlayerProfile(
        player_id="7998",
        nick="s1mple",
        realname="Oleksandr Kostyliev",
        country="Ukraine",
        team="BC.Game",
        rating=1.22,
        kd=1.24,
        kd_scope="生涯统计",
        maps=29,
        role_stats=[("Firepower", 85), ("Sniping", 89)],
        major_wins=1,
        major_mvps=1,
        recent_matches=[
            RecentMatch("Sashi", "13 : 11", "win", "ROG JOURNEY", rating=1.31)
        ],
    )

    html = render.build_player_profile_html(profile, "2026-10-05 12:00")

    assert "s1mple" in html
    assert "1.22" in html
    assert "1.24" in html
    assert "生涯统计" in html
    assert "Firepower" in html
    assert "1 次 Major 冠军" in html
    assert "Sashi" in html


def test_team_profile_card_contains_rankings_roster_and_results() -> None:
    profile = TeamProfile(
        team_id="4608",
        name="Natus Vincere",
        country="Europe",
        world_rank=10,
        regional_rank=8,
        region="Europe",
        vrs_rank=20,
        roster=[TeamRosterPlayer("18987", "b1t", "STARTER", 1.12)],
        recent_results=[
            RecentMatch("Vitality", "1 : 2", "loss", "ESL Pro League Season 24")
        ],
    )

    html = render.build_team_profile_html(profile, "2026-10-05 12:00")

    assert "Natus Vincere" in html
    assert "#10" in html
    assert "#20" in html
    assert "b1t" in html
    assert "Vitality" in html
