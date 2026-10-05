"""战队/选手名字的宽松匹配 + 严格解析。

规则(实测于 HLTV 2026-07):
- 大小写不敏感(casefold)。
- 选手常用数字替字母(s1mple / m0NESY / b1t),用 **deleet** 归一:把昵称里的数字
  映射回字母(0→o 1→i 3→e 4→a 5→s 7→t …)。
- 匹配是**非对称**的:`cf(query)==cf(nick)` 或 `cf(query)==deleet(nick)`。
  即用户可以敲昵称原形(s1mple)或"读音形"(simple);但用户敲的数字不会被反向变字母,
  所以敲 "s1mple" 只命中 s1mple、不会误命中 "Simple"(避免歧义死循环)。
- 严格:只有 HLTV 搜索返回的**真实**候选里、按上面规则唯一命中才自动落库;多个命中时
  先按世界排名消歧(战队取队伍排名,选手取所属队伍排名),排名也并列或未知 → 让用户
  用更精确写法(如昵称原形)重发;只有模糊建议时绝不猜着存。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from . import hltv

# 数字→规范字母(仅单向:昵称里的数字可还原成字母)。
_LEET = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b", "9": "g", "2": "z", "6": "g",
}


def cf(s: str) -> str:
    return (s or "").strip().casefold()


def deleet(s: str) -> str:
    """casefold 后把数字还原成规范字母。"""
    return "".join(_LEET.get(c, c) for c in cf(s))


def nick_matches(query: str, nick: str) -> bool:
    """query 是否命中 nick(大小写无关 + 昵称 leet 还原)。"""
    q = cf(query)
    if not q:
        return False
    return q == cf(nick) or q == deleet(nick)


def team_key(name: str) -> str:
    """战队订阅的归一键 = casefold 队名(与比赛数据里的队名对齐匹配)。"""
    return cf(name)


ResolveStatus = Literal["ok", "ambiguous", "none", "error"]


@dataclass
class PlayerResolution:
    status: ResolveStatus
    player: Optional[hltv.SearchPlayer] = None
    candidates: list[hltv.SearchPlayer] = field(default_factory=list)


@dataclass
class TeamResolution:
    status: ResolveStatus
    team: Optional[hltv.SearchTeam] = None
    candidates: list[hltv.SearchTeam] = field(default_factory=list)


def _tiebreak_exact(query: str, strong: list, name_of) -> list:
    """多个 casefold/leet 命中时,若查询与某一个**区分大小写完全相等**,优先它。

    解决「Vitality vs ViTAlity」「Simple vs s1mple」这类只差大小写/leet 的同名歧义:
    用户敲原样大小写即可精确命中,不必再被迫在两个几乎一样的名字间猜。
    """
    q = (query or "").strip()
    exact = [c for c in strong if name_of(c) == q]
    return exact if len(exact) == 1 else strong


def _dedup_players(players: list[hltv.SearchPlayer]) -> list[hltv.SearchPlayer]:
    out: list[hltv.SearchPlayer] = []
    seen: set[str] = set()
    for p in players:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def _dedup_teams(teams: list[hltv.SearchTeam]) -> list[hltv.SearchTeam]:
    out: list[hltv.SearchTeam] = []
    seen: set[str] = set()
    for t in teams:
        if t.id in seen:
            continue
        seen.add(t.id)
        out.append(t)
    return out


def _rank_value(value) -> Optional[int]:
    """把本地名录里的 rank 规范成正整数;缺失/0/非法值都视为未知。"""
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _team_rank_lookup(teams: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    by_id: dict[str, int] = {}
    by_key: dict[str, int] = {}
    for team in teams or []:
        rank = _rank_value(team.get("rank"))
        if rank is None:
            continue
        team_id = str(team.get("team_id") or "").strip()
        if team_id:
            by_id.setdefault(team_id, rank)
            continue
        key = cf(str(team.get("team_key") or team.get("name") or ""))
        if key:
            by_key.setdefault(key, rank)
    return by_id, by_key


def _player_rank_lookup(players: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    by_id: dict[str, int] = {}
    by_team: dict[str, int] = {}
    for player in players or []:
        rank = _rank_value(player.get("team_rank"))
        if rank is None:
            continue
        player_id = str(player.get("player_id") or "").strip()
        if player_id:
            by_id.setdefault(player_id, rank)
        key = cf(str(player.get("team_key") or player.get("team") or ""))
        if key:
            by_team.setdefault(key, rank)
    return by_id, by_team


def _choose_by_rank(candidates: list, rank_of) -> tuple[Optional[object], list]:
    """按 rank 选唯一最优候选;并列最优或全未知时返回候选列表供用户确认。"""
    ranked = [(candidate, _rank_value(rank_of(candidate))) for candidate in candidates]
    known = [(candidate, rank) for candidate, rank in ranked if rank is not None]
    if not known:
        return None, candidates
    best_rank = min(rank for _, rank in known)
    winners = [candidate for candidate, rank in known if rank == best_rank]
    if len(winners) == 1:
        return winners[0], []
    ordered = [
        candidate
        for candidate, _ in sorted(
            ranked, key=lambda item: (item[1] is None, item[1] if item[1] is not None else 0)
        )
    ]
    return None, ordered


# —————————— 本地名录解析(订阅命令用:秒回、零 HLTV 请求) ——————————
def _suggest(query: str, items: list, name_of, limit: int = 6) -> list:
    """错拼/漏拼时的就近建议:子串包含(casefold + deleet 双通道)。"""
    q, dq = cf(query), deleet(query)
    if not q:
        return []
    out = []
    for it in items:
        n = name_of(it)
        if q in cf(n) or dq in deleet(n):
            out.append(it)
            if len(out) >= limit:
                break
    return out


# 常用俗称 → HLTV 正式队名(只收社区高频且与正式名对不上的;正式名能直接命中的不用进来)
_TEAM_ALIASES = {
    "navi": "Natus Vincere",
    "na'vi": "Natus Vincere",
    "nip": "Ninjas in Pyjamas",
    "vp": "Virtus.pro",
    "virtus pro": "Virtus.pro",
    "mongolz": "The MongolZ",
    "lvg": "Lynn Vision",
}


def team_alias_variants(name: str) -> set[str]:
    """Return aliases that resolve to an HLTV team's formal name."""
    formal = cf(name)
    return {
        alias
        for alias, target in _TEAM_ALIASES.items()
        if cf(target) == formal
    }


