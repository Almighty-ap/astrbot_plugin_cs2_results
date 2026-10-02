from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

from astrbot_plugin_cs2_results import news, store
from astrbot_plugin_cs2_results.config import Config
from astrbot_plugin_cs2_results.news_entities import NewsEntityMatcher


@dataclass(frozen=True)
class _Target:
    kind: str
    target_key: str
    display: str


@dataclass(frozen=True)
class _NewsItem:
    title: str
    description: str = ""


def test_news_entity_matcher_handles_team_alias_multiword_and_leet() -> None:
    matcher = NewsEntityMatcher.from_targets(
        [
            _Target("team", "natus vincere", "Natus Vincere"),
            _Target("player", "7998", "s1mple"),
        ]
    )

    teams, players = matcher.match(
        "NAVI return to the final while s1mple leads the server"
    )

    assert teams == {"natus vincere"}
    assert players == {"7998"}

    teams, players = matcher.match("A simple change in the roster")
    assert teams == set()
    assert players == {"7998"}


def test_mentions_for_item_uses_group_targets_and_caps(
    monkeypatch,
) -> None:
    targets = [_Target("team", "vitality", "Vitality")]
    monkeypatch.setattr(store, "all_targets", lambda: targets)
    monkeypatch.setattr(
        store,
        "recipients_for",
        lambda groups, teams, players: {1001: {7, 42, 99}},
    )
    cfg = Config(
        cs2_news_mention_subscriptions=True,
        cs2_news_mention_teams=True,
        cs2_news_mention_players=True,
        cs2_news_mention_max_per_group=2,
    )
    service = news.NewsService(cfg, fetcher=None, context=None)  # type: ignore[arg-type]
    item = news.NewsItem(
        guid="g1",
        title="Vitality win the grand final",
        description="ZywOo was named MVP.",
        link="",
        image_url="",
    )

    assert news.NewsService._group_id_from_umo(
        "napcat:GroupMessage:1001"
    ) == 1001
    assert service.mentions_for_item(item, 1001) == [7, 42]


def test_news_push_builds_per_group_at_then_image(monkeypatch) -> None:
    monkeypatch.setattr(
        store,
        "recipients_for",
        lambda groups, teams, players: {1001: {7, 42}},
    )
    cfg = Config(
        cs2_news_mention_subscriptions=True,
        cs2_news_mention_teams=True,
        cs2_news_mention_players=True,
    )

    class _Context:
        def __init__(self) -> None:
            self.chains = []

        async def send_message(self, umo, chain) -> bool:  # type: ignore[no-untyped-def]
            self.chains.append((umo, chain))
            return True

    context = _Context()
    service = news.NewsService(cfg, fetcher=None, context=context)  # type: ignore[arg-type]
    service.render_item = AsyncMock(return_value=b"png")  # type: ignore[method-assign]
    matcher = NewsEntityMatcher.from_targets(
        [_Target("team", "vitality", "Vitality")]
    )
    item = news.NewsItem(
        guid="g1",
        title="Vitality win",
        description="",
        link="",
        image_url="",
    )

    asyncio.run(
        service.push_item(
            item,
            ["napcat:GroupMessage:1001"],
            matcher=matcher,
        )
    )

    _, chain = context.chains[0]
    assert [component.qq for component in chain.chain[:-1]] == [7, 42]
    assert chain.chain[-1].__class__.__name__ == "Image"
