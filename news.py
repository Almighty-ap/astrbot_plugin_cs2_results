"""HLTV RSS news polling, translation, rendering, and delivery."""

from __future__ import annotations

import asyncio
import email.utils
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, Image, Plain

from . import render as card
from . import store
from .news_entities import NewsEntityMatcher

if TYPE_CHECKING:
    from astrbot.api.star import Context

    from .config import Config
    from .fetcher import Fetcher

RSS_URL_DEFAULT = "https://www.hltv.org/rss/news"
CST = timezone(timedelta(hours=8))
MEDIA_NS = "{http://search.yahoo.com/mrss/}"

CATEGORY_RULES = (
    (r"\b(bench(?:es|ed)?|demot(?:e|es|ed)|stand-?ins?)\b", "下放/替补"),
    (r"\b(leave|leaves|left|departs?|parts? ways|releas(?:e|es|ed))\b", "离队"),
    (
        r"\b(sign(?:s|ed|ing)?|join(?:s|ed)?|acquir(?:e|es|ed)|"
        r"promot(?:e|es|ed)|returns?|completes? the roster)\b",
        "签约/加入",
    ),
)

TRANSLATE_PROMPT = (
    "你是 CS 电竞资讯翻译助手。请把下面的 HLTV 新闻标题和摘要翻译成自然流畅的中文,"
    "并做简要总结。\n"
    "输出格式(严格遵守):\n"
    "标题:<中文标题>\n"
    "总结:<2~3 句中文总结>\n"
    "要求:\n"
    "- 选手 ID、战队名、赛事名保留英文原文\n"
    "- 不要编造原文没有的信息\n"
    "- 不要输出任何多余内容\n\n"
    "新闻标题:{title}\n"
    "新闻摘要:{description}"
)


@dataclass(frozen=True)
class NewsItem:
    guid: str
    title: str
    description: str
    link: str
    image_url: str
    pub_time: Optional[datetime] = None


