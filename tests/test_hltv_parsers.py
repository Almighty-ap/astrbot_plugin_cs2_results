from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

BOT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")


def _load_hltv_module() -> ModuleType:
    """Load the pure parser without importing cs2_results/__init__.py.

    Importing the package registers NoneBot handlers and scheduler jobs. Parser
    unit tests intentionally avoid those process-level side effects.
    """
    path = BOT_ROOT / "hltv.py"
    spec = importlib.util.spec_from_file_location("cs2_hltv_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load parser module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class HltvParserHappyPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hltv = _load_hltv_module()

    def test_events_and_whitelist(self) -> None:
        html = _fixture("events_normal.html")

        self.assertEqual(self.hltv.featured_event_ids(html), {"9001"})
        refs = {event.id: event for event in self.hltv.parse_whitelist(html)}
        self.assertEqual(set(refs), {"9001", "9002"})
        self.assertEqual(refs["9001"].name, "Featured Cup")

        events = self.hltv.parse_events(html)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.id, "9002")
        self.assertEqual(event.name, "Major 2026")
        self.assertEqual(event.start_unix, 1_784_563_200_000)
        self.assertEqual(event.end_unix, 1_784_995_200_000)
        self.assertEqual(event.teams, "16")
        self.assertEqual(event.prize, "$1,000,000")
        self.assertEqual(event.location, "Shanghai, China")

    def test_live_and_upcoming_matches(self) -> None:
        html = _fixture("matches_normal.html")
        with patch.object(self.hltv.time, "time", return_value=1_700_000_000):
            live = self.hltv.parse_live_matches(html)
            upcoming = self.hltv.parse_upcoming_matches(html)

        self.assertEqual([match.match_id for match in live], ["1001"])
        self.assertEqual(live[0].event_id, "9002")
        self.assertEqual(live[0].team1, "Alpha")
        self.assertEqual(live[0].team1_logo, "https://cdn.example/teamlogo/alpha-day.png")

        self.assertEqual([match.match_id for match in upcoming], ["1002"])
        self.assertEqual(upcoming[0].best_of, "bo1")
        self.assertEqual(upcoming[0].start_unix, 4_102_444_800_000)
        self.assertEqual(upcoming[0].event_logo, "https://cdn.example/eventlogo/major.png")

    def test_results(self) -> None:
        results = self.hltv.parse_results(_fixture("results_normal.html"))

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.match_id, "999")
        self.assertEqual(result.status, "finished")
        self.assertEqual((result.score1, result.score2), (2, 1))
        self.assertEqual(result.winner, "team1")
        self.assertEqual(result.team2_logo, "https://cdn.example/teamlogo/beta.png")

    def test_match_detail_and_player_stats(self) -> None:
        match = self.hltv.parse_match(
            _fixture("match_detail_normal.html"),
            "https://www.hltv.org/matches/4242/alpha-vs-beta",
        )

        self.assertEqual(match.match_id, "4242")
        self.assertEqual(match.event_id, "9002")
        self.assertEqual(match.event_name, "Major 2026")
        self.assertEqual(match.best_of, 3)
        self.assertTrue(match.is_lan)
        self.assertEqual(match.stage, "Grand Final")
        self.assertEqual((match.team1, match.team2), ("Alpha", "Beta"))
        self.assertEqual(len(match.maps), 1)

        played_map = match.maps[0]
        self.assertTrue(played_map.finished)
        self.assertEqual(played_map.mapstatsid, "777")
        self.assertEqual(played_map.picked_by, "team1")
        self.assertEqual((played_map.team1_score, played_map.team2_score), (13, 8))
        self.assertEqual(played_map.team1_players[0].kd, "20-12")
        self.assertAlmostEqual(played_map.team1_players[0].rating, 1.35)

    def test_vrs_forecast_three_columns_before_the_series_ends(self) -> None:
        match = self.hltv.parse_match(
            _fixture("match_detail_normal.html"),
            "https://www.hltv.org/matches/4242/alpha-vs-beta",
        )

        vrs = match.vrs
        self.assertIsNotNone(vrs)
        self.assertFalse(vrs.settled)  # 赛前:三档预测
        alpha, beta = vrs.pair(match.team1, match.team2)
        self.assertEqual((alpha.name, beta.name), ("Alpha", "Beta"))
        self.assertEqual((alpha.current.points, alpha.current.rank), ("1238pt", 49))
        self.assertFalse(alpha.current.signed)
        self.assertEqual((alpha.win.points, alpha.win.rank, alpha.win.rank_delta), ("+32pt", 44, 5))
        self.assertEqual(alpha.win.trend, "rising")
        self.assertTrue(alpha.win.signed)
        self.assertEqual(
            (alpha.lose.points, alpha.lose.rank, alpha.lose.rank_delta), ("-2pt", 50, -1)
        )
        # 名次不变时 HLTV 不给变化数字
        self.assertEqual((beta.win.points, beta.win.rank, beta.win.rank_delta), ("+2pt", 9, None))
        self.assertEqual(beta.lose.points, "-30pt")

    def test_vrs_result_panel_after_the_series_ends(self) -> None:
        match = self.hltv.parse_match(
            _fixture("match_vrs_result.html"),
            "https://www.hltv.org/matches/4243/alpha-vs-beta",
        )

        vrs = match.vrs
        self.assertIsNotNone(vrs)
        self.assertTrue(vrs.settled)  # 赛后:只剩「赛前 + 实际增减」两列
        # 面板行序与 team1/team2 相反,pair() 必须按队名对齐
        alpha, beta = vrs.pair(match.team1, match.team2)
        self.assertEqual((alpha.name, beta.name), ("Alpha", "Beta"))
        self.assertEqual((alpha.current.points, alpha.current.rank), ("1242pt", 48))
        self.assertEqual((alpha.win.points, alpha.win.rank, alpha.win.rank_delta), ("+41pt", 41, 7))
        self.assertIsNone(alpha.lose)
        self.assertEqual((beta.current.points, beta.current.rank), ("2012pt", 2))
        self.assertEqual((beta.win.points, beta.win.rank), ("-31pt", 2))
        self.assertIsNone(beta.lose)

    def test_player_profile_and_recent_stats(self) -> None:
        profile = self.hltv.parse_player_profile(
            _fixture("player_profile_normal.html"), "7998"
        )

        self.assertEqual(profile.nick, "s1mple")
        self.assertEqual(profile.realname, "Oleksandr Kostyliev")
        self.assertEqual(profile.country, "Ukraine")
        self.assertEqual(profile.team, "BC.Game")
        self.assertEqual(profile.team_id, "12878")
        self.assertEqual(profile.rating, 1.22)
        self.assertEqual(profile.kd, 1.36)
        self.assertEqual(profile.kd_scope, "生涯统计")
        self.assertEqual(profile.maps, 29)
        self.assertIn("Past 3 months", profile.stats_period)
        self.assertEqual(profile.role_stats, [("Firepower", 85), ("Sniping", 89)])
        self.assertEqual(profile.major_wins, 1)
        self.assertEqual(profile.major_mvps, 1)
        self.assertEqual(len(profile.recent_matches), 2)
        self.assertEqual(profile.recent_matches[0].opponent, "Sashi")
        self.assertEqual(profile.recent_matches[0].result, "win")
        self.assertEqual(profile.recent_matches[0].rating, 1.31)
        self.assertEqual(profile.recent_matches[1].result, "loss")

    def test_player_stats_extracts_kd_kills_and_maps(self) -> None:
        stats = self.hltv.parse_player_stats(_fixture("player_stats_normal.html"))

        self.assertEqual(stats["kd"], 1.24)
        self.assertEqual(stats["kills"], 582)
        self.assertEqual(stats["deaths"], 469)
        self.assertEqual(stats["maps"], 29)

    def test_team_profile_rankings_roster_and_recent_results(self) -> None:
        profile = self.hltv.parse_team_profile(
            _fixture("team_profile_normal.html"), "4608"
        )

        self.assertEqual(profile.name, "Natus Vincere")
        self.assertEqual(profile.country, "Europe")
        self.assertEqual(profile.world_rank, 10)
        self.assertEqual(profile.regional_rank, 8)
        self.assertEqual(profile.region, "Europe")
        self.assertEqual(profile.vrs_rank, 20)
        self.assertEqual(profile.vrs_regional_rank, 15)
        self.assertEqual(profile.vrs_region, "Europe")
        self.assertEqual(profile.average_age, 24.4)
        self.assertEqual(profile.coach, "Andrey 'B1ad3' Gorodenskiy")
        self.assertEqual(profile.win_rate, "40.0%")
        self.assertEqual(profile.win_streak, "0")
        self.assertEqual([p.nick for p in profile.roster], ["b1t", "Aleksib", "iM", "w0nderful", "makazze"])
        self.assertEqual(profile.roster[0].rating, 1.12)
        self.assertEqual(len(profile.recent_results), 2)
        self.assertEqual(profile.recent_results[0].opponent, "Vitality")
        self.assertEqual(profile.recent_results[0].result, "loss")
        self.assertEqual(profile.recent_results[1].opponent, "G2")
        self.assertEqual(profile.recent_results[1].result, "win")

    def test_team_profile_uses_page_name_when_id_does_not_match(self) -> None:
        profile = self.hltv.parse_team_profile(
            _fixture("team_profile_normal.html"), "999999"
        )

        self.assertEqual([item.opponent for item in profile.recent_results], ["Vitality", "G2"])
        self.assertEqual([item.result for item in profile.recent_results], ["loss", "win"])

    def test_team_profile_handles_target_team_on_second_side(self) -> None:
        profile = self.hltv.parse_team_profile(
            """
            <div class="teamProfile">
              <div class="profile-team-name">Beta</div>
              <div class="match-table"><table><tbody>
                <tr class="team-row">
                  <td class="date-cell">01/10/2026</td>
                  <td class="team-center-cell">
                    <a class="team-name team-1" href="/team/1/alpha">Alpha</a>
                    1 <span>:</span> 2
                    <a class="team-name team-2" href="/team/2/beta">Beta</a>
                  </td>
                  <td class="stats-button-cell"><a href="/matches/1/a-vs-b">Match</a></td>
                </tr>
              </tbody></table></div>
            </div>
            """,
            "2",
        )

        self.assertEqual(profile.recent_results[0].opponent, "Alpha")
        self.assertEqual(profile.recent_results[0].score, "1 : 2")
        self.assertEqual(profile.recent_results[0].result, "win")


    def test_bracket_round_ignores_scheduled_shells_until_opponents_exist(self) -> None:
        hltv = self.hltv
        self.assertTrue(hltv._requires_opponent_selection("BLAST Bounty 2026 Season 2 Finals"))
        self.assertFalse(hltv._requires_opponent_selection("IEM Cologne 2026"))


        scheduled_shell = hltv.Matchup(
            "2396019",
            "",
            1_785_580_200_000,
            3,
            hltv.SlotTeam(),
            hltv.SlotTeam(),
        )
        inferred_pairing = hltv.Matchup(
            None,
            "",
            0,
            0,
            hltv.SlotTeam("Spirit"),
            hltv.SlotTeam("MOUZ"),
        )
        confirmed_match = hltv.Matchup(
            "2396018",
            "",
            1_785_416_400_000,
            3,
            hltv.SlotTeam("Liquid"),
            hltv.SlotTeam("Spirit"),
        )

        self.assertFalse(hltv.BracketRound("半决赛", [scheduled_shell]).is_pending())
        self.assertTrue(
            hltv.BracketRound(
                "半决赛", [scheduled_shell], requires_opponent_selection=True
            ).is_pending()
        )
        self.assertTrue(
            hltv.BracketRound(
                "半决赛", [inferred_pairing], requires_opponent_selection=True
            ).is_pending()
        )
        self.assertFalse(
            hltv.BracketRound(
                "八强赛", [confirmed_match], requires_opponent_selection=True
            ).is_pending()
        )

        semifinal = hltv.BracketRound(
            "半决赛",
            [inferred_pairing],
            stage="semifinal",
            requires_opponent_selection=True,
        )
        bracket = hltv.Bracket("single", "Stage 1", upper=[semifinal])
        reserved_slot = hltv._SchedEntry(
            start=1_785_580_200_000,
            bo=3,
            stage="semifinal",
            teams=frozenset(),
        )
        hltv._enrich_bracket_schedule([bracket], [reserved_slot])
        self.assertEqual((inferred_pairing.start_unix, inferred_pairing.best_of), (0, 0))
        self.assertTrue(semifinal.is_pending())

        official_match = hltv._SchedEntry(
            start=1_785_580_200_000,
            bo=3,
            stage="semifinal",
            teams=frozenset({"spirit", "mouz"}),
        )
        hltv._enrich_bracket_schedule([bracket], [official_match])
        self.assertEqual(
            (inferred_pairing.start_unix, inferred_pairing.best_of),
            (1_785_580_200_000, 3),
        )
        self.assertFalse(semifinal.is_pending())


        normal_pairing = hltv.Matchup(
            None,
            "",
            0,
            0,
            hltv.SlotTeam("Alpha"),
            hltv.SlotTeam("Beta"),
        )
        normal_round = hltv.BracketRound(
            "半决赛", [normal_pairing], stage="semifinal"
        )
        normal_bracket = hltv.Bracket("single", "Playoffs", upper=[normal_round])
        hltv._enrich_bracket_schedule([normal_bracket], [reserved_slot])
        self.assertEqual(
            (normal_pairing.start_unix, normal_pairing.best_of),
            (1_785_580_200_000, 3),
        )
        self.assertFalse(normal_round.is_pending())

    def test_multi_tree_same_stage_backfill_stays_in_its_own_tree(self) -> None:
        """EWC 形态:主单败树与小组双败树都有 grandfinal 轮,主赛段总决赛的排期
        不得回填到小组树的同名空轮上(回归:小组总决赛曾显示主赛段的时间/BO5)。"""
        hltv = self.hltv

        def _mu(t1: str = "", t2: str = "") -> object:
            return hltv.Matchup(None, "", 0, 0, hltv.SlotTeam(t1), hltv.SlotTeam(t2))

        def _build() -> tuple:
            main_final = _mu()
            main = hltv.Bracket(
                "single",
                "Playoffs",
                upper=[
                    hltv.BracketRound(
                        "半决赛", [_mu("FaZe", "Vitality"), _mu("B8", "Spirit")], stage="semifinal"
                    ),
                    hltv.BracketRound("总决赛", [main_final], stage="grandfinal"),
                ],
            )
            group_final = _mu()
            group = hltv.Bracket(
                "double",
                "Group A",
                upper=[hltv.BracketRound("胜者组首轮", [_mu("Spirit", "JiJieHao")], stage="upperquarter")],
                finals=[hltv.BracketRound("总决赛", [group_final], stage="grandfinal")],
            )
            return main, group, main_final, group_final

        # 场次带队名:两队都只同属主树 → 只回填主树总决赛,小组树保持待定
        main, group, main_final, group_final = _build()
        named_final = hltv._SchedEntry(
            start=1_787_484_600_000, bo=5, stage="grandfinal", teams=frozenset({"faze", "spirit"})
        )
        hltv._enrich_bracket_schedule([main, group], [named_final])
        self.assertEqual((main_final.start_unix, main_final.best_of), (1_787_484_600_000, 5))
        self.assertEqual((group_final.start_unix, group_final.best_of), (0, 0))

        # 场次双方待定:多棵树的同名轮次无法归属 → 谁都不回填,而不是错发给先遍历的树
        main, group, main_final, group_final = _build()
        tbd_final = hltv._SchedEntry(
            start=1_787_484_600_000, bo=5, stage="grandfinal", teams=frozenset()
        )
        hltv._enrich_bracket_schedule([main, group], [tbd_final])
        self.assertEqual((main_final.start_unix, main_final.best_of), (0, 0))
        self.assertEqual((group_final.start_unix, group_final.best_of), (0, 0))

    def test_swiss_ref_only_popup_uses_dom_team_names_and_matches_schedule(self) -> None:
        html = """
        <div class="swiss-visual-container">
          <div class="swiss-visual-column active">
            <div class="swiss-visual-matchups-wrapper">
              <div class="swiss-visual-matchups-title">0:0</div>
              <div class="swiss-visual-matchup"
                   data-match-details-popup-json='{"ref":{"matchId":{"matchId":2398717},"resultId":null}}'>
                <div class="swiss-visual-team">
                  <img class="swiss-visual-team-logo" title="Falcons"
                       src="https://img-cdn.hltv.org/teamlogo/falcons.png">
                </div>
                <div class="swiss-visual-team">
                  <img class="swiss-visual-team-logo" title="TYLOO"
                       src="https://img-cdn.hltv.org/teamlogo/tyloo.png">
                </div>
              </div>
            </div>
          </div>
        </div>
        """

        stage = self.hltv.parse_swiss(self.hltv.tree(html), set())

        assert stage is not None
        matchup = stage.columns[0].cells[0].matchups[0]
        self.assertEqual(matchup.match_id, "2398717")
        self.assertEqual(matchup.team1.name, "Falcons")
        self.assertEqual(matchup.team2.name, "TYLOO")

        self.hltv._enrich_swiss_schedule(
            stage,
            [
                self.hltv._SchedEntry(
                    start=1_788_000_000_000,
                    bo=3,
                    stage="",
                    teams=frozenset(),
                    match_id="2398717",
                )
            ],
        )
        self.assertEqual(matchup.start_unix, 1_788_000_000_000)
        self.assertEqual(matchup.best_of, 3)


class HltvParserDegradedInputContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hltv = _load_hltv_module()

    def test_missing_optional_fields_use_defaults_or_skip_incomplete_rows(self) -> None:
        html = _fixture("fields_missing.html")

        events = self.hltv.parse_events(html)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "Minimal Event")
        self.assertEqual((events[0].start_unix, events[0].end_unix), (0, 0))
        self.assertEqual((events[0].date_text, events[0].teams, events[0].prize), ("", "", ""))

        self.assertEqual(self.hltv.parse_results(html), [])

        match = self.hltv.parse_match(html, "https://www.hltv.org/matches/6/minimal")
        self.assertEqual((match.team1, match.team2), ("Team 1", "Team 2"))
        self.assertEqual(len(match.maps), 1)
        self.assertEqual(match.maps[0].name, "Ancient")
        self.assertFalse(match.maps[0].finished)
        self.assertIsNone(match.maps[0].mapstatsid)
        self.assertIsNone(match.vrs)  # 无 VRS 模块 → 卡片自动不显示该面板

    def test_cloudflare_page_never_becomes_domain_data(self) -> None:
        html = _fixture("cloudflare_challenge.html")

        self.assertEqual(self.hltv.parse_whitelist(html), [])
        self.assertEqual(self.hltv.featured_event_ids(html), set())
        self.assertEqual(self.hltv.parse_events(html), [])
        self.assertEqual(self.hltv.parse_live_matches(html), [])
        self.assertEqual(self.hltv.parse_upcoming_matches(html), [])
        self.assertEqual(self.hltv.parse_results(html), [])

        match = self.hltv.parse_match(html, "https://www.hltv.org/matches/7/blocked")
        self.assertEqual(match.match_id, "7")
        self.assertEqual((match.team1, match.team2), ("Team 1", "Team 2"))
        self.assertEqual(match.maps, [])
        self.assertEqual(match.event_name, "")
        self.assertIsNone(match.vrs)

        schedule = self.hltv.parse_event_schedule(html, "9002")
        self.assertEqual(schedule.event_id, "9002")
        self.assertEqual(schedule.name, "")
        self.assertIsNone(schedule.swiss)
        self.assertEqual(schedule.brackets, [])
        self.assertEqual(schedule.groups, [])


    def test_parse_vrs_ranking_extracts_logos_and_countries(self) -> None:
        fixture_path = FIXTURES / "ranking_cache_sample.html"
        if not fixture_path.exists():
            return
        html = fixture_path.read_text(encoding="utf-8")
        teams, date_text = self.hltv.parse_vrs_ranking(html)
        self.assertTrue(len(teams) > 0)
        self.assertTrue("July 23rd, 2026" in date_text or "2026" in date_text)
        top1 = teams[0]
        self.assertEqual(top1.name, "Vitality")
        self.assertEqual(top1.rank, 1)
        self.assertEqual(top1.country, "France")
        self.assertTrue(top1.logo_url and "teamlogo/" in top1.logo_url)
        self.assertEqual(top1.points, 1977)
        self.assertEqual(top1.region, "EU")

    def test_parse_ranking_extracts_rank_change_and_country(self) -> None:
        sample_html = """
        <div class="ranked-team">
            <div class="ranking-header">
                <span class="position">#5</span>
                <span class="change positive">+2</span>
                <div class="teamLine">
                    <img class="flag" src="/flag.gif" title="China" alt="China" />
                    <span class="name">TYLOO</span>
                    <a href="/team/4863/tyloo">HLTV Team</a>
                </div>
            </div>
            <div class="team-logo">
                <img class="day-only" src="https://img-cdn.hltv.org/teamlogo/tyloo.png" alt="TYLOO" />
            </div>
            <div class="players">
                <a href="/player/19698/jamyoung"><span class="nick">JamYoung</span></a>
                <a href="/player/11271/advent"><span class="nick">advent</span></a>
            </div>
        </div>
        """
        teams = self.hltv.parse_ranking(sample_html)
        self.assertEqual(len(teams), 1)
        t = teams[0]
        self.assertEqual(t.name, "TYLOO")
        self.assertEqual(t.rank, 5)
        self.assertEqual(t.team_id, "4863")
        self.assertEqual(t.rank_change, "+2")
        self.assertEqual(t.country, "China")
        self.assertEqual(t.logo_url, "https://img-cdn.hltv.org/teamlogo/tyloo.png")
        self.assertEqual(t.players, [("19698", "JamYoung"), ("11271", "advent")])

    def test_parse_veto_steps(self) -> None:
        html = """
        <div class="veto-box"><div class="preformatted-text">Best of 3 (LAN)</div></div>
        <div class="veto-box">
            1. FaZe removed Mirage
            2. Spirit removed Ancient
            3. FaZe picked Nuke
            4. Spirit picked Dust2
            5. FaZe removed Inferno
            6. Spirit removed Train
            7. Anubis was left over
        </div>
        """
        steps = self.hltv.parse_veto_steps(self.hltv.tree(html), "FaZe", "Spirit")
        self.assertEqual(len(steps), 7)
        self.assertEqual(steps[0].action, "ban")
        self.assertEqual(steps[0].team, "FaZe")
        self.assertEqual(steps[0].map_name, "Mirage")
        self.assertEqual(steps[0].map_cn, "荒漠迷城")

        self.assertEqual(steps[2].action, "pick")
        self.assertEqual(steps[2].team, "FaZe")
        self.assertEqual(steps[2].map_name, "Nuke")
        self.assertEqual(steps[2].map_cn, "核子危机")

        self.assertEqual(steps[6].action, "decider")
        self.assertEqual(steps[6].map_name, "Anubis")
        self.assertEqual(steps[6].map_cn, "阿努比斯")

    def test_parse_h2h_results(self) -> None:
        html = """
        <div class="result-con" data-zonedgrouping-entry-unix="1720000000000">
            <a href="/matches/2370001/vitality-vs-spirit-iem-cologne-2024">
                <span class="event-name">IEM Cologne 2024</span>
                <span class="team">Vitality</span>
                <span class="team">Spirit</span>
                <span class="result-score">2 - 1</span>
                <span class="team-won">Vitality</span>
            </a>
        </div>
        <div class="result-con" data-zonedgrouping-entry-unix="1680000000000">
            <a href="/matches/2360001/vitality-vs-spirit-blast-paris-2023">
                <span class="event-name">BLAST Paris Major 2023</span>
                <span class="team">Spirit</span>
                <span class="team">Vitality</span>
                <span class="result-score">2 - 0</span>
                <span class="team-won">Spirit</span>
            </a>
        </div>
        """
        # 场景 1: 给定参考时间，近 6 个月内有 1 场 (1720000000000)，过滤掉超过 6 个月的 1680000000000
        ref_now = 1720000000000 + 30 * 86400 * 1000  # 比赛后 30 天
        summary = self.hltv.parse_h2h_results(
            html, "Vitality", "Spirit", team1_id="9565", team2_id="10276", now_ms=ref_now
        )
        self.assertFalse(summary.is_fallback)
        self.assertEqual(summary.total_matches, 1)
        self.assertEqual(summary.team1_wins, 1)
        self.assertEqual(summary.team2_wins, 0)
        self.assertEqual(len(summary.matches), 1)
        self.assertEqual(summary.matches[0].score1, 2)
        self.assertEqual(summary.matches[0].score2, 1)

        # 场景 2: 近 6 个月内无交手，自动回退展示最近 1 场历史比赛
        far_future = 1720000000000 + 365 * 86400 * 1000  # 1 年后
        summary_fallback = self.hltv.parse_h2h_results(
            html, "Vitality", "Spirit", team1_id="9565", team2_id="10276", now_ms=far_future
        )
        self.assertTrue(summary_fallback.is_fallback)
        self.assertEqual(summary_fallback.fallback_date, "2024-07-03")
        self.assertEqual(summary_fallback.total_matches, 1)
        self.assertEqual(summary_fallback.team1_wins, 1)
        self.assertEqual(summary_fallback.team2_wins, 0)
        self.assertEqual(len(summary_fallback.matches), 1)
        self.assertEqual(summary_fallback.matches[0].match_id, "2370001")

        # 场景 3: 完全没有历史比赛记录
        summary_empty = self.hltv.parse_h2h_results(
            "<div>无比赛</div>", "Vitality", "Spirit", team1_id="9565", team2_id="10276"
        )
        self.assertFalse(summary_empty.is_fallback)
        self.assertEqual(summary_empty.total_matches, 0)
        self.assertEqual(summary_empty.team1_wins, 0)
        self.assertEqual(summary_empty.team2_wins, 0)
        self.assertEqual(len(summary_empty.matches), 0)

    def test_parse_h2h_results_strict_matching(self) -> None:
        """测试当 HLTV results 返回两队各自的比赛并集时，严格仅匹配两队之间真正的交手。

        防止 Falcons 对阵 NIP 时，把 NIP 误当作 TYLOO 并把 NIP 队标赋给 TYLOO。
        """
        html = """
        <div class="result-con" data-zonedgrouping-entry-unix="1791024625000">
            <a href="/matches/2380001/falcons-vs-ninjas-in-pyjamas-esl-pro-league">
                <span class="event-name">ESL Pro League Season 24</span>
                <span class="team">Falcons</span>
                <span class="team">Ninjas in Pyjamas</span>
                <span class="result-score">2 - 0</span>
                <span class="team-won">Falcons</span>
                <img title="Falcons" src="https://img-cdn.hltv.org/teamlogo/falcons.png">
                <img title="Ninjas in Pyjamas" src="https://img-cdn.hltv.org/teamlogo/nip.png">
            </a>
        </div>
        <div class="result-con" data-zonedgrouping-entry-unix="1791024620000">
            <a href="/matches/2380002/falcons-vs-tyloo-esl-pro-league">
                <span class="event-name">ESL Pro League Season 24</span>
                <span class="team">Falcons</span>
                <span class="team">TYLOO</span>
                <span class="result-score">2 - 0</span>
                <span class="team-won">Falcons</span>
                <img title="Falcons" src="https://img-cdn.hltv.org/teamlogo/falcons.png">
                <img title="TYLOO" src="https://img-cdn.hltv.org/teamlogo/tyloo.png">
            </a>
        </div>
        <div class="result-con" data-zonedgrouping-entry-unix="1791024610000">
            <a href="/matches/2380003/tyloo-vs-depo-challengers">
                <span class="event-name">Challengers</span>
                <span class="team">TYLOO</span>
                <span class="team">DEPO</span>
                <span class="result-score">3 - 0</span>
                <span class="team-won">TYLOO</span>
                <img title="TYLOO" src="https://img-cdn.hltv.org/teamlogo/tyloo.png">
                <img title="DEPO" src="https://img-cdn.hltv.org/teamlogo/depo.png">
            </a>
        </div>
        """
        summary = self.hltv.parse_h2h_results(
            html, "TYLOO", "Falcons", team1_id="4863", team2_id="11283", now_ms=1791024630000
        )
        # 严格只保留 TYLOO vs Falcons 这一场，跳过 Falcons vs NIP 和 TYLOO vs DEPO
        self.assertEqual(summary.total_matches, 1)
        self.assertEqual(summary.team1_wins, 0)
        self.assertEqual(summary.team2_wins, 1)
        self.assertEqual(len(summary.matches), 1)
        self.assertEqual(summary.matches[0].score1, 0)
        self.assertEqual(summary.matches[0].score2, 2)
        self.assertEqual(summary.matches[0].winner, "team2")
        # 队标必须是 TYLOO 自己的队标，绝对不能是 NIP
        self.assertIn("tyloo.png", summary.team1_logo or "")
        self.assertIn("falcons.png", summary.team2_logo or "")
        self.assertNotIn("nip.png", summary.team1_logo or "")
    def test_bracket_from_json_modern_flat_slots_single_elimination(self) -> None:
        """测试现代 HLTV 单败淘汰赛 JSON（slot1, slot2 平铺格式与 roundName 字典）解析。"""
        raw_json = {
            "type": "org.hltv.bracket.model.Bracket.SingleElimination",
            "bracketId": {"id": 5359},
            "name": "Playoffs",
            "rounds": [
                {
                    "type": "org.hltv.bracket.model.Bracket.Round.Round2",
                    "roundName": {"name": "Semi-finals", "shortName": "Semis"},
                    "slot1": {
                        "slotId": {"id": "Semifinals1"},
                        "matchup": {
                            "match": {
                                "matchId": 2399415,
                                "status": "Normal",
                                "startTime": 1792256400000,
                                "numberOfMaps": 3,
                            },
                            "team1": {"name": "Falcons", "logo": {"dayLogoURL": "falcons.png"}},
                            "team2": {"name": "BetBoom", "logo": {"dayLogoURL": "betboom.png"}},
                        },
                    },
                    "slot2": {
                        "slotId": {"id": "Semifinals2"},
                        "matchup": {
                            "match": {
                                "matchId": 2399416,
                                "status": "Normal",
                                "startTime": 1792256400000,
                                "numberOfMaps": 3,
                            },
                            "team1": {"name": "FURIA", "logo": {"dayLogoURL": "furia.png"}},
                            "team2": {"name": "The MongolZ", "logo": {"dayLogoURL": "mongolz.png"}},
                        },
                    },
                }
            ],
        }
        bracket = self.hltv._bracket_from_json(raw_json, set())
        self.assertIsNotNone(bracket)
        self.assertEqual(bracket.kind, "single")
        self.assertEqual(len(bracket.upper), 1)
        semis = bracket.upper[0]
        self.assertEqual(semis.name, "半决赛")
        self.assertEqual(len(semis.matchups), 2)
        self.assertEqual(semis.matchups[0].team1.name, "Falcons")
        self.assertEqual(semis.matchups[0].team2.name, "BetBoom")
        self.assertEqual(semis.matchups[1].team1.name, "FURIA")
        self.assertEqual(semis.matchups[1].team2.name, "The MongolZ")

    def test_bracket_from_json_modern_double_elimination(self) -> None:
        """测试现代 HLTV 双败淘汰赛 JSON（slot1, slot2 平铺格式及字符串 roundName）解析。"""
        raw_json = {
            "type": "org.hltv.bracket.model.Bracket.DoubleElimination4",
            "bracketId": {"id": 5360},
            "name": "Group A",
            "upperRound1": {
                "roundName": "Upper Round 1",
                "slot1": {
                    "matchup": {
                        "match": {"matchId": 2399420, "numberOfMaps": 3},
                        "resultId": {"resultId": 132134},
                        "score": {
                            "team1Score": 1,
                            "team2Score": 2,
                            "team1Winner": False,
                        },
                        "team1": {"name": "Vitality"},
                        "team2": {"name": "G2"},
                    }
                },
            },
            "grandFinal": {
                "roundName": "Grand Final",
                "slot1": {
                    "matchup": {
                        "match": {"matchId": 2399421, "numberOfMaps": 5},
                        "team1": {"name": "Vitality"},
                        "team2": {"name": "Spirit"},
                    }
                },
            },
        }
        bracket = self.hltv._bracket_from_json(raw_json, set())
        self.assertIsNotNone(bracket)
        self.assertEqual(bracket.kind, "double")
        self.assertEqual(bracket.name, "Group A")
        self.assertEqual(len(bracket.upper), 1)
        self.assertEqual(bracket.upper[0].name, "胜者组首轮")
        self.assertEqual(len(bracket.upper[0].matchups), 1)
        mu0 = bracket.upper[0].matchups[0]
        self.assertEqual(mu0.team1.name, "Vitality")
        self.assertEqual((mu0.score1, mu0.score2), (1, 2))
        self.assertEqual(mu0.winner, "team2")
        self.assertTrue(mu0.finished)
        self.assertEqual(len(bracket.finals), 1)
        self.assertEqual(bracket.finals[0].name, "总决赛")


if __name__ == "__main__":
    unittest.main()

