"""HLTV 赛事收官数据独立解析模块。

纯 HTML 解析（零网络、零数据库依赖），输入赛事详情页 HTML（及可选的赛事统计页 HTML），
输出结构化 dict，便于直接用缓存 HTML fixture 进行毫秒级单元测试。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import names
from .hltv import (
    BASE,
    Html,
    HTMLParser,
    Node,
    _event_meta,
    _logo_variant_rank,
    _norm,
    _pick_logo,
    _txt,
    parse_brackets,
    tree,
)

_ENDED_STATUS_WORDS = frozenset({"finished", "ended", "completed", "event ended"})
_PLACE_NUM_RE = re.compile(r"^(\d+)")
_TEAM_ID_RE = re.compile(r"/team/(\d+)/")
_PLAYER_ID_RE = re.compile(r"/(?:stats/)?players?/(\d+)/")
_EVENT_ID_RE = re.compile(r"/events/(\d+)(?:/|$|\?)")
_QUOTED_NICK_RE = re.compile(r"'([^']+)'")
_RATING_NUM_RE = re.compile(r"\b([012]\.\d{2})\b")


def _img_url(img: Optional[Node]) -> Optional[str]:
    if img is None:
        return None
    u = img.attributes.get("data-cookieblock-src") or img.attributes.get("src") or ""
    u = u.strip()
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return f"{BASE}{u}"
    return u or None


def _pick_node_logo(node: Node, title: str = "") -> Optional[str]:
    """在一个局部容器内挑选最优队标（优先 day-only 变体，兼容 title 匹配）。"""
    best_u: Optional[str] = None
    best_rank = 99
    title_l = (title or "").lower()
    for img in node.css("img"):
        u = _img_url(img)
        if not u or ("teamlogo/" not in u and "eventlogo/" not in u):
            continue
        cls = img.attributes.get("class") or ""
        rank = _logo_variant_rank(cls)
        it = (img.attributes.get("title") or img.attributes.get("alt") or "").lower()
        if title_l and it and it != title_l:
            rank += 10
        if rank < best_rank:
            best_u = u
            best_rank = rank
    return best_u


def _extract_event_logo(t: HTMLParser, name: str) -> Optional[str]:
    exact = _pick_logo(t, "eventlogo", name, allow_fallback=False)
    if exact:
        return exact
    og = t.css_first("meta[property='og:image']")
    if og:
        content = (og.attributes.get("content") or "").strip()
        if "eventlogo/" in content:
            return content
    return _pick_logo(t, "eventlogo", name, allow_fallback=True)


def _extract_event_id_and_url(
    t: HTMLParser, event_id: str = "", event_url: str = ""
) -> tuple[str, str]:
    eid = str(event_id or "").strip()
    url = str(event_url or "").strip()
    canonical = ""
    can_node = t.css_first("link[rel='canonical']")
    if can_node:
        canonical = (can_node.attributes.get("href") or "").strip()
    if not canonical:
        hub_top = t.css_first("a.event-hub-top[href*='/events/']")
        if hub_top:
            href = (hub_top.attributes.get("href") or "").strip()
            canonical = f"{BASE}{href}" if href.startswith("/") else href
    if not canonical:
        sb = t.css_first("a.sidebar-single-line-item[href*='/events/']")
        if sb:
            href = (sb.attributes.get("href") or "").strip().split("#")[0]
            canonical = f"{BASE}{href}" if href.startswith("/") else href

    if not url and canonical:
        url = canonical
    if not eid:
        for candidate in (url, canonical):
            m = _EVENT_ID_RE.search(candidate)
            if m:
                eid = m.group(1)
                break
    if not url and eid:
        url = f"{BASE}/events/{eid}/x"
    elif url.startswith("/"):
        url = f"{BASE}{url}"
    return eid, url


def _is_event_ended(t: HTMLParser) -> tuple[bool, str]:
    ind = t.css_first(".event-hub-indicator")
    if ind is None:
        return False, ""
    cls = (ind.attributes.get("class") or "").lower()
    text = _txt(ind)
    tl = text.lower()
    if "event-ended" in cls or tl in _ENDED_STATUS_WORDS:
        return True, text or "Finished"
    return False, text


def _parse_rank_num(rank_text: str) -> int:
    m = _PLACE_NUM_RE.match((rank_text or "").strip())
    return int(m.group(1)) if m else 999


def _parse_placements(
    t: HTMLParser, team_logo_map: dict[str, str]
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """解析 .placements/.placement 区块，返回 (champion, top8_placements)。"""
    all_rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str]] = set()
    placement_nodes = t.css(".placements .placement") or t.css(".placements-holder .placement")
    for pl in placement_nodes:
        team_a = pl.css_first(".team a[href*='/team/'], .team a")
        team_name = _txt(team_a) if team_a else _txt(pl.css_first(".team"))
        if not team_name:
            continue
        href = (team_a.attributes.get("href") or "") if team_a else ""
        m_id = _TEAM_ID_RE.search(href)
        team_id = m_id.group(1) if m_id else ""

        rank_text = ""
        for ch in pl.iter():
            if ch.tag == "div" and not ch.attributes.get("class"):
                cand = _txt(ch)
                if cand and _PLACE_NUM_RE.match(cand):
                    rank_text = cand
                    break
        if not rank_text:
            pos_node = pl.css_first(".position, .placement-rank, .rank")
            rank_text = _txt(pos_node)
        rank_num = _parse_rank_num(rank_text)

        prize = ""
        for pr in pl.css(".prize"):
            pcls = (pr.attributes.get("class") or "").lower()
            if "club-share" in pcls or "spot-prizes" in pcls:
                continue
            ptxt = _txt(pr)
            if ptxt:
                prize = ptxt
                break
        if not prize:
            for pr in pl.css(".prize"):
                ptxt = _txt(pr)
                if ptxt:
                    prize = ptxt
                    break

        dedup_key = (rank_text, names.team_key(team_name))
        if dedup_key in seen_rows:
            continue
        seen_rows.add(dedup_key)
        logo_url = _pick_node_logo(pl, team_name) or team_logo_map.get(names.team_key(team_name))
        all_rows.append(
            {
                "rank": rank_text or f"#{len(all_rows) + 1}",
                "rank_num": rank_num,
                "team_name": team_name,
                "team_id": team_id,
                "logo_url": logo_url,
                "prize": prize,
            }
        )

    all_rows.sort(key=lambda item: item["rank_num"])
    top8 = [row for row in all_rows if row["rank_num"] <= 8][:8]

    champion: Optional[dict[str, Any]] = None
    for row in top8:
        if row["rank_num"] == 1:
            champion = dict(row)
            break

    # 若 .placements 无冠军或缺失，尝试从淘汰赛 Grand Final 赛果回退冠军队
    if champion is None:
        champion = _fallback_champion_from_brackets(t, team_logo_map)
    return champion, top8


def _fallback_champion_from_brackets(
    t: HTMLParser, team_logo_map: dict[str, str]
) -> Optional[dict[str, Any]]:
    try:
        brackets = parse_brackets(t, set())
    except Exception:  # noqa: BLE001
        return None
    for b in reversed(brackets):
        for rnd in reversed(b.all_rounds()):
            rn = (rnd.name or "").lower()
            if "grand" in rn or "final" in rn or rnd is b.all_rounds()[-1]:
                for mu in rnd.matchups:
                    if mu.finished and mu.winner in ("team1", "team2"):
                        win_team = mu.team1 if mu.winner == "team1" else mu.team2
                        if win_team.name:
                            return {
                                "rank": "1st",
                                "rank_num": 1,
                                "team_name": win_team.name,
                                "team_id": "",
                                "logo_url": win_team.logo
                                or team_logo_map.get(names.team_key(win_team.name)),
                                "prize": "",
                            }
    return None


def _parse_team_boxes(
    t: HTMLParser,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str], set[str], dict[str, str]]:
    """解析参赛战队区块 (.team-box)，返回:
    (teams, player_id->team_name, nick_lower->team_name, player_ids, team_key->logo_url)
    """
    teams: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    pid_to_team: dict[str, str] = {}
    nick_to_team: dict[str, str] = {}
    player_ids: set[str] = set()
    team_logo_map: dict[str, str] = {}

    for box in t.css(".team-box"):
        name_node = (
            box.css_first(".team-name .text")
            or box.css_first(".team-name a")
            or box.css_first(".team-name")
        )
        team_name = _txt(name_node)
        if not team_name:
            continue
        a_team = box.css_first(".team-name a[href*='/team/']")
        href = (a_team.attributes.get("href") or "") if a_team else ""
        m_id = _TEAM_ID_RE.search(href)
        team_id = m_id.group(1) if m_id else ""
        tkey = names.team_key(team_name)
        logo = _pick_node_logo(box, team_name)
        if logo and tkey not in team_logo_map:
            team_logo_map[tkey] = logo
        if tkey not in seen_keys:
            seen_keys.add(tkey)
            teams.append(
                {
                    "team_name": team_name,
                    "team_id": team_id,
                    "team_key": tkey,
                    "logo_url": logo,
                }
            )
        for pa in box.css(".lineup-box a[href*='/player/'], .player a[href*='/player/']"):
            phref = pa.attributes.get("href") or ""
            pnick = _txt(pa)
            mp = _PLAYER_ID_RE.search(phref)
            if mp:
                pid = mp.group(1)
                player_ids.add(pid)
                pid_to_team.setdefault(pid, team_name)
            if pnick:
                nick_to_team.setdefault(pnick.lower(), team_name)

    return teams, pid_to_team, nick_to_team, player_ids, team_logo_map


def _extract_nick(full_text: str, href: str = "") -> str:
    m = _QUOTED_NICK_RE.search(full_text or "")
    if m:
        return _norm(m.group(1))
    cleaned = _norm(full_text)
    if cleaned:
        return cleaned
    if href:
        seg = href.rstrip("/").split("/")[-1]
        if seg and not seg.isdigit():
            return seg
    return ""


def _parse_mvp(
    t: HTMLParser, pid_to_team: dict[str, str], nick_to_team: dict[str, str]
) -> Optional[dict[str, Any]]:
    box = t.css_first(".standard-box.mvp, .event-mvp, .mvp-box")
    if box is None:
        return None
    pa = box.css_first(".player-name a[href*='/player/'], a[href*='/player/']")
    if pa is None:
        return None
    href = (pa.attributes.get("href") or "").strip()
    m_pid = _PLAYER_ID_RE.search(href)
    player_id = m_pid.group(1) if m_pid else ""
    full_name = _txt(pa)
    nick = _extract_nick(full_name, href)
    if not nick:
        return None
    player_url = f"{BASE}{href}" if href.startswith("/") else href

    pic_img = box.css_first(".mvp-picture img, img.mvp-photo, img[src*='player'], img[src*='gallerypicture']")
    avatar_url = _img_url(pic_img)

    coin_img = box.css_first("img.mvp-coin")
    coin_src = ((coin_img.attributes.get("src") or "") if coin_img else "").lower()
    coin_type = "gold" if "gold" in coin_src else ("silver" if "silver" in coin_src else "")

    rating: Optional[float] = None
    rating_label = "Rating"
    graph = box.css_first(".chart .graph[data-chartjs-config], [data-chartjs-config]")
    if graph:
        raw_cfg = graph.attributes.get("data-chartjs-config") or ""
        try:
            chart_cfg = json.loads(raw_cfg)
            datasets = chart_cfg.get("data", {}).get("datasets", [])
            if datasets and isinstance(datasets, list):
                for item in datasets[0].get("data", []):
                    cat = str(item.get("category") or "")
                    if cat.lower().startswith("rating"):
                        rating_label = cat
                        val = item.get("value")
                        if val is None:
                            val = item.get("label")
                        if val is not None:
                            rating = float(val)
                        break
        except (ValueError, TypeError, KeyError):
            pass

    if rating is None:
        rnode = box.css_first(".mvp-rating, .rating-value, .rating")
        if rnode:
            mr = _RATING_NUM_RE.search(_txt(rnode))
            if mr:
                rating = float(mr.group(1))
    if rating is None:
        mr = re.search(r"Rating(?:\s*[23]\.0)?\s*[:：]?\s*([012]\.\d{2})", _txt(box), re.I)
        if mr:
            rating = float(mr.group(1))

    team_name = ""
    if player_id and player_id in pid_to_team:
        team_name = pid_to_team[player_id]
    elif nick.lower() in nick_to_team:
        team_name = nick_to_team[nick.lower()]
    else:
        tnode = box.css_first(".mvp-team, .team-name")
        team_name = _txt(tnode)

    return {
        "nick": nick,
        "full_name": full_name,
        "player_id": player_id,
        "player_url": player_url,
        "avatar_url": avatar_url,
        "team_name": team_name,
        "rating": rating,
        "rating_label": rating_label,
        "coin_type": coin_type,
    }


def _parse_float(text: str) -> Optional[float]:
    m = _RATING_NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_int(text: str) -> Optional[int]:
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def _parse_explicit_tote(
    t: HTMLParser, pid_to_team: dict[str, str], nick_to_team: dict[str, str]
) -> list[dict[str, Any]]:
    """解析显式 Team of the Event / All-Stars 区块（若页面存在）。"""
    container: Optional[Node] = t.css_first(
        ".team-of-the-event, .allstar-team, .all-star-team, [data-team-of-the-event]"
    )
    if container is None:
        for box in t.css(".standard-box, .stats-section, section"):
            head = box.css_first(".standard-headline, .box-headline, h2, h3")
            ht = _txt(head).lower()
            if "team of the event" in ht or "all-star" in ht or "allstar" in ht:
                container = box
                break
    if container is None:
        return []

    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    cards = container.css(".player-card, .allstar-player, .tote-player, .top-x-box, a[href*='/player/'], a[href*='/stats/players/']")
    for card in cards:
        pa = card if card.tag == "a" else card.css_first("a[href*='/player/'], a[href*='/stats/players/']")
        if pa is None:
            continue
        href = (pa.attributes.get("href") or "").strip()
        mp = _PLAYER_ID_RE.search(href)
        pid = mp.group(1) if mp else ""
        nick_node = card.css_first(".nick, .name, .player-nick, .evp-name-top") or pa
        nick = _extract_nick(_txt(nick_node), href)
        if not nick:
            continue
        dedup_key = pid or nick.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        rating = _parse_float(_txt(card.css_first(".rating, .rating-value, .stat-value"))) or _parse_float(_txt(card))
        role = _txt(card.css_first(".role, .player-role, .position"))
        avatar_url = _img_url(card.css_first("img[src*='player'], img.img, img"))
        team_name = _txt(card.css_first(".team-name, .team"))
        if not team_name:
            team_name = pid_to_team.get(pid, "") or nick_to_team.get(nick.lower(), "")
        players.append(
            {
                "nick": nick,
                "player_id": pid,
                "player_url": f"{BASE}{href}" if href.startswith("/") else href,
                "avatar_url": avatar_url,
                "team_name": team_name,
                "rating": rating,
                "maps": _parse_int(_txt(card.css_first(".maps, .average"))),
                "role": role,
            }
        )
        if len(players) >= 5:
            break
    return players


def _parse_stats_top_players(
    stats_t: HTMLParser, pid_to_team: dict[str, str], nick_to_team: dict[str, str]
) -> list[dict[str, Any]]:
    """从 /stats?event={id} 的 Top players 区块提取 Rating Top 5 选手。"""
    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    for box in stats_t.css(".top-x-box"):
        pa = box.css_first("a.name[href*='/stats/players/'], a[href*='/stats/players/'], a[href*='/player/']")
        if pa is None:
            continue
        href = (pa.attributes.get("href") or "").strip()
        mp = _PLAYER_ID_RE.search(href)
        pid = mp.group(1) if mp else ""
        nick = _txt(pa)
        if not nick:
            continue
        dedup_key = pid or nick.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        rating_node = box.css_first(".rating .bold") or box.css_first(".rating")
        rating = _parse_float(_txt(rating_node))
        maps_node = box.css_first(".average .bold") or box.css_first(".average")
        maps = _parse_int(_txt(maps_node))
        avatar_url = _img_url(box.css_first(".playerPicture img, img.img"))
        team_name = pid_to_team.get(pid, "") or nick_to_team.get(nick.lower(), "")
        players.append(
            {
                "nick": nick,
                "player_id": pid,
                "player_url": f"{BASE}{href}" if href.startswith("/") else href,
                "avatar_url": avatar_url,
                "team_name": team_name,
                "rating": rating,
                "maps": maps,
                "role": "",
            }
        )
        if len(players) >= 5:
            break
    return players


def _parse_evp_players(
    t: HTMLParser, pid_to_team: dict[str, str], nick_to_team: dict[str, str]
) -> list[dict[str, Any]]:
    """从赛事页 .standard-box.evp 提取至多 5 名 EVP 选手作为最佳阵容兜底。"""
    evp_box = t.css_first(".standard-box.evp")
    if evp_box is None:
        return []
    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in evp_box.css(".evp-wrapper, a[href*='/player/']"):
        href = (a.attributes.get("href") or "").strip()
        mp = _PLAYER_ID_RE.search(href)
        pid = mp.group(1) if mp else ""
        nick = _txt(a.css_first(".evp-name-top")) or _extract_nick(_txt(a), href)
        if not nick:
            continue
        dedup_key = pid or nick.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        rating = _parse_float(_txt(a.css_first(".evp-rating-number")))
        avatar_url = _img_url(a.css_first(".evp-image-container img, img"))
        team_name = pid_to_team.get(pid, "") or nick_to_team.get(nick.lower(), "")
        players.append(
            {
                "nick": nick,
                "player_id": pid,
                "player_url": f"{BASE}{href}" if href.startswith("/") else href,
                "avatar_url": avatar_url,
                "team_name": team_name,
                "rating": rating,
                "maps": None,
                "role": "EVP",
            }
        )
        if len(players) >= 5:
            break
    return players


def parse_event_finale(
    event_html: Html,
    *,
    stats_html: Optional[Html] = None,
    event_id: str = "",
    event_url: str = "",
) -> dict[str, Any]:
    """解析 HLTV 赛事页（+ 可选 stats 页）为收官卡结构化 dict。

    任一区块缺失时绝不抛出异常，而是记录到 ``missing_sections`` 并设置
    ``degraded=True``，供上层决定宽限重试或降级渲染。
    """
    t = tree(event_html)
    meta = _event_meta(t)
    name = meta.get("name") or ""
    if not name:
        og_title = t.css_first("meta[property='og:title']")
        if og_title:
            name = _norm((og_title.attributes.get("content") or "").split("|")[0])
        if not name:
            name = _txt(t.css_first("h1"))
    eid, eurl = _extract_event_id_and_url(t, event_id=event_id, event_url=event_url)
    ended, status_text = _is_event_ended(t)
    event_logo = _extract_event_logo(t, name) or meta.get("logo")

    teams, pid_to_team, nick_to_team, player_ids, team_logo_map = _parse_team_boxes(t)
    champion, placements = _parse_placements(t, team_logo_map)

    # 把名次表里出现但 .team-box 未收录的队也并入参赛队列表
    seen_tkeys = {item["team_key"] for item in teams}
    for row in placements:
        tkey = names.team_key(row["team_name"])
        if tkey and tkey not in seen_tkeys:
            seen_tkeys.add(tkey)
            teams.append(
                {
                    "team_name": row["team_name"],
                    "team_id": row["team_id"],
                    "team_key": tkey,
                    "logo_url": row["logo_url"],
                }
            )

    mvp = _parse_mvp(t, pid_to_team, nick_to_team)
    if mvp and mvp["player_id"]:
        player_ids.add(mvp["player_id"])

    stats_t = tree(stats_html) if stats_html else None
    tote_source = ""
    tote = _parse_explicit_tote(t, pid_to_team, nick_to_team)
    if tote:
        tote_source = "team_of_the_event"
    elif stats_t is not None:
        tote = _parse_explicit_tote(stats_t, pid_to_team, nick_to_team)
        if tote:
            tote_source = "team_of_the_event"
        else:
            tote = _parse_stats_top_players(stats_t, pid_to_team, nick_to_team)
            if tote:
                tote_source = "stats_top_players"
    if not tote:
        tote = _parse_evp_players(t, pid_to_team, nick_to_team)
        if tote:
            tote_source = "evp"

    for p in tote:
        if p.get("player_id"):
            player_ids.add(p["player_id"])

    missing_sections: list[str] = []
    if champion is None:
        missing_sections.append("champion")
    if len(placements) < 2:
        missing_sections.append("placements")
    if mvp is None:
        missing_sections.append("mvp")
    if not tote:
        missing_sections.append("team_of_the_event")

    return {
        "event_id": eid,
        "event_url": eurl,
        "ended": ended,
        "event": {
            "id": eid,
            "name": name,
            "date_text": meta.get("date_text") or "",
            "prize": meta.get("prize") or "",
            "location": meta.get("location") or "",
            "logo_url": event_logo,
            "url": eurl,
            "status": status_text or meta.get("status") or "",
        },
        "champion": champion,
        "placements": placements,
        "mvp": mvp,
        "team_of_the_event": tote,
        "team_of_the_event_source": tote_source,
        "teams": teams,
        "player_ids": sorted(player_ids),
        "missing_sections": missing_sections,
        "degraded": bool(missing_sections),
    }


def needs_stats_fetch(parsed: dict[str, Any]) -> bool:
    """判断已完赛解析结果是否仍需补抓 /stats?event={id} 以获取最佳阵容。"""
    if not parsed.get("ended"):
        return False
    src = parsed.get("team_of_the_event_source") or ""
    tote = parsed.get("team_of_the_event") or []
    return src not in ("team_of_the_event", "stats_top_players") or len(tote) < 5