def _node_text(node: ET.Element, tag: str) -> str:
    element = node.find(tag)
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rss(xml_text: str) -> list[NewsItem]:
    """Parse an HLTV RSS feed while preserving source order."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("[cs2.news] RSS 解析失败: %s", exc)
        return []

    items: list[NewsItem] = []
    for node in root.iter("item"):
        guid = _node_text(node, "guid") or _node_text(node, "link")
        if not guid:
            continue
        media = node.find(f"{MEDIA_NS}content")
        pub_time: Optional[datetime] = None
        raw_date = _node_text(node, "pubDate")
        if raw_date:
            try:
                pub_time = email.utils.parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                pub_time = None
        items.append(
            NewsItem(
                guid=guid,
                title=_clean_text(_node_text(node, "title")),
                description=_clean_text(_node_text(node, "description")),
                link=_node_text(node, "link"),
                image_url=(media.get("url") or "").strip() if media is not None else "",
                pub_time=pub_time,
            )
        )
    return items


def parse_llm_output(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    title_match = re.search(r"标题[:：]\s*(.+)", text)
    summary_match = re.search(r"总结[:：]\s*([\s\S]+)", text)
    title = title_match.group(1).strip() if title_match else ""
    summary = summary_match.group(1).strip() if summary_match else text.strip()
    return title, summary


def news_category(item: NewsItem) -> str:
    text = f"{item.title} {item.description}".lower()
    for pattern, label in CATEGORY_RULES:
        if re.search(pattern, text):
            return label
    return "综合"


def format_news_time(pub_time: Optional[datetime]) -> str:
    if pub_time is None:
        return "时间未知"
    if pub_time.tzinfo is None:
        pub_time = pub_time.replace(tzinfo=timezone.utc)
    return pub_time.astimezone(CST).strftime("%Y-%m-%d %H:%M") + " 北京时间"


class NewsService:
    def __init__(self, cfg: Config, fetcher: Fetcher, context: Context) -> None:
        self.cfg = cfg
        self.fetcher = fetcher
        self.context = context

    async def poll_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            if self.cfg.cs2_news_enabled:
                try:
                    await self.check_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("[cs2.news] 轮询失败: %s", exc)
            await asyncio.sleep(self.cfg.cs2_news_poll_interval * 60)

    async def fetch_items(self) -> list[NewsItem]:
        xml_text = await self.fetcher.fetch_impersonated_text(
            self.cfg.cs2_news_rss_url or RSS_URL_DEFAULT,
            accept="application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            priority="scan",
        )
        if not xml_text:
            return []
        return parse_rss(xml_text)

    async def latest_item(self) -> Optional[NewsItem]:
        items = await self.fetch_items()
        if not items:
            return None
        return max(
            items,
            key=lambda item: item.pub_time or datetime.min.replace(tzinfo=timezone.utc),
        )

    async def check_once(self) -> list[NewsItem]:
        """Poll once, mark new GUIDs, render and push cards to subscribers."""
        items = await self.fetch_items()
        if not items:
            return []

        seen = set(store.news_seen_guids())
        initialized = store.news_initialized()
        new_items = [item for item in items if item.guid not in seen]
        if not initialized:
            store.news_mark_seen(
                [item.guid for item in items],
                keep=self.cfg.cs2_news_max_seen,
                initialized=True,
            )
            logger.info("[cs2.news] 首次运行,记录 %s 条历史资讯但不推送", len(items))
            return []
        if not new_items:
            return []

        new_items.sort(
            key=lambda item: item.pub_time or datetime.min.replace(tzinfo=timezone.utc)
        )
        store.news_mark_seen(
            [item.guid for item in new_items],
            keep=self.cfg.cs2_news_max_seen,
            initialized=True,
        )

        subscribers = store.news_subscribers()
        if not subscribers:
            logger.info("[cs2.news] 发现 %s 条新资讯,但没有订阅会话", len(new_items))
            return new_items

        to_push = new_items[-self.cfg.cs2_news_max_push_per_poll :]
        matcher = self._build_matcher() if self._mention_enabled() else None
        for item in to_push:
            await self.push_item(item, subscribers, matcher=matcher)
        return to_push

    async def push_item(
        self,
        item: NewsItem,
        subscribers: list[str],
        *,
        matcher: NewsEntityMatcher | None = None,
    ) -> None:
        try:
            rendered = await self.render_item(item)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[cs2.news] 资讯卡片渲染失败,退化为文本: %s", exc)
            rendered = None
        matcher = matcher or (self._build_matcher() if self._mention_enabled() else None)
        for umo in subscribers:
            try:
                mentions = self.mentions_for_item(
                    item,
                    self._group_id_from_umo(umo),
                    matcher=matcher,
                )
                components = [At(qq=qq) for qq in mentions]
                if rendered is None:
                    components.append(Plain(self._fallback_text(item)))
                else:
                    components.append(Image.fromBytes(rendered))
                chain = MessageChain(chain=components)
                await self.context.send_message(umo, chain)
            except Exception as exc:  # noqa: BLE001
                logger.error("[cs2.news] 推送失败 %s: %s", umo, exc)

    def _mention_enabled(self) -> bool:
        return (
            self.cfg.cs2_news_mention_subscriptions
            and self.cfg.cs2_news_mention_max_per_group > 0
        )

    @staticmethod
    def _group_id_from_umo(unified_msg_origin: str) -> int:
        parts = str(unified_msg_origin or "").split(":", 2)
        if len(parts) != 3 or parts[1].casefold() != "groupmessage":
            return 0
        try:
            return int(parts[2])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _build_matcher() -> NewsEntityMatcher:
        return NewsEntityMatcher.from_targets(store.all_targets())

    def mentions_for_item(
        self,
        item: NewsItem,
        group_id: int,
        *,
        matcher: NewsEntityMatcher | None = None,
    ) -> list[int]:
        if not self._mention_enabled() or group_id <= 0:
            return []
        matcher = matcher or self._build_matcher()
        teams, players = matcher.match(f"{item.title} {item.description}")
        if not self.cfg.cs2_news_mention_teams:
            teams = set()
        if not self.cfg.cs2_news_mention_players:
            players = set()
        if not teams and not players:
            return []
        recipients = store.recipients_for([group_id], teams, players).get(group_id, set())
        return sorted(recipients)[: self.cfg.cs2_news_mention_max_per_group]

    async def render_item(self, item: NewsItem) -> bytes:
        translated_title = ""
        summary = ""
        try:
            translated_title, summary = await self._translate(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cs2.news] LLM 翻译失败(%s): %s", item.title, exc)
        image_bytes = None
        if self.cfg.cs2_news_include_image and item.image_url:
            image_bytes = await self.fetcher.fetch_impersonated_bytes(
                item.image_url,
                priority="warm",
            )
        return await card.render_news_card(
            category=news_category(item),
            title=translated_title or item.title,
            original_title=item.title,
            summary=summary or item.description or "(无摘要)",
            image_bytes=image_bytes,
            pub_time_text=format_news_time(item.pub_time),
            link=item.link if self.cfg.cs2_news_include_link else "",
        )

    @staticmethod
    def _fallback_text(item: NewsItem) -> str:
        lines = [
            f"HLTV 资讯 · {news_category(item)}",
            "",
            item.title,
        ]
        if item.description:
            lines += ["", item.description]
        if item.link:
            lines += ["", item.link]
        lines += ["", format_news_time(item.pub_time)]
        return "\n".join(lines)

    async def _translate(self, item: NewsItem) -> tuple[str, str]:
        if not self.cfg.cs2_news_translate:
            return "", ""
        provider_id = await self._resolve_provider_id()
        if not provider_id:
            logger.warning("[cs2.news] 未找到可用 LLM 提供商,推送不带中文翻译")
            return "", ""
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=TRANSLATE_PROMPT.format(
                title=item.title,
                description=item.description or "(无)",
            ),
        )
        return parse_llm_output(getattr(response, "completion_text", "") or "")

    async def _resolve_provider_id(self) -> Optional[str]:
        provider_id = self.cfg.cs2_news_provider_id.strip()
        if provider_id:
            return provider_id
        for umo in store.news_subscribers():
            try:
                provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                if provider_id:
                    return provider_id
            except Exception:  # noqa: BLE001
                continue
        try:
            manager = getattr(self.context, "provider_manager", None)
            instances = getattr(manager, "provider_insts", None) if manager else None
            if callable(instances):
                instances = instances()
            if isinstance(instances, (list, tuple)):
                for provider in instances:
                    meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
                    if getattr(meta, "type", None) in (None, "chat"):
                        return getattr(meta, "id", None)
            if isinstance(instances, dict):
                for pid, provider in instances.items():
                    meta = getattr(provider, "meta", None)
                    if getattr(meta, "type", None) in (None, "chat"):
                        return pid
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cs2.news] 枚举 LLM 提供商失败: %s", exc)
        return None