def resolve_team_local(query: str, teams: list[dict]) -> TeamResolution:
    """在本地名录里解析战队。teams = store.all_index_teams() 的行。

    status:"error"=名录为空(未初始化);"none" 时 candidates 是就近建议(仅提示,不落库)。
    """
    q = (query or "").strip()
    if not q:
        return TeamResolution("none")
    if not teams:
        return TeamResolution("error")
    cands = [
        hltv.SearchTeam(str(t.get("team_id") or t.get("team_key") or ""), str(t.get("name") or ""))
        for t in teams
        if t.get("name")
    ]
    rank_by_id, rank_by_key = _team_rank_lookup(teams)

    def rank_of(candidate: hltv.SearchTeam) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_key.get(team_key(candidate.name))

    # 俗称先行:唯一命中直接返回;命不中(如队伍改名离榜)按原查询走正常流程
    alias = _TEAM_ALIASES.get(cf(q))
    if alias:
        hit = [t for t in cands if nick_matches(alias, t.name)]
        if len(hit) == 1:
            return TeamResolution("ok", team=hit[0])
        if len(hit) > 1:
            chosen, ordered = _choose_by_rank(hit, rank_of)
            if chosen is not None:
                return TeamResolution("ok", team=chosen)
            return TeamResolution("ambiguous", candidates=ordered[:8])
    strong = [t for t in cands if nick_matches(q, t.name)]
    if len(strong) > 1:
        strong = _tiebreak_exact(q, strong, lambda t: t.name)
    if len(strong) == 1:
        return TeamResolution("ok", team=strong[0])
    if len(strong) > 1:
        chosen, ordered = _choose_by_rank(strong, rank_of)
        if chosen is not None:
            return TeamResolution("ok", team=chosen)
        return TeamResolution("ambiguous", candidates=ordered[:8])
    return TeamResolution("none", candidates=_suggest(q, cands, lambda t: t.name))


