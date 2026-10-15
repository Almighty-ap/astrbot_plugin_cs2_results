from __future__ import annotations

import asyncio
import json

from astrbot_plugin_cs2_results import names


class _SearchFetcher:
    def __init__(self, payload: list[dict], term: str = "NAVI") -> None:
        self.payload = payload
        self.term = term

    async def fetch_search(self, term: str) -> str:
        assert term == self.term
        return json.dumps(self.payload)


def _search_payload(*, players: list[dict] | None = None, teams: list[dict] | None = None) -> list[dict]:
    return [{"players": players or [], "teams": teams or []}]


def test_live_team_search_resolves_navi_alias() -> None:
    fetcher = _SearchFetcher(
        _search_payload(
            teams=[
                {"id": 4608, "name": "Natus Vincere"},
                {"id": 10371, "name": "NAVI Junior"},
            ]
        )
    )
    result = asyncio.run(names.resolve_team(fetcher, "NAVI"))

    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "4608"
    assert result.team.name == "Natus Vincere"


def test_live_team_duplicate_prefers_better_world_rank() -> None:
    fetcher = _SearchFetcher(
        _search_payload(teams=[{"id": 11, "name": "Acme"}, {"id": 22, "name": "Acme"}]),
        term="Acme",
    )
    index = [
        {"team_id": "11", "name": "Acme", "team_key": "acme-1", "rank": 10},
        {"team_id": "22", "name": "Acme", "team_key": "acme-2", "rank": 2},
    ]

    result = asyncio.run(names.resolve_team(fetcher, "Acme", index))

    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "22"


def test_live_team_same_name_does_not_inherit_rank_by_name() -> None:
    fetcher = _SearchFetcher(
        _search_payload(teams=[{"id": 4863, "name": "TYLOO"}, {"id": 9999, "name": "TyLoo"}]),
        term="Tyloo",
    )
    index = [
        {"team_id": "4863", "name": "TYLOO", "team_key": "tyloo", "rank": 33},
    ]

    result = asyncio.run(names.resolve_team(fetcher, "Tyloo", index))

    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "4863"


def test_local_team_duplicate_prefers_better_world_rank() -> None:
    index = [
        {"team_id": "11", "name": "Acme", "team_key": "acme-1", "rank": None},
        {"team_id": "22", "name": "Acme", "team_key": "acme-2", "rank": 15},
    ]

    result = names.resolve_team_local("Acme", index)

    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "22"


def test_team_equal_best_rank_remains_ambiguous() -> None:
    index = [
        {"team_id": "11", "name": "Acme", "team_key": "acme-1", "rank": 5},
        {"team_id": "22", "name": "Acme", "team_key": "acme-2", "rank": 5},
    ]

    result = names.resolve_team_local("Acme", index)

    assert result.status == "ambiguous"
    assert [candidate.id for candidate in result.candidates] == ["11", "22"]


def test_live_player_duplicate_prefers_team_with_better_world_rank() -> None:
    fetcher = _SearchFetcher(
        _search_payload(
            players=[
                {"id": 1, "nickName": "dup", "team": {"name": "Alpha"}},
                {"id": 2, "nickName": "dup", "team": {"name": "Beta"}},
            ]
        ),
        term="dup",
    )
    index = [
        {"player_id": "1", "nick": "dup", "team": "Alpha", "team_key": "alpha", "team_rank": 20},
        {"player_id": "2", "nick": "dup", "team": "Beta", "team_key": "beta", "team_rank": 3},
    ]

    result = asyncio.run(names.resolve_player(fetcher, "dup", index))

    assert result.status == "ok"
    assert result.player is not None
    assert result.player.id == "2"


def test_local_player_duplicate_prefers_team_with_better_world_rank() -> None:
    index = [
        {"player_id": "1", "nick": "dup", "team": "Alpha", "team_key": "alpha", "team_rank": 20},
        {"player_id": "2", "nick": "dup", "team": "Beta", "team_key": "beta", "team_rank": 3},
    ]

    result = names.resolve_player_local("dup", index)

    assert result.status == "ok"
    assert result.player is not None
    assert result.player.id == "2"


def test_player_equal_best_team_rank_remains_ambiguous() -> None:
    index = [
        {"player_id": "1", "nick": "dup", "team": "Alpha", "team_key": "alpha", "team_rank": 4},
        {"player_id": "2", "nick": "dup", "team": "Beta", "team_key": "beta", "team_rank": 4},
    ]

    result = names.resolve_player_local("dup", index)

    assert result.status == "ambiguous"
    assert [candidate.id for candidate in result.candidates] == ["1", "2"]


