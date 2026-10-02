"""Local team/player mention matching for HLTV RSS news."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import names

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'._-]*")


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(text or ""))


@dataclass
class NewsEntityMatcher:
    team_index: dict[tuple[str, ...], set[str]] = field(default_factory=dict)
    player_index: dict[tuple[str, ...], set[str]] = field(default_factory=dict)

    @classmethod
    def from_targets(cls, targets: Iterable[object]) -> "NewsEntityMatcher":
        matcher = cls()
        for target in targets:
            kind = str(getattr(target, "kind", "") or "")
            target_key = str(getattr(target, "target_key", "") or "").strip()
            display = str(getattr(target, "display", "") or "").strip()
            if not target_key or not display:
                continue
            if kind == "team":
                variants = {display}
                variants.update(names.team_alias_variants(display))
                for variant in variants:
                    key = _tokens(variant)
                    if key:
                        matcher.team_index.setdefault(key, set()).add(target_key)
            elif kind == "player":
                for variant in {display, names.deleet(display)}:
                    key = _tokens(variant)
                    if key:
                        matcher.player_index.setdefault(key, set()).add(target_key)
        return matcher

    def match(self, text: str) -> tuple[set[str], set[str]]:
        tokens = _tokens(text)
        if not tokens:
            return set(), set()
        max_len = max(
            [len(key) for key in self.team_index] + [len(key) for key in self.player_index] + [1]
        )
        teams: set[str] = set()
        players: set[str] = set()
        i = 0
        while i < len(tokens):
            matched_len = 0
            for size in range(min(max_len, len(tokens) - i), 0, -1):
                phrase = tokens[i : i + size]
                team_hits = self.team_index.get(phrase)
                player_hits = self.player_index.get(phrase)
                if not team_hits and not player_hits:
                    continue
                if team_hits:
                    teams.update(team_hits)
                if player_hits:
                    players.update(player_hits)
                matched_len = size
                break
            i += matched_len or 1
        return teams, players
