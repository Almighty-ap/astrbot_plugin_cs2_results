from __future__ import annotations

import asyncio
import json

from astrbot_plugin_cs2_results import names


class _SearchFetcher:
    async def fetch_search(self, term: str) -> str:
        assert term == "NAVI"
        return json.dumps(
            [
                {
                    "players": [],
                    "teams": [
                        {"id": 4608, "name": "Natus Vincere"},
                        {"id": 10371, "name": "NAVI Junior"},
                    ],
                }
            ]
        )


def test_live_team_search_resolves_navi_alias() -> None:
    result = asyncio.run(names.resolve_team(_SearchFetcher(), "NAVI"))

    assert result.status == "ok"
    assert result.team is not None
    assert result.team.id == "4608"
    assert result.team.name == "Natus Vincere"
