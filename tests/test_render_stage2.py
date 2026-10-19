from __future__ import annotations

import unittest

from astrbot_plugin_cs2_results import render
from astrbot_plugin_cs2_results.hltv import (
    H2HMatch,
    H2HSummary,
    ScheduledMatch,
)


class TestRenderStage2(unittest.TestCase):
    def test_h2h_card_html(self) -> None:
        summary = H2HSummary(
            team1="Vitality",
            team2="Spirit",
            team1_id="9565",
            team2_id="10276",
            total_matches=5,
            team1_wins=3,
            team2_wins=2,
            team1_players=["ZywOo", "apEX", "Spinx", "flameZ", "mezii"],
            team2_players=["chopper", "sh1ro", "magixx", "donk", "zont1x"],
            matches=[
                H2HMatch(
                    match_id="1",
                    url="https://example.com/1",
                    date_ms=1720000000000,
                    event_name="IEM Cologne 2024",
                    team1="Vitality",
                    team2="Spirit",
                    score1=2,
                    score2=1,
                    winner="team1",
                    is_cs2=True,
                    date_text="2024-07-03",
                )
            ],
        )
        html = render.build_h2h_html(summary, "12:00 北京时间")
        self.assertIn("战队交手历史", html)
        self.assertIn("Vitality", html)
        self.assertIn("Spirit", html)
        self.assertIn("ZywOo", html)
        self.assertIn("donk", html)
        self.assertIn("近 6 个月交手", html)
        self.assertIn("Vitality 3 胜 - 2 胜 Spirit", html)
        self.assertIn("IEM Cologne 2024", html)
        # 不再包含 CS2/CS:GO 时代切片与标签
        self.assertNotIn("CS2 时代", html)
        self.assertNotIn("CS:GO 时代", html)

    def test_h2h_fallback_card_html(self) -> None:
        summary = H2HSummary(
            team1="Vitality",
            team2="Spirit",
            team1_id="9565",
            team2_id="10276",
            total_matches=1,
            team1_wins=1,
            team2_wins=0,
            is_fallback=True,
            fallback_date="2024-07-03",
            matches=[
                H2HMatch(
                    match_id="1",
                    url="https://example.com/1",
                    date_ms=1720000000000,
                    event_name="IEM Cologne 2024",
                    team1="Vitality",
                    team2="Spirit",
                    score1=2,
                    score2=1,
                    winner="team1",
                    is_cs2=True,
                    date_text="2024-07-03",
                )
            ],
        )
        html = render.build_h2h_html(summary, "12:00 北京时间")
        self.assertIn("近 6 个月无交锋", html)
        self.assertIn("展示最近 1 场历史交锋", html)
        self.assertIn("2024-07-03", html)
        self.assertIn("历史回退", html)

    def test_morning_report_card_html(self) -> None:
        yesterday = [
            ScheduledMatch(
                match_id="101",
                event_id="1",
                event_name="EPL Season 24",
                event_logo=None,
                team1="MOUZ",
                team2="NAVI",
                start_unix=1720000000000,
                best_of="bo3",
                status="finished",
                score1=2,
                score2=1,
                winner="team1",
            )
        ]
        today = [
            ScheduledMatch(
                match_id="102",
                event_id="1",
                event_name="EPL Season 24",
                event_logo=None,
                team1="Vitality",
                team2="FaZe",
                start_unix=1720050000000,
                best_of="bo3",
                status="upcoming",
            )
        ]
        html = render.build_morning_report_html("2026年10月16日", yesterday, today, "08:30 北京时间")
        self.assertIn("CS2 每日晨报", html)
        self.assertIn("昨夜今晨战报", html)
        self.assertIn("今日焦点赛程", html)
        self.assertIn("MOUZ", html)
        self.assertIn("NAVI", html)
        self.assertIn("Vitality", html)
        self.assertIn("FaZe", html)

    def test_morning_report_card_next_day_and_live(self) -> None:
        live_match = ScheduledMatch(
            match_id="103",
            event_id="1",
            event_name="Thunderpick 2026",
            event_logo=None,
            team1="Spirit",
            team2="G2",
            start_unix=0,
            best_of="bo3",
            status="live",
        )
        # 2026-10-17 01:30:00 Beijing time (UTC+8) -> timestamp: 1792171800000
        next_day_match = ScheduledMatch(
            match_id="104",
            event_id="1",
            event_name="Thunderpick 2026",
            event_logo=None,
            team1="Aurora",
            team2="9z",
            start_unix=1792171800000,
            best_of="bo3",
            status="upcoming",
        )
        html = render.build_morning_report_html(
            "2026年10月16日 周五",
            [],
            [live_match, next_day_match],
            "08:30 北京时间",
        )
        self.assertIn("进行中", html)
        self.assertIn("次日 01:30", html)
        self.assertIn("昨夜今晨暂无完赛的顶级赛事", html)
        self.assertIn("Aurora", html)
        self.assertIn("9z", html)


if __name__ == "__main__":
    unittest.main()
