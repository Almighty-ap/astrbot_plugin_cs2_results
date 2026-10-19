from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from astrbot_plugin_cs2_results import render
from astrbot_plugin_cs2_results.hltv import (
    Bracket,
    BracketRound,
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
        top20_text="#1 ('21), #2 ('22), #3 ('23)",
        recent_matches=[
            RecentMatch("Sashi", "13 : 11", "win", "ROG JOURNEY", rating=1.31),
            RecentMatch("BBL", "1 : 2", "loss", "ROG JOURNEY"),
        ],
    )

    html = render.build_player_profile_html(
        profile, "2026-10-05 12:00", b"\x89PNG\r\n\x1a\navatar"
    )

    assert "s1mple" in html
    assert "1.22" in html
    assert "1.24" in html
    assert "生涯统计" in html
    assert "Firepower" in html
    assert "1 次 Major 冠军" in html
    assert "Sashi" in html
    assert "13 : 11" in html
    assert "1.31" in html
    assert html.count("Top 20") == 3
    assert "data:image/png;base64" in html


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
    assert "1 : 2" in html
    assert "最近 5 场 · 比分" in html
    assert "width:100%;max-width:1200px" in html
    assert 'style="width:1200px;' not in html


def test_build_ranking_html_renders_hltv_and_vrs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render.store, "logo_data_uri", lambda u: f"data:image/png;base64,{u}" if u else None)
    hltv_teams = [
        {"rank": 1, "name": "Spirit", "team_key": "spirit", "team_id": "7020", "points": None, "region": "", "players": ["donk", "sh1ro"], "logo_url": "https://img-cdn.hltv.org/spirit.png", "rank_change": "+2", "country": "Russia"},
        {"rank": 2, "name": "Vitality", "team_key": "vitality", "team_id": "9565", "points": None, "region": "", "players": ["ZywOo", "apEX"], "logo_url": "https://img-cdn.hltv.org/vitality.png", "rank_change": "-1", "country": "France"},
    ]
    html_hltv = render.build_ranking_html(hltv_teams, {"source": "hltv", "total_teams": 2}, "2026-10-12 12:00")
    assert "HLTV 战队世界排名" in html_hltv
    assert "Spirit" in html_hltv
    assert "Vitality" in html_hltv
    assert "donk" in html_hltv
    assert "Russia" in html_hltv
    assert "France" in html_hltv
    assert "↑2" in html_hltv
    assert "↓1" in html_hltv
    assert "https://img-cdn.hltv.org/spirit.png" in html_hltv
    assert "零网络" not in html_hltv
    assert "本地数据名录" not in html_hltv
    assert "hltv.org" in html_hltv

    vrs_teams = [
        {"rank": 1, "name": "Vitality", "team_key": "vitality", "team_id": "9565", "points": 1977, "region": "EU", "players": ["ZywOo", "apEX"]},
        {"rank": 2, "name": "MOUZ", "team_key": "mouz", "team_id": "4494", "points": 1946, "region": "EU", "players": ["torzsi", "Spinx"]},
    ]
    html_vrs = render.build_ranking_html(vrs_teams, {"source": "vrs", "total_teams": 2, "snapshot": "July 23rd, 2026"}, "2026-10-12 12:00")
    assert "Valve VRS 世界排名" in html_vrs
    assert "1,977 pts" in html_vrs
    assert "EU" in html_vrs
    assert "Regional Standings" in html_vrs
    assert "零网络" not in html_vrs

    html_empty = render.build_ranking_html([], {"source": "hltv", "total_teams": 0}, "2026-10-12 12:00")
    assert "暂无本地排名数据" in html_empty


def _sample_mu(m_id: str, t1: str, t2: str) -> Matchup:
    return Matchup(m_id, "", 1_792_000_000_000, 3, SlotTeam(t1), SlotTeam(t2))


def test_gsl_group_brackets_widen_card_and_pair_lower_finals() -> None:
    def make_gsl_group(name: str) -> Bracket:
        return Bracket(
            kind="double",
            name=name,
            upper=[
                BracketRound("胜者组首轮", [_sample_mu("1", "Falcons", "NIP"), _sample_mu("2", "9z", "Aurora")]),
                BracketRound("胜者组决赛", [_sample_mu("3", "Falcons", "9z")]),
            ],
            lower=[
                BracketRound("败者组第一轮", [_sample_mu("4", "NIP", "Aurora")]),
            ],
            finals=[
                BracketRound("季军赛", [_sample_mu("5", "9z", "Aurora")]),
            ],
        )

    playoffs = Bracket(
        kind="single",
        name="Single Elimination Bracket",
        upper=[
            BracketRound("半决赛", [_sample_mu("6", "Falcons", "BETBOOM"), _sample_mu("7", "FURIA", "Aurora")]),
            BracketRound("总决赛", [_sample_mu("8", "BETBOOM", "FURIA")]),
        ],
    )
    schedule = EventSchedule(
        event_id="9100",
        name="Thunderpick World Championship 2026",
        logo=None,
        date_text="Oct 14th - Oct 18th 2026",
        prize="$1,000,000",
        location="Malta",
        status="Finished",
        brackets=[playoffs, make_gsl_group("Group A"), make_gsl_group("Group B")],
    )

    html = render.build_event_schedule_html(schedule, "2026-10-19 09:26")

    assert "width:1080px;min-width:1080px;" in html
    assert "repeat(2,minmax(434px,1fr))" in html
    assert "repeat(2,minmax(0,1fr))" not in html
    assert "败者组 Lower Bracket" in html
    assert "决赛阶段 Finals" in html


def test_deep_group_brackets_stack_vertically() -> None:
    def make_deep_group(name: str) -> Bracket:
        return Bracket(
            kind="double",
            name=name,
            upper=[
                BracketRound("胜者组八强", [_sample_mu("1", "Vitality", "Astralis")]),
                BracketRound("胜者组半决赛", [_sample_mu("2", "Vitality", "MOUZ")]),
                BracketRound("胜者组决赛", [_sample_mu("3", "Vitality", "Spirit")]),
            ],
            lower=[
                BracketRound("败者组第一轮", [_sample_mu("4", "Astralis", "FaZe")]),
                BracketRound("败者组半决赛", [_sample_mu("5", "MOUZ", "FaZe")]),
            ],
            finals=[
                BracketRound("败者组决赛", [_sample_mu("6", "Spirit", "FaZe")]),
            ],
        )

    schedule = EventSchedule(
        event_id="9101",
        name="IEM Katowice",
        logo=None,
        date_text="Feb 1st - Feb 9th",
        prize="$1,000,000",
        location="Poland",
        status="Ongoing",
        brackets=[make_deep_group("Group A"), make_deep_group("Group B")],
    )

    html = render.build_event_schedule_html(schedule, "DEBUG")

    assert "repeat(2,minmax(" not in html
    assert "width:786px;min-width:786px;" in html

