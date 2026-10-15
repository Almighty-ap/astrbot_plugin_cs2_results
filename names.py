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


# 战队俗称/缩写/中文别名 → HLTV 正式队名
_TEAM_ALIASES: dict[str, str] = {
    # Vitality
    "小蜜蜂": "Vitality",
    "小峰": "Vitality",
    "蜜蜂": "Vitality",
    "vita": "Vitality",
    "vitality": "Vitality",
    "team vitality": "Vitality",
    # Spirit
    "绿龙": "Spirit",
    "雪碧": "Spirit",
    "ts": "Spirit",
    "spirit": "Spirit",
    "team spirit": "Spirit",
    # FaZe
    "银河战舰": "FaZe",
    "法则": "FaZe",
    "faze": "FaZe",
    "faze clan": "FaZe",
    # Natus Vincere
    "navi": "Natus Vincere",
    "na'vi": "Natus Vincere",
    "黄黑军团": "Natus Vincere",
    "natus vincere": "Natus Vincere",
    # MOUZ
    "老鼠": "MOUZ",
    "老鼠队": "MOUZ",
    "mouz": "MOUZ",
    "mousesports": "MOUZ",
    # G2
    "g2": "G2",
    "武士": "G2",
    "g2 esports": "G2",
    # Virtus.pro
    "vp": "Virtus.pro",
    "virtus pro": "Virtus.pro",
    "virtus.pro": "Virtus.pro",
    # Astralis
    "a队": "Astralis",
    "ast": "Astralis",
    "astralis": "Astralis",
    # Ninjas in Pyjamas
    "nip": "Ninjas in Pyjamas",
    "睡衣忍者": "Ninjas in Pyjamas",
    "忍者": "Ninjas in Pyjamas",
    "ninjas in pyjamas": "Ninjas in Pyjamas",
    # FURIA
    "黑豹": "FURIA",
    "巴西黑豹": "FURIA",
    "furia": "FURIA",
    "furia esports": "FURIA",
    # The MongolZ
    "mongolz": "The MongolZ",
    "the mongolz": "The MongolZ",
    "蒙古": "The MongolZ",
    "蒙古队": "The MongolZ",
    # TYLOO
    "天禄": "TYLOO",
    "天禄队": "TYLOO",
    "tyloo": "TYLOO",
    # Lynn Vision
    "lvg": "Lynn Vision",
    "lynn vision": "Lynn Vision",
    # HEROIC
    "x队": "HEROIC",
    "英雄": "HEROIC",
    "heroic": "HEROIC",
    # Complexity
    "col": "Complexity",
    "蓝星": "Complexity",
    # Eternal Fire
    "ef": "Eternal Fire",
    "永恒之火": "Eternal Fire",
    "土耳其": "Eternal Fire",
    "eternal fire": "Eternal Fire",
    # Liquid
    "液体": "Liquid",
    "tl": "Liquid",
    "team liquid": "Liquid",
    "liquid": "Liquid",
    # Cloud9
    "c9": "Cloud9",
    "cloud9": "Cloud9",
    # Falcons
    "猎鹰": "Falcons",
    "falcons": "Falcons",
    "team falcons": "Falcons",
    # 3DMAX
    "3d": "3DMAX",
    "3dmax": "3DMAX",
    # Rare Atom
    "ra": "Rare Atom",
    "稀有原子": "Rare Atom",
    "rare atom": "Rare Atom",
    # Steel Helmet
    "钢盔": "Steel Helmet",
    "steel helmet": "Steel Helmet",
    # fnatic
    "fnatic": "fnatic",
    "橙黑军团": "fnatic",
    # BIG
    "big": "BIG",
    # ENCE
    "ence": "ENCE",
    # GamerLegion
    "gl": "GamerLegion",
    "gamerlegion": "GamerLegion",
    # paiN
    "pain": "paiN",
    "pain gaming": "paiN",
    # Imperial
    "帝国": "Imperial",
    "imperial": "Imperial",
    # Aurora
    "极光": "Aurora",
    "aurora": "Aurora",
    "欧若拉": "Aurora",
    # M80
    "m80": "M80",
    # FlyQuest
    "flyquest": "FlyQuest",
    "绿叶": "FlyQuest",
    "灰狗": "FlyQuest",
    # Wildcard
    "wildcard": "Wildcard",
    "外卡": "Wildcard",
    # PARIVISION
    "parivision": "PARIVISION",
    "pv": "PARIVISION",    
    # BetBoom
    "bb": "BetBoom",
    "betboom": "BetBoom",
    # AMKAL
    "amkal": "AMKAL",
    # Sashi
    "sashi": "Sashi",
    # OG
    "og": "OG",
    # SAW
    "saw": "SAW",
    # B8
    "b8": "B8",
}