def test_canonical_team_and_player_chinese_aliases() -> None:
    # 战队
    assert names.canonical_team("小蜜蜂") == "Vitality"
    assert names.canonical_team("小峰") == "Vitality"
    assert names.canonical_team("绿龙") == "Spirit"
    assert names.canonical_team("天禄") == "TYLOO"
    assert names.canonical_team("银河战舰") == "FaZe"
    assert names.canonical_team("黑豹") == "FURIA"
    assert names.canonical_team("a队") == "Astralis"
    assert names.canonical_team("老鼠") == "MOUZ"
    assert names.canonical_team("UnknownTeam") == "UnknownTeam"

    # 选手
    assert names.canonical_player("载物") == "ZywOo"
    assert names.canonical_player("贼物") == "ZywOo"
    assert names.canonical_player("森破") == "s1mple"
    assert names.canonical_player("小孩") == "m0NESY"
    assert names.canonical_player("太子") == "m0NESY"
    assert names.canonical_player("大表哥") == "karrigan"
    assert names.canonical_player("总监") == "Twistzz"
    assert names.canonical_player("雨神") == "rain"
    assert names.canonical_player("UnknownPlayer") == "UnknownPlayer"


def test_local_team_chinese_alias_resolves() -> None:
    index = [
        {"team_id": "9565", "name": "Vitality", "team_key": "vitality", "rank": 1},
        {"team_id": "7020", "name": "Spirit", "team_key": "spirit", "rank": 2},
        {"team_id": "4863", "name": "TYLOO", "team_key": "tyloo", "rank": 30},
        {"team_id": "8297", "name": "FURIA", "team_key": "furia", "rank": 8},
        {"team_id": "6667", "name": "FaZe", "team_key": "faze", "rank": 6},
    ]

    res = names.resolve_team_local("小蜜蜂", index)
    assert res.status == "ok"
    assert res.team is not None
    assert res.team.name == "Vitality"
    assert res.team.id == "9565"

    res = names.resolve_team_local("小峰", index)
    assert res.status == "ok"
    assert res.team is not None and res.team.name == "Vitality"

    res = names.resolve_team_local("绿龙", index)
    assert res.status == "ok"
    assert res.team is not None and res.team.name == "Spirit"

    res = names.resolve_team_local("天禄", index)
    assert res.status == "ok"
    assert res.team is not None and res.team.name == "TYLOO"

    res = names.resolve_team_local("银河战舰", index)
    assert res.status == "ok"
    assert res.team is not None and res.team.name == "FaZe"

    res = names.resolve_team_local("黑豹", index)
    assert res.status == "ok"
    assert res.team is not None and res.team.name == "FURIA"


def test_local_player_chinese_alias_resolves() -> None:
    index = [
        {"player_id": "11893", "nick": "ZywOo", "team": "Vitality", "team_rank": 1},
        {"player_id": "7998", "nick": "s1mple", "team": "Natus Vincere", "team_rank": 5},
        {"player_id": "19230", "nick": "m0NESY", "team": "G2", "team_rank": 3},
        {"player_id": "884", "nick": "karrigan", "team": "FaZe", "team_rank": 6},
        {"player_id": "10394", "nick": "Twistzz", "team": "Liquid", "team_rank": 12},
        {"player_id": "8183", "nick": "rain", "team": "FaZe", "team_rank": 6},
    ]

    for query, expected_nick, expected_id in [
        ("载物", "ZywOo", "11893"),
        ("贼物", "ZywOo", "11893"),
        ("森破", "s1mple", "7998"),
        ("小孩", "m0NESY", "19230"),
        ("太子", "m0NESY", "19230"),
        ("大表哥", "karrigan", "884"),
        ("总监", "Twistzz", "10394"),
        ("雨神", "rain", "8183"),
    ]:
        res = names.resolve_player_local(query, index)
        assert res.status == "ok"
        assert res.player is not None
        assert res.player.nick == expected_nick
        assert res.player.id == expected_id


def test_live_team_chinese_alias_searches_formal_name() -> None:
    fetcher = _SearchFetcher(
        _search_payload(teams=[{"id": 9565, "name": "Vitality"}]),
        term="Vitality",
    )
    result = asyncio.run(names.resolve_team(fetcher, "小蜜蜂"))
    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "9565"
    assert result.team.name == "Vitality"


def test_live_player_chinese_alias_searches_formal_nick() -> None:
    fetcher = _SearchFetcher(
        _search_payload(players=[{"id": 11893, "nickName": "ZywOo", "team": {"name": "Vitality"}}]),
        term="ZywOo",
    )
    result = asyncio.run(names.resolve_player(fetcher, "载物"))
    assert result.status == "ok"
    assert result.player is not None
    assert result.player.id == "11893"
    assert result.player.nick == "ZywOo"


