"""Local team/player mention matching for HLTV RSS news."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import names

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'._-]*")

# 中文俗称/输入法纠错产出的英文常见词、数字梗或过短缩写，仅用于命令输入解析，
# 严禁进入 HLTV 英文资讯全文词元索引（避免 "a"、"on X"、"Major"、"a bit" 等高频误 @）。
_NEWS_AMBIGUOUS_SINGLE_TOKENS = frozenset(
    {
        "a",
        "b",
        "p",
        "x",
        "s1",
        "k3",
        "4k",
        "ee",
        "mo",
        "pm",
        "pm2.5",
        "pdf",
        "pee",
        "259",
        "911",
        "bit",
        "bot",
        "simple",
        "wonderful",
        "exercise",
        "exercice",
        "ex3",
        "major",
    }
)


def _normalize_token(raw_token: str) -> str:
    tok = (raw_token or "").casefold().strip("._'-")
    if tok.endswith("'s") and len(tok) > 3:
        tok = tok[:-2].rstrip("._'-")
    return tok


def _tokens(text: str) -> tuple[str, ...]:
    normalized_text = (text or "").replace("’", "'").replace("‘", "'")
    out: list[str] = []
    for raw in _TOKEN_RE.findall(normalized_text):
        tok = _normalize_token(raw)
        if tok:
            out.append(tok)
    return tuple(out)


def _indexable_variant_tokens(variant: str, official_display: str) -> tuple[str, ...]:
    """仅允许纯 ASCII 且无英语高频歧义的变体词元进入资讯实体索引。"""
    raw = (variant or "").strip()
    if not raw or not raw.isascii():
        return ()
    key = _tokens(raw)
    if not key:
        return ()
    if len(key) == 1:
        tok = key[0]
        if len(tok) < 2:
            return ()
        official_tokens = _tokens(official_display)
        is_official = len(official_tokens) == 1 and tok == official_tokens[0]
        if not is_official and tok in _NEWS_AMBIGUOUS_SINGLE_TOKENS:
            return ()
    return key


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
                for variant in list(variants):
                    if any(sep in variant for sep in (".", "-", "_")):
                        variants.add(re.sub(r"[._-]+", " ", variant))
                for variant in variants:
                    key = _indexable_variant_tokens(variant, display)
                    if key:
                        matcher.team_index.setdefault(key, set()).add(target_key)
            elif kind == "player":
                variants = {display, names.deleet(display)}
                variants.update(names.player_alias_variants(display))
                for variant in variants:
                    key = _indexable_variant_tokens(variant, display)
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