# 选手外号/中文俗称 → HLTV 正式选手昵称
_PLAYER_ALIASES: dict[str, str] = {
    # ZywOo
    "载物": "ZywOo",
    "贼物": "ZywOo",
    "大裂谷": "ZywOo",
    "番茄": "ZywOo",
    "zywoo": "ZywOo",
    # s1mple
    "森破": "s1mple",
    "简单男孩": "s1mple",
    "simple": "s1mple",
    "s1": "s1mple",
    "s1mple": "s1mple",
    # m0NESY
    "小孩": "m0NESY",
    "孩贼王": "m0NESY",
    "太子": "m0NESY",
    "孩神": "m0NESY",
    "海参": "m0NESY",
    "monesy": "m0NESY",
    "m0nesy": "m0NESY",
    # karrigan
    "大表哥": "karrigan",
    "卡利甘": "karrigan",
    "karrigan": "karrigan",
    "刀锋女王": "karrigan",
    "千早爱烟": "karrigan",
    "凯圣帝君": "karrigan",
    # Twistzz
    "总监": "Twistzz",
    "发型总监": "Twistzz",
    "扭扭": "Twistzz",
    "twistzz": "Twistzz",
    "监哥": "Twistzz",    
    "弓箭手": "Twistzz",    
    "贼贼": "Twistzz",
    # rain
    "雨神": "rain",
    "rain": "rain",
    # Ex3rcice
    "ex3": "Ex3rcice",
    "exercise": "Ex3rcice",
    "练习哥": "Ex3rcice",
    "练*哥": "Ex3rcice",
    # donk
    "洞克": "donk",
    "东克": "donk",
    "绿龙大爹": "donk",
    "donk": "donk",
    "狗洞": "donk",
    "洞可": "donk",
    # sh1ro
    "息肉": "sh1ro",
    "若子": "sh1ro",
    "shiro": "sh1ro",
    "细弱": "sh1ro",
    "sh1ro": "sh1ro",
    # cadiaN
    "cadian": "cadiaN",
    "点子": "cadiaN",
    "点圣": "cadiaN",
    "出生点": "cadiaN",
    # broky
    "箱子": "broky",
    "布罗基": "broky",
    "小箱": "broky",
    "箱神": "broky",
    "broky": "broky",
    "切除": "broky",
    # ropz
    "肉铺子": "ropz",
    "车主": "ropz",
    "罗宾": "ropz",
    "罗斌": "ropz",
    "ropz": "ropz",
    "911": "ropz",
    # frozen
    "寒冰": "frozen",
    "冰雪奇缘": "frozen",
    "frozen": "frozen",
    "寒王": "frozen",
    # b1t
    "大b哥": "b1t",
    "大b": "b1t",
    "b1t": "b1t",
    "bit": "b1t",
    "bot": "b1t",
    # jL
    "jl": "jL",
    # iM
    "阿努比斯跑打": "iM",
    "im": "iM",
    "pm": "iM",
    "pm2.5": "iM",
    # Aleksib
    "小李子": "Aleksib",
    "李神": "Aleksib",
    "李子": "Aleksib",
    "处理": "Aleksib",
    "aleksib": "Aleksib",
    # zweih
    "紫薇": "zweih",
    "哲伟": "zweih",
    # w0nderful
    "玩的福": "w0nderful",
    "王德发": "w0nderful",
    "wonderful": "w0nderful",
    "w0nderful": "w0nderful",
    "pdf": "w0nderful",
    # chopper
    "大超": "chopper",
    "天道超": "chopper",
    "chopper": "chopper",
    # zont1x
    "宗主": "zont1x",
    "宗宗": "zont1x",
    "zont1x": "zont1x",
    "啊啊啊啊": "zont1x",
    # device
    "阿汤哥": "device",
    "阿汤": "device",
    "玩机爸": "device",
    "device": "device",
    "流脓": "device",
    "人类极限": "device",
    "dev1ce": "device",
    # NiKo
    "尼公子": "NiKo",
    "尼古拉科维奇": "NiKo",
    "小科维奇": "NiKo",
    "niko": "NiKo",
    "大虾": "NiKo",
    "软脚虾": "NiKo",
    "夜店王子": "NiKo",
    "老尼": "NiKo",
    "我尼": "NiKo",
    "虾哥": "NiKo",
    # huNter-
    "表哥": "huNter-",
    "猎人": "huNter-",
    "hunter": "huNter-",
    "hunter-": "huNter-",
    # Snax
    "白熊": "Snax",
    "snax": "Snax",
    # malbsMd
    "马尔布斯": "malbsMd",
    "危地马拉战神": "malbsMd",
    "malbsmd": "malbsMd",
    "malbs": "malbsMd",
    # torzsi
    "托子": "torzsi",
    "托神": "torzsi",
    "托出": "torzsi",
    "torzsi": "torzsi",
    # xertioN
    "谢逊": "xertioN",
    "xertion": "xertioN",
    # siuhy
    "修嗨": "siuhy",
    "小夫": "siuhy",
    "索嗨": "siuhy",
    "苏一": "siuhy",
    "休一": "siuhy",
    "siuhy": "siuhy",
    # Brollan
    "宝蓝": "Brollan",
    "brollan": "Brollan",    
    # Jame
    "上帝": "Jame",
    "大詹": "Jame",
    "jame": "Jame",
    # FL1T
    "大天使长": "FL1T",
    "fl1t": "FL1T",
    # fame
    "菲姆": "fame",
    "fame": "fame",
    # n0rb3r7
    "诺伯特": "n0rb3r7",
    "诺博": "n0rb3r7",
    "norbert": "n0rb3r7",
    "n0rb3r7": "n0rb3r7",
    # tabseN
    "元首": "tabseN",
    # 国内知名选手
    "小鬼": "somebody",
    "孙宝迪": "somebody",
    "somebody": "somebody",
    "白菜": "Attacker",
    "attacker": "Attacker",
    "蛋神": "Danking",
    "蛋出": "Danking",
    "中门载物杀手": "Danking",
    "danking": "Danking",
    "茄子": "qz",
    "qz": "qz",
    "老队长": "captainMo",
    "mo": "captainMo",
    "259": "advent",
    "小鸡": "advent",
    "advent": "advent",
    "果酱年轻": "JamYoung",
    "杨易": "JamYoung",
    "jamyoung": "JamYoung",
    "西瓜": "westmelon",
    "westmelon": "westmelon",
    "大舅子": "Starry",
    "国产niko": "Starry",
    "starry": "Starry",
    "勾哥": "Jee",
    "jee": "Jee",
    "勾ee": "Jee",
    "pee": "Jee",
    "莫西雨": "Moseyuh",
    "moseyuh": "Moseyuh",
    # XANTARES
    "狠人": "XANTARES",
    "土耳其雨姐": "XANTARES",
    "xantares": "XANTARES",
    # woxic
    "左手": "woxic",
    "woxic": "woxic",
    # MAJ3R
    "major": "MAJ3R",
    "maj3r": "MAJ3R",
    # Wicadia
    "维卡迪亚": "Wicadia",
    "wicadia": "Wicadia",
    "礼神": "Wicadia",
    # electroNic
    "电子哥": "electroNic",
    "电子": "electroNic",
    "electronic": "electroNic",
    # Perfecto
    "p元帅": "Perfecto",
    "perfecto": "Perfecto",
    # Boombl4
    "胖球": "Boombl4",
    "boombl4": "Boombl4",
    # EliGE
    "鸡哥": "EliGE",
    "elige": "EliGE",
    # NAF
    "树懒": "NAF",
    "naf": "NAF",
    # apEX
    "豆豆": "apEX",
    "憨豆": "apEX",
    "apex": "apEX",
    "豆战胜佛": "apEX",
    # Spinx
    "阿斌": "Spinx",
    "斯宾克斯": "Spinx",
    "spinx": "Spinx",
    # flameZ
    "火仔": "flameZ",
    "flamez": "flameZ",
    # mezii
    "妹子": "mezii",
    "奶爸": "mezii",
    "mezii": "mezii",
    # magixx
    "马西西": "magixx",
    "magixx": "magixx",
    # stavn
    "蛇": "stavn",
    "stavn": "stavn",
    "狗蛇": "stavn",
    "小天使": "stavn",
    "小狗使": "stavn",
    # jabbi
    "鼠": "jabbi",
    "jabbi": "jabbi",
    # Staehr
    "野榜": "Staehr",
    "staehr": "Staehr",
    # blameF
    "大壮": "blameF",
    "blamef": "blameF",
    # k0nfig
    "k0nfig": "k0nfig",
    # SunPayus
    "太阳神": "SunPayus",
    "sunpayus": "SunPayus",
    # Maden
    "马登": "Maden",
    "maden": "Maden",
    # TeSeS
    "龙哥": "TeSeS",
    "龙": "TeSeS",
    "神龙": "TeSeS",
    "teses": "TeSeS",
    # sjuush
    "术士": "sjuush",
    "sjuush": "sjuush",
    # degster
    "拍拍": "degster",
    "拍拍熊": "degster",
    "degster": "degster",
    # kyxsan
    "k3": "kyxsan",
    "kyxsan": "kyxsan",
    # NertZ
    "nertz": "NertZ",
    # Senzu
    "森祖": "Senzu",
    "senzu": "Senzu",
    # Techno
    "4k": "Techno",
    "techno": "Techno",
    "techno4k": "Techno",
    # bLitz
    "布里茨": "bLitz",
    "blitz": "bLitz",
    # Hallzerk
    "护工": "Hallzerk",
    "大汉": "Hallzerk",
    "hallzerk": "Hallzerk",
    # JT
    "jt": "JT",
    "南非常规大师": "JT",
}