def resolve_player_local(query: str, players: list[dict]) -> PlayerResolution:
    """在本地名录里解析选手。players = store.all_index_players() 的行。"""
    q = (query or "").strip()
    if not q:
        return PlayerResolution("none")
    if not players:
        return PlayerResolution("error")
    cands = [
        hltv.SearchPlayer(
            str(p.get("player_id") or ""), str(p.get("nick") or ""), "", p.get("team"), None
        )
        for p in players
        if p.get("player_id") and p.get("nick")
    ]
    rank_by_id, rank_by_team = _player_rank_lookup(players)

    def rank_of(candidate: hltv.SearchPlayer) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_team.get(cf(candidate.team or ""))

    strong = [p for p in cands if nick_matches(q, p.nick)]
    if len(strong) > 1:
        strong = _tiebreak_exact(q, strong, lambda p: p.nick)
    if len(strong) == 1:
        return PlayerResolution("ok", player=strong[0])
    if len(strong) > 1:
        chosen, ordered = _choose_by_rank(strong, rank_of)
        if chosen is not None:
            return PlayerResolution("ok", player=chosen)
        return PlayerResolution("ambiguous", candidates=ordered[:8])
    return PlayerResolution("none", candidates=_suggest(q, cands, lambda p: p.nick))


async def resolve_player(
    fetcher, query: str, players: Optional[list[dict]] = None
) -> PlayerResolution:
    """把用户输入的选手名解析成唯一 HLTV 选手,或给出候选让其精确重发。"""
    q = (query or "").strip()
    if not q:
        return PlayerResolution("none")
    js = await fetcher.fetch_search(q)
    if js is None:
        return PlayerResolution("error")
    found_players, _ = hltv.parse_search(js)
    found_players = _dedup_players(found_players)
    if not found_players:
        return PlayerResolution("none")
    rank_by_id, rank_by_team = _player_rank_lookup(players)

    def rank_of(candidate: hltv.SearchPlayer) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_team.get(cf(candidate.team or ""))

    strong = [p for p in found_players if nick_matches(q, p.nick)]
    if len(strong) > 1:
        strong = _tiebreak_exact(q, strong, lambda p: p.nick)
    if len(strong) == 1:
        return PlayerResolution("ok", player=strong[0])
    if len(strong) > 1:
        chosen, ordered = _choose_by_rank(strong, rank_of)
        if chosen is not None:
            return PlayerResolution("ok", player=chosen)
        return PlayerResolution("ambiguous", candidates=ordered[:8])
    # 无精确命中:把搜索的模糊建议给用户,让其用昵称原形重发(绝不自动猜存)
    return PlayerResolution("ambiguous", candidates=found_players[:6])


async def resolve_team(fetcher, query: str, teams: Optional[list[dict]] = None) -> TeamResolution:
    """把用户输入的战队名解析成唯一 HLTV 战队,或给出候选。"""
    q = (query or "").strip()
    if not q:
        return TeamResolution("none")
    js = await fetcher.fetch_search(q)
    if js is None:
        return TeamResolution("error")
    _, found_teams = hltv.parse_search(js)
    found_teams = _dedup_teams(found_teams)
    if not found_teams:
        return TeamResolution("none")
    rank_by_id, rank_by_key = _team_rank_lookup(teams)

    def rank_of(candidate: hltv.SearchTeam) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_key.get(team_key(candidate.name))

    alias = _TEAM_ALIASES.get(cf(q))
    if alias:
        alias_hits = [t for t in found_teams if nick_matches(alias, t.name)]
        if len(alias_hits) == 1:
            return TeamResolution("ok", team=alias_hits[0])
        if len(alias_hits) > 1:
            chosen, ordered = _choose_by_rank(alias_hits, rank_of)
            if chosen is not None:
                return TeamResolution("ok", team=chosen)
            return TeamResolution("ambiguous", candidates=ordered[:8])
    strong = [t for t in found_teams if nick_matches(q, t.name)]
    if len(strong) > 1:
        strong = _tiebreak_exact(q, strong, lambda t: t.name)
    if len(strong) == 1:
        return TeamResolution("ok", team=strong[0])
    if len(strong) > 1:
        chosen, ordered = _choose_by_rank(strong, rank_of)
        if chosen is not None:
            return TeamResolution("ok", team=chosen)
        return TeamResolution("ambiguous", candidates=ordered[:8])
    return TeamResolution("ambiguous", candidates=found_teams[:6])
