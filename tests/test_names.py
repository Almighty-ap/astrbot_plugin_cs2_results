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