# 确保所有 key 均经 cf 归一
_TEAM_ALIASES = {cf(k): v for k, v in _TEAM_ALIASES.items()}
_PLAYER_ALIASES = {cf(k): v for k, v in _PLAYER_ALIASES.items()}
TEAM_ALIASES = _TEAM_ALIASES
PLAYER_ALIASES = _PLAYER_ALIASES


def canonical_team(name: str) -> str:
    """把战队别名/俗称转为 HLTV 官方规范队名,未收录则返回原输入。"""
    q = cf(name)
    return _TEAM_ALIASES.get(q, (name or "").strip())


def canonical_player(nick: str) -> str:
    """把选手外号/中文俗称转为 HLTV 官方规范昵称,未收录则返回原输入。"""
    q = cf(nick)
    return _PLAYER_ALIASES.get(q, (nick or "").strip())


def target_matches(query: str, display: str, kind: str = "team") -> bool:
    """检查用户输入的 query 是否命中已有订阅的目标(支持大小写、leet 还原及中英文俗称/外号)。"""
    q = cf(query)
    disp = cf(display)
    if not q or not disp:
        return False
    if q == disp or nick_matches(query, display):
        return True
    if kind == "team":
        canon = cf(canonical_team(query))
        if canon and (canon == disp or nick_matches(canon, display)):
            return True
        variants = team_alias_variants(display)
        if q in variants:
            return True
    elif kind == "player":
        canon = cf(canonical_player(query))
        if canon and (canon == disp or nick_matches(canon, display)):
            return True
        variants = player_alias_variants(display)
        if q in variants:
            return True
    return False