def test_target_matches_aliases() -> None:
    assert names.target_matches("小蜜蜂", "Vitality", "team")
    assert names.target_matches("小峰", "Vitality", "team")
    assert names.target_matches("绿龙", "Spirit", "team")
    assert names.target_matches("天禄", "TYLOO", "team")
    assert names.target_matches("银河战舰", "FaZe", "team")
    assert names.target_matches("黑豹", "FURIA", "team")
    assert not names.target_matches("小蜜蜂", "Spirit", "team")

    assert names.target_matches("载物", "ZywOo", "player")
    assert names.target_matches("贼物", "ZywOo", "player")
    assert names.target_matches("森破", "s1mple", "player")
    assert names.target_matches("simple", "s1mple", "player")
    assert names.target_matches("小孩", "m0NESY", "player")
    assert names.target_matches("太子", "m0NESY", "player")
    assert names.target_matches("大表哥", "karrigan", "player")
    assert names.target_matches("总监", "Twistzz", "player")
    assert names.target_matches("雨神", "rain", "player")
    assert not names.target_matches("载物", "s1mple", "player")


def test_team_matches_filter_aliases() -> None:
    assert names.team_matches_filter("小蜜蜂", "Vitality")
    assert names.team_matches_filter("小峰", "Vitality")
    assert names.team_matches_filter("绿龙", "Spirit")
    assert names.team_matches_filter("天禄", "TYLOO")
    assert names.team_matches_filter("银河战舰", "FaZe")
    assert names.team_matches_filter("黑豹", "FURIA")
    assert names.team_matches_filter("FaZe", "FaZe")
    assert not names.team_matches_filter("小蜜蜂", "Spirit")


def test_v172_added_player_aliases_canonical_and_matches() -> None:
    cases = [
        ("孩贼王", "m0NESY"),
        ("千早爱烟", "karrigan"),
        ("练习哥", "Ex3rcice"),
        ("练*哥", "Ex3rcice"),
        ("ex3", "Ex3rcice"),
        ("exercise", "Ex3rcice"),
        ("点圣", "cadiaN"),
        ("出生点", "cadiaN"),
        ("点子", "cadiaN"),
        ("cadian", "cadiaN"),
        ("911", "ropz"),
        ("bot", "b1t"),
        ("pm", "iM"),
        ("pm2.5", "iM"),
        ("紫薇", "zweih"),
        ("哲伟", "zweih"),
        ("pdf", "w0nderful"),
        ("虾哥", "NiKo"),
        ("元首", "tabseN"),
        ("pee", "Jee"),
    ]
    for alias, expected in cases:
        assert names.canonical_player(alias) == expected
        assert names.target_matches(alias, expected, "player")


def test_v172_added_player_aliases_resolve_local() -> None:
    index = [
        {"player_id": "19230", "nick": "m0NESY", "team": "G2", "team_rank": 3},
        {"player_id": "884", "nick": "karrigan", "team": "FaZe", "team_rank": 6},
        {"player_id": "19714", "nick": "Ex3rcice", "team": "3DMAX", "team_rank": 21},
        {"player_id": "7964", "nick": "cadiaN", "team": "Astralis", "team_rank": 14},
        {"player_id": "11816", "nick": "ropz", "team": "FaZe", "team_rank": 6},
        {"player_id": "18987", "nick": "b1t", "team": "Natus Vincere", "team_rank": 1},
        {"player_id": "14759", "nick": "iM", "team": "Natus Vincere", "team_rank": 1},
        {"player_id": "22068", "nick": "zweih", "team": "Spirit", "team_rank": 2},
        {"player_id": "20127", "nick": "w0nderful", "team": "Natus Vincere", "team_rank": 1},
        {"player_id": "3741", "nick": "NiKo", "team": "G2", "team_rank": 3},
        {"player_id": "5794", "nick": "tabseN", "team": "BIG", "team_rank": 25},
        {"player_id": "20790", "nick": "Jee", "team": "TYLOO", "team_rank": 30},
    ]

    for alias, expected_nick, expected_id in [
        ("孩贼王", "m0NESY", "19230"),
        ("千早爱烟", "karrigan", "884"),
        ("练习哥", "Ex3rcice", "19714"),
        ("练*哥", "Ex3rcice", "19714"),
        ("点圣", "cadiaN", "7964"),
        ("出生点", "cadiaN", "7964"),
        ("911", "ropz", "11816"),
        ("bot", "b1t", "18987"),
        ("pm", "iM", "14759"),
        ("pm2.5", "iM", "14759"),
        ("紫薇", "zweih", "22068"),
        ("pdf", "w0nderful", "20127"),
        ("虾哥", "NiKo", "3741"),
        ("元首", "tabseN", "5794"),
        ("pee", "Jee", "20790"),
    ]:
        res = names.resolve_player_local(alias, index)
        assert res.status == "ok"
        assert res.player is not None
        assert res.player.nick == expected_nick
        assert res.player.id == expected_id