def team_matches_filter(filter_str: str, team_name: str) -> bool:
    """判断战队名 team_name 是否命中过滤词 filter_str (支持子串包含与别名归一化)。"""
    q = cf(filter_str)
    t = cf(team_name)
    if not q or not t:
        return False
    if q in t:
        return True
    canon_q = cf(canonical_team(filter_str))
    if canon_q and canon_q in t:
        return True
    variants = team_alias_variants(team_name)
    if any(q in v or v in q for v in variants):
        return True
    return False


def team_alias_variants(name: str) -> set[str]:
    """Return aliases that resolve to an HLTV team's formal name."""
    formal = cf(name)
    return {
        alias
        for alias, target in _TEAM_ALIASES.items()
        if cf(target) == formal
    }


def player_alias_variants(nick: str) -> set[str]:
    """Return aliases that resolve to an HLTV player's formal nick."""
    formal = cf(nick)
    return {
        alias
        for alias, target in _PLAYER_ALIASES.items()
        if cf(target) == formal
    }


def _suggest_teams(query: str, items: list[hltv.SearchTeam], limit: int = 6) -> list[hltv.SearchTeam]:
    """错拼/漏拼时的战队就近建议。"""
    q, dq = cf(query), deleet(query)
    if not q:
        return []
    out: list[hltv.SearchTeam] = []
    seen: set[str] = set()
    for it in items:
        n = it.name
        if q in cf(n) or dq in deleet(n):
            out.append(it)
            seen.add(it.id)
            if len(out) >= limit:
                return out
    if len(q) >= 2:
        for alias_key, target in _TEAM_ALIASES.items():
            if q in alias_key:
                for it in items:
                    if it.id not in seen and nick_matches(target, it.name):
                        out.append(it)
                        seen.add(it.id)
                        if len(out) >= limit:
                            return out
    return out


def _suggest_players(query: str, items: list[hltv.SearchPlayer], limit: int = 6) -> list[hltv.SearchPlayer]:
    """错拼/漏拼时的选手就近建议。"""
    q, dq = cf(query), deleet(query)
    if not q:
        return []
    out: list[hltv.SearchPlayer] = []
    seen: set[str] = set()
    for it in items:
        n = it.nick
        if q in cf(n) or dq in deleet(n):
            out.append(it)
            seen.add(it.id)
            if len(out) >= limit:
                return out
    if len(q) >= 2:
        for alias_key, target in _PLAYER_ALIASES.items():
            if q in alias_key:
                for it in items:
                    if it.id not in seen and nick_matches(target, it.nick):
                        out.append(it)
                        seen.add(it.id)
                        if len(out) >= limit:
                            return out
    return out


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
    return TeamResolution("none", candidates=_suggest_teams(q, cands))


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

    # 外号/俗称先行:唯一命中直接返回
    alias = _PLAYER_ALIASES.get(cf(q))
    if alias:
        hit = [p for p in cands if nick_matches(alias, p.nick)]
        if len(hit) == 1:
            return PlayerResolution("ok", player=hit[0])
        if len(hit) > 1:
            chosen, ordered = _choose_by_rank(hit, rank_of)
            if chosen is not None:
                return PlayerResolution("ok", player=chosen)
            return PlayerResolution("ambiguous", candidates=ordered[:8])

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
    return PlayerResolution("none", candidates=_suggest_players(q, cands))


async def resolve_player(
    fetcher, query: str, players: Optional[list[dict]] = None
) -> PlayerResolution:
    """把用户输入的选手名解析成唯一 HLTV 选手,或给出候选让其精确重发。"""
    q = (query or "").strip()
    if not q:
        return PlayerResolution("none")
    alias = _PLAYER_ALIASES.get(cf(q))
    # 若命中外号且原查询含有非 ASCII 字符(如中文),直接以规范昵称请求 HLTV;否则先搜 q
    search_term = alias if (alias and not q.isascii()) else q
    js = await fetcher.fetch_search(search_term)
    if js is None:
        return PlayerResolution("error")
    found_players, _ = hltv.parse_search(js)
    found_players = _dedup_players(found_players)
    # 若原词为 ASCII 但搜空,且有 alias,尝试用 alias 二次检索
    if not found_players and alias and search_term != alias:
        js2 = await fetcher.fetch_search(alias)
        if js2 is not None:
            found_players, _ = hltv.parse_search(js2)
            found_players = _dedup_players(found_players)
    if not found_players:
        return PlayerResolution("none")
    rank_by_id, rank_by_team = _player_rank_lookup(players)

    def rank_of(candidate: hltv.SearchPlayer) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_team.get(cf(candidate.team or ""))

    if alias:
        alias_hits = [p for p in found_players if nick_matches(alias, p.nick)]
        if len(alias_hits) == 1:
            return PlayerResolution("ok", player=alias_hits[0])
        if len(alias_hits) > 1:
            chosen, ordered = _choose_by_rank(alias_hits, rank_of)
            if chosen is not None:
                return PlayerResolution("ok", player=chosen)
            return PlayerResolution("ambiguous", candidates=ordered[:8])

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
    alias = _TEAM_ALIASES.get(cf(q))
    # 若命中俗称且原查询含有非 ASCII 字符(如中文),直接以规范队名请求 HLTV;否则先搜 q
    search_term = alias if (alias and not q.isascii()) else q
    js = await fetcher.fetch_search(search_term)
    if js is None:
        return TeamResolution("error")
    _, found_teams = hltv.parse_search(js)
    found_teams = _dedup_teams(found_teams)
    # 若原词为 ASCII 但搜空,且有 alias,尝试用 alias 二次检索
    if not found_teams and alias and search_term != alias:
        js2 = await fetcher.fetch_search(alias)
        if js2 is not None:
            _, found_teams = hltv.parse_search(js2)
            found_teams = _dedup_teams(found_teams)
    if not found_teams:
        return TeamResolution("none")
    rank_by_id, rank_by_key = _team_rank_lookup(teams)

    def rank_of(candidate: hltv.SearchTeam) -> Optional[int]:
        return rank_by_id.get(candidate.id) or rank_by_key.get(team_key(candidate.name))

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
