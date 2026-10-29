"""HLTV fetcher with caching, throttling, and priority-aware access.

The optional ``curl_cffi`` channel impersonates Chrome's TLS fingerprint and can
send requests through an explicit or environment-provided proxy.  When that
channel is disabled, fails, or receives a Cloudflare challenge, traffic falls
back to the long-lived Playwright Chromium process.

Network navigations are globally serialized.  The priority gate normally
serves ``live > user > scan > warm`` while aging old requests so decorative
warm-up work cannot be starved forever.  The minimum request gap is applied
once per *fetch*: a Cloudflare challenge retry rides inside the same slot,
because the challenge + reload pair is one logical page load (see
``_navigate_html``).  Logos in a batch are exempt from the gate entirely.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Optional
from urllib.parse import quote_plus, urlsplit

from astrbot.api import logger

from . import hltv, store
from .config import Config

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FetchPriority = Literal["live", "user", "scan", "warm"]
PRIORITY_LIVE: FetchPriority = "live"
PRIORITY_USER: FetchPriority = "user"
PRIORITY_SCAN: FetchPriority = "scan"
PRIORITY_WARM: FetchPriority = "warm"
IMPERSONATE_CANDIDATES = ("firefox", "edge", "chrome_android", "chrome", "safari")

_PRIORITY_RANK: dict[str, int] = {
    PRIORITY_USER: 0,
    PRIORITY_LIVE: 1,
    PRIORITY_SCAN: 2,
    PRIORITY_WARM: 3,
}

# Explicit navigation targets.  Subresources may use third-party services, but
# user-controlled top-level navigation and every redirect stay on HLTV-owned
# hosts.  This is deliberately an exact allowlist rather than ``*.hltv.org``.
_PAGE_HOSTS = frozenset({"hltv.org", "www.hltv.org"})
_ASSET_HOSTS = frozenset({"hltv.org", "www.hltv.org", "img-cdn.hltv.org"})
_STORAGE_STATE_FILENAME = "playwright_storage_state.json"


@dataclass(frozen=True, slots=True)
class ManualVerifyResult:
    """Result of Cloudflare verification probe."""

    ok: bool
    message: str
    display: str | None = None
    vnc_port: int = 0
    novnc_port: int = 0
    tunnel_target: str | None = None


class TurnstileAutoSolver:
    """Automatic detector and solver for Cloudflare Turnstile challenges."""

    @staticmethod
    def is_challenge(title: str, html: str = "") -> bool:
        """Check if the title or HTML indicates a Cloudflare challenge/turnstile page."""
        sample_title = (title or "").lower()
        if "just a moment" in sample_title or "attention required" in sample_title:
            return True
        sample_html = (html or "")[:20000].lower()
        return (
            "just a moment" in sample_html
            or "attention required" in sample_html
            or ("cloudflare ray id" in sample_html and "access denied" in sample_html)
            or "sorry, you have been blocked" in sample_html
            or "challenges.cloudflare.com" in sample_html
        )

    @staticmethod
    def generate_bezier_trajectory(
        start_x: float,
        start_y: float,
        target_x: float,
        target_y: float,
        steps: int = 20,
    ) -> list[tuple[float, float]]:
        """Generate human-like mouse movement coordinates using a cubic Bezier curve."""
        dx = target_x - start_x
        dy = target_y - start_y
        cx1 = start_x + dx * random.uniform(0.15, 0.45) + random.uniform(-25.0, 25.0)
        cy1 = start_y + dy * random.uniform(0.15, 0.45) + random.uniform(-25.0, 25.0)
        cx2 = start_x + dx * random.uniform(0.55, 0.85) + random.uniform(-15.0, 15.0)
        cy2 = start_y + dy * random.uniform(0.55, 0.85) + random.uniform(-15.0, 15.0)

        points: list[tuple[float, float]] = []
        for i in range(1, steps + 1):
            t = i / steps
            t_eased = t * t * (3.0 - 2.0 * t)
            u = 1.0 - t_eased
            x = (
                (u**3) * start_x
                + 3.0 * (u**2) * t_eased * cx1
                + 3.0 * u * (t_eased**2) * cx2
                + (t_eased**3) * target_x
            )
            y = (
                (u**3) * start_y
                + 3.0 * (u**2) * t_eased * cy1
                + 3.0 * u * (t_eased**2) * cy2
                + (t_eased**3) * target_y
            )
            points.append((round(x, 1), round(y, 1)))
        return points

    @classmethod
    async def solve(cls, page, timeout: float = 30.0) -> bool:
        """Attempt to solve Cloudflare challenge (both JS Managed Challenge and Turnstile widget)."""
        try:
            logger.info("[cs2] 检测到 Cloudflare 挑战,尝试自动求解与穿透…")
            deadline = time.monotonic() + timeout

            clicked = False
            iframe_selector = "iframe[src*='challenges.cloudflare.com']"

            while time.monotonic() < deadline:
                # 1. 检查是否已经放行通过 (Title 变更或出现 cf_clearance)
                try:
                    title = await page.title()
                    if title and not cls.is_challenge(title):
                        logger.info(f"[cs2] Cloudflare 挑战已成功放行: {title!r}")
                        return True
                    cookies = await page.context.cookies("https://www.hltv.org")
                    if any(c.get("name") == "cf_clearance" for c in cookies):
                        logger.info("[cs2] Cloudflare 挑战已捕获有效 cf_clearance")
                        return True
                except Exception:
                    pass

                # 2. 如果尚未点击过且检测到了 Turnstile iframe, 则执行模拟物理轨迹点击
                if not clicked:
                    try:
                        iframe = await page.query_selector(iframe_selector)
                        if iframe is not None:
                            box = await iframe.bounding_box()
                            if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                                target_x = box["x"] + min(30.0, box["width"] * 0.15) + random.uniform(-2.5, 2.5)
                                target_y = box["y"] + (box["height"] / 2.0) + random.uniform(-2.5, 2.5)
                                start_x = random.uniform(50.0, 200.0)
                                start_y = random.uniform(50.0, 200.0)
                                trajectory = cls.generate_bezier_trajectory(start_x, start_y, target_x, target_y)

                                for px, py in trajectory:
                                    await page.mouse.move(px, py)
                                    await asyncio.sleep(random.uniform(0.005, 0.012))

                                await asyncio.sleep(random.uniform(0.12, 0.25))
                                await page.mouse.down()
                                await asyncio.sleep(random.uniform(0.06, 0.12))
                                await page.mouse.up()
                                clicked = True
                                logger.info(f"[cs2] 已向 Turnstile 坐标 ({target_x:.1f}, {target_y:.1f}) 发送拟真点击")
                    except Exception as e:
                        logger.debug(f"[cs2] 探测 Turnstile iframe 异常: {e}")

                await asyncio.sleep(0.5)

            logger.warning("[cs2] Cloudflare 挑战求解等待放行超时")
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cs2] Turnstile 求解异常: {exc}")
            return False


@dataclass(frozen=True, slots=True)
class ConditionalTextResult:
    """A conditional HTTP response for RSS/XML feeds."""

    text: Optional[str] = None
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(eq=False, slots=True)
class _Waiter:
    priority: FetchPriority
    sequence: int
    grant_snapshot: int
    future: asyncio.Future[None]
    granted: bool = False


class _FairPriorityGate:
    """A cancellation-safe, starvation-resistant single-owner priority gate.

    Priority is strict for fresh requests.  A waiter moves up one effective
    priority level after every four other grants so scans and warm-ups
    eventually make progress during a long live streak.

    The gate also owns the global navigation clock.  If the request gap has not
    elapsed, nobody becomes owner yet; a lightweight event-loop timer dispatches
    the highest-priority request at the actual navigation deadline.  Thus a
    warm request cannot reserve the gate merely by arriving before a live one
    and then sleeping for a long throttle interval.
    """

    _AGE_GRANTS = 4

    def __init__(self, min_gap: float = 0.0) -> None:
        self._state_lock = asyncio.Lock()
        self._waiters: list[_Waiter] = []
        self._active = False
        self._sequence = 0
        self._grant_count = 0
        self._min_gap = max(0.0, min_gap)
        self._next_grant_at = 0.0
        self._dispatch_timer: asyncio.TimerHandle | None = None
        self._closed = False

    def _effective_rank(self, waiter: _Waiter) -> int:
        grants_waited = self._grant_count - waiter.grant_snapshot
        age_levels = grants_waited // self._AGE_GRANTS
        return max(0, _PRIORITY_RANK[waiter.priority] - age_levels)

    def _timer_dispatch(self) -> None:
        # Event-loop callbacks do not interleave with synchronous critical
        # sections, so it is safe to run the non-awaiting dispatcher here.
        self._dispatch_timer = None
        self._dispatch_locked()

    def _dispatch_locked(self) -> None:
        if self._active or self._closed:
            return
        # A task cancelled while queued also cancels the Future it awaited.
        self._waiters[:] = [w for w in self._waiters if not w.future.done()]
        if not self._waiters:
            return
        now = time.monotonic()
        delay = self._next_grant_at - now
        if delay > 0:
            if self._dispatch_timer is None:
                loop = asyncio.get_running_loop()
                self._dispatch_timer = loop.call_later(delay, self._timer_dispatch)
            return
        if self._dispatch_timer is not None:
            self._dispatch_timer.cancel()
            self._dispatch_timer = None
        waiter = min(
            self._waiters,
            key=lambda w: (self._effective_rank(w), w.sequence),
        )
        self._waiters.remove(waiter)
        self._active = True
        self._grant_count += 1
        self._next_grant_at = now + self._min_gap
        waiter.granted = True
        waiter.future.set_result(None)

    async def acquire(self, priority: FetchPriority) -> None:
        loop = asyncio.get_running_loop()
        async with self._state_lock:
            if self._closed:
                raise RuntimeError("priority gate is closed")
            self._sequence += 1
            waiter = _Waiter(
                priority=priority,
                sequence=self._sequence,
                grant_snapshot=self._grant_count,
                future=loop.create_future(),
            )
            self._waiters.append(waiter)
            self._dispatch_locked()
        try:
            await waiter.future
        except asyncio.CancelledError:
            # Cancellation may race with set_result().  If this waiter had
            # already become owner, hand the gate to the next task here.
            async with self._state_lock:
                if waiter.granted:
                    self._active = False
                    self._dispatch_locked()
                elif waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise

    async def close(self) -> None:
        """Cancel the throttle timer and wake queued callers during shutdown."""
        async with self._state_lock:
            self._closed = True
            if self._dispatch_timer is not None:
                self._dispatch_timer.cancel()
                self._dispatch_timer = None
            waiters, self._waiters = self._waiters, []
            for waiter in waiters:
                if not waiter.future.done():
                    waiter.future.cancel()

    async def release(self) -> None:
        async with self._state_lock:
            if not self._active:
                raise RuntimeError("priority gate released without an owner")
            self._active = False
            self._dispatch_locked()

    @asynccontextmanager
    async def slot(self, priority: FetchPriority) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            await self.release()


class Fetcher:
    _LOGO_FETCH_CONCURRENCY = 3

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._context = None
        self._xvfb_proc: subprocess.Popen[bytes] | None = None
        self._display: str | None = None
        self._lifecycle_lock = asyncio.Lock()
        # Browser operations are serialized as a whole so recycling can never
        # close the shared context while another command or logo batch uses it.
        self._browser_operation_lock = asyncio.Lock()
        self._browser_uses = 0
        self._browser_started_at: float | None = None
        self._gate = _FairPriorityGate(cfg.cs2_request_min_gap)
        self._asset_semaphore = asyncio.Semaphore(self._LOGO_FETCH_CONCURRENCY)
        self._closing = False
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._refreshing: set[str] = set()  # background page refresh dedupe
        self._logo_refreshing: set[str] = set()  # background logo fetch dedupe
        self._logo_failed: dict[str, float] = {}  # url -> last-fail epoch (retry-cooldown)
        self._working_impersonate: str | None = None
        self._challenge_until = 0.0
        self._challenge_streak = 0
        self._metrics_started = time.monotonic()
        self._metrics: dict[str, int] = {
            "attempts": 0,
            "success": 0,
            "errors": 0,
            "challenges": 0,
            "suppressed": 0,
        }
        self._last_metrics_log = self._metrics_started

    @property
    def closed(self) -> bool:
        """Whether this instance has completed or started shutdown."""
        return self._closing

    @staticmethod
    def _priority(value: FetchPriority | str) -> FetchPriority:
        if value not in _PRIORITY_RANK:
            allowed = ", ".join(_PRIORITY_RANK)
            raise ValueError(f"unknown fetch priority {value!r}; expected one of {allowed}")
        return value  # type: ignore[return-value]

    @staticmethod
    def _allowed_url(url: str, hosts: frozenset[str]) -> bool:
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").rstrip(".").lower()
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme.lower() == "https"
            and parsed.username is None
            and parsed.password is None
            and port in (None, 443)
            and host in hosts
        )

    @classmethod
    def _require_allowed_url(cls, url: str, hosts: frozenset[str]) -> None:
        if not cls._allowed_url(url, hosts):
            raise ValueError(f"blocked non-HLTV navigation URL: {url!r}")

    def _backoff_remaining(self) -> float:
        return max(0.0, self._challenge_until - time.monotonic())

    def backoff_remaining_seconds(self) -> float:
        """Public read-only view for the scheduler's retry delay."""
        return self._backoff_remaining()

    def _backoff_blocks(self, priority: FetchPriority) -> bool:
        # The live result lane must remain available.
        # Everything decorative or periodic yields to the cooldown.
        if priority == PRIORITY_LIVE:
            return False
        return self._backoff_remaining() > 0

    def _record_challenge(self, *, arm_backoff: bool = False) -> None:
        self._metrics["challenges"] += 1
        if not arm_backoff:
            return
        self._challenge_streak += 1
        base = float(self.cfg.cs2_challenge_backoff_base_min * 60)
        cap = float(self.cfg.cs2_challenge_backoff_max_min * 60)
        exponent = min(self._challenge_streak - 1, 16)
        delay = min(cap, base * (2**exponent))
        delay *= random.uniform(0.85, 1.15)
        self._challenge_until = time.monotonic() + delay
        logger.warning(
            f"[cs2] Cloudflare 连续失败 {self._challenge_streak} 次,"
            f"低优先级抓取退避 {delay / 60:.1f} 分钟"
        )

    def _record_fetch_success(self) -> None:
        self._metrics["success"] += 1
        self._challenge_streak = 0
        self._challenge_until = 0.0

    def metrics_snapshot(self) -> dict[str, float | int]:
        elapsed = max(1e-6, time.monotonic() - self._metrics_started)
        attempts = self._metrics["attempts"]
        return {
            **self._metrics,
            "window_seconds": elapsed,
            "attempts_per_hour": attempts * 3600.0 / elapsed,
            "backoff_remaining": self._backoff_remaining(),
            "challenge_streak": self._challenge_streak,
        }

    def maybe_log_metrics(self, *, interval: float = 3600.0) -> None:
        now = time.monotonic()
        if now - self._last_metrics_log < interval:
            return
        snapshot = self.metrics_snapshot()
        logger.info(
            "[cs2] HLTV 抓取统计:"
            f"尝试 {snapshot['attempts']} / 成功 {snapshot['success']} / "
            f"失败 {snapshot['errors']} / CF {snapshot['challenges']} / "
            f"退避 {float(snapshot['backoff_remaining']) / 60:.1f} 分钟 / "
            f"抑制 {snapshot['suppressed']}"
        )
        self._metrics = {
            "attempts": 0,
            "success": 0,
            "errors": 0,
            "challenges": 0,
            "suppressed": 0,
        }
        self._metrics_started = now
        self._last_metrics_log = now


    # —— curl_cffi 双通道(Chrome TLS 指纹伪装)——
    # 仿照 HLTV RSS 插件:用 curl_cffi 的 impersonate="chrome" 伪装 Chrome TLS 指纹,
    # 绕过 Cloudflare 的 JA3 检测。成功则直接返回;失败(403/挑战页/超时)则回退 Playwright。
    # 代理配置优先读取 WebUI,其次沿用容器的 HTTP(S)_PROXY。页面和 logo 共用请求闸门。

    def _curl_cffi_enabled(self) -> bool:
        return bool(self.cfg.cs2_use_curl_cffi)

    def _curl_cffi_proxy(self) -> Optional[str]:
        proxy = (self.cfg.cs2_proxy_url or "").strip()
        if proxy:
            return proxy
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            value = os.environ.get(key, "").strip()
            if value:
                return value
        return None

    def _curl_cffi_timeout(self) -> float:
        return max(5.0, min(float(self.cfg.cs2_nav_timeout) / 1000.0, 120.0))

    def _headful_enabled(self) -> bool:
        return bool(self.cfg.cs2_headful)

    def _manual_verify_enabled(self) -> bool:
        # Kept for backward compatibility
        return False

    def _visible_headful(self) -> bool:
        return bool(self.cfg.cs2_headful and os.name == "nt")

    def _chromium_launch_args(self) -> list[str]:
        args = ["--disable-blink-features=AutomationControlled"]
        if not self._headful_enabled():
            return args
        if self._visible_headful():
            # A local Windows runner and an explicitly enabled manual-verify
            # session need a visible window.  The offscreen position remains
            # useful for ordinary Linux headful scraping.
            args += ["--window-position=0,0", "--window-size=1366,900"]
        else:
            args += ["--window-position=-32000,-32000", "--window-size=1366,900"]
        return args

    def _storage_state_file(self) -> Path:
        return store.DATA_DIR / _STORAGE_STATE_FILENAME

    def _load_storage_cookies(self, domain: str = "hltv.org") -> dict[str, str]:
        state_file = self._storage_state_file()
        if not state_file.is_file():
            return {}
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            cookies: dict[str, str] = {}
            now = time.time()
            for item in data.get("cookies", []):
                item_domain = str(item.get("domain") or "").lstrip(".")
                if domain in item_domain:
                    expires = float(item.get("expires", -1) or -1)
                    if 0 < expires < now:
                        continue
                    name = str(item.get("name") or "").strip()
                    value = str(item.get("value") or "").strip()
                    if name and value:
                        cookies[name] = value
            return cookies
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[cs2] 读取 storage_state 凭证失败: {exc}")
            return {}

    @staticmethod
    def _html_title(html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.I | re.S)
        if not match:
            return ""
        return html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()

    @staticmethod
    def _is_cloudflare_challenge_html(html_text: str) -> bool:
        """检测 curl_cffi 返回的 HTML 是否为 Cloudflare 挑战页(需回退 Playwright)。"""
        sample = (html_text or "")[:20000].lower()
        return (
            "just a moment" in sample
            or "attention required" in sample
            or ("cloudflare ray id" in sample and "access denied" in sample)
            or "sorry, you have been blocked" in sample
        )

    async def _fetch_via_curl_cffi(
        self, url: str, priority: FetchPriority
    ) -> Optional[str]:
        """用 curl_cffi 的 Chrome TLS 指纹抓取 HLTV 页面 HTML。"""
        if not self._curl_cffi_enabled():
            return None
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            logger.warning("[cs2] curl_cffi 未安装,回退 Playwright")
            return None

        proxy = self._curl_cffi_proxy()
        cookies = self._load_storage_cookies()
        backend = str(getattr(self.cfg, "cs2_browser_backend", "camoufox") or "camoufox").lower()
        impersonate_target = "firefox" if backend == "camoufox" else "chrome"
        session_kwargs: dict[str, object] = {
            "impersonate": impersonate_target,
            "proxy": proxy,
            "timeout": self._curl_cffi_timeout(),
        }
        if cookies:
            session_kwargs["cookies"] = cookies
        if impersonate_target == "firefox":
            ff_os = getattr(self.cfg, "cs2_camoufox_os", "windows")
            ff_ua = (
                "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"
                if ff_os == "linux"
                else "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0"
            )
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "User-Agent": ff_ua,
            }
        else:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": UA,
            }
        async with self._gate.slot(priority):
            try:
                async with AsyncSession(**session_kwargs) as session:
                    resp = await session.get(url, headers=headers)
                    resp.raise_for_status()
                    final_url = str(getattr(resp, "url", "") or url)
                    if not self._allowed_url(final_url, _PAGE_HOSTS):
                        logger.warning(
                            f"[cs2] curl_cffi 页面重定向到非白名单地址: {final_url}"
                        )
                        return None
                    content_type = (
                        resp.headers.get("content-type", "") or ""
                    ).lower()
                    if content_type.startswith(("application/json", "text/plain")):
                        logger.warning(
                            f"[cs2] curl_cffi 返回了非 HTML 页面({content_type}): {url}"
                        )
                        return None
                    html_text = resp.text
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] curl_cffi 抓取失败 {url}: {exc}")
                return None

        if self._is_cloudflare_challenge_html(html_text):
            self._record_challenge()
            logger.info(f"[cs2] curl_cffi 命中 Cloudflare 挑战页,回退 Playwright: {url}")
            return None
        title = self._html_title(html_text)
        if self._is_error_page(title, html_text):
            logger.warning(f"[cs2] curl_cffi 拒绝错误页 {title!r}: {url}")
            return None
        logger.debug(f"[cs2] curl_cffi 抓取成功: {url}")
        self._record_fetch_success()
        return html_text

    async def _fetch_via_nodriver(
        self, url: str, priority: FetchPriority
    ) -> Optional[str]:
        """使用 nodriver 异步原生 CDP 直连抓取 HLTV 页面 (备选抗检测方案)。"""
        if self._backoff_blocks(priority):
            self._metrics["suppressed"] += 1
            return None
        try:
            import nodriver as uc
        except ImportError:
            logger.debug("[cs2] nodriver 未安装,跳过 nodriver 通道")
            return None

        async with self._gate.slot(priority):
            browser = None
            try:
                headful = getattr(self.cfg, "cs2_headful", False)
                proxy = self._curl_cffi_proxy()
                browser_args = []
                if proxy:
                    browser_args.append(f"--proxy-server={proxy}")
                browser = await uc.start(
                    headless=not headful,
                    browser_args=browser_args if browser_args else None,
                )
                tab = await browser.get(url)
                await tab.sleep(2.0)

                content = await tab.get_content()
                title = self._html_title(content)

                if TurnstileAutoSolver.is_challenge(title, content):
                    logger.info(f"[cs2] nodriver 遇到 Cloudflare 挑战,等待穿透: {url}")
                    timeout = getattr(self.cfg, "cs2_turnstile_timeout", 30)
                    start_t = time.monotonic()
                    while time.monotonic() - start_t < timeout:
                        await tab.sleep(1.5)
                        content = await tab.get_content()
                        title = self._html_title(content)
                        if not TurnstileAutoSolver.is_challenge(title, content):
                            break
                    if TurnstileAutoSolver.is_challenge(title, content):
                        logger.warning(f"[cs2] nodriver 挑战超时未放行: {url}")
                        self._record_challenge()
                        return None

                if self._is_error_page(title, content):
                    logger.warning(f"[cs2] nodriver 拒绝错误页 {title!r}: {url}")
                    return None

                try:
                    cookies = await tab.send(uc.cdp.network.get_cookies())
                    if cookies:
                        formatted_cookies = [
                            {
                                "name": c.name,
                                "value": c.value,
                                "domain": c.domain,
                                "path": c.path,
                                "expires": getattr(c, "expires", -1),
                                "httpOnly": getattr(c, "http_only", False),
                                "secure": getattr(c, "secure", False),
                            }
                            for c in cookies
                        ]
                        self._save_storage_state({"cookies": formatted_cookies, "origins": []})
                except Exception as c_exc:
                    logger.debug(f"[cs2] nodriver 提取 cookies 失败: {c_exc}")

                self._record_fetch_success()
                return content
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] nodriver 抓取失败 {url}: {exc}")
                return None
            finally:
                if browser is not None:
                    try:
                        browser.stop()
                    except Exception:
                        pass

    async def _download_logo_via_curl_cffi(
        self, url: str, priority: FetchPriority
    ) -> Optional[bytes]:
        """用 curl_cffi 的 Chrome TLS 指纹下载队标/赛事 logo 图片。"""
        if not self._curl_cffi_enabled():
            return None
        if self._backoff_blocks(priority):
            self._metrics["suppressed"] += 1
            return None
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            return None

        proxy = self._curl_cffi_proxy()
        cookies = self._load_storage_cookies()
        backend = str(getattr(self.cfg, "cs2_browser_backend", "camoufox") or "camoufox").lower()
        impersonate_target = "firefox" if backend == "camoufox" else "chrome"
        session_kwargs: dict[str, object] = {
            "impersonate": impersonate_target,
            "proxy": proxy,
            "timeout": self._curl_cffi_timeout(),
        }
        if cookies:
            session_kwargs["cookies"] = cookies
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.hltv.org/",
        }
        async with self._asset_semaphore:
            try:
                async with AsyncSession(**session_kwargs) as session:
                    resp = await session.get(url, headers=headers)
                    resp.raise_for_status()
                    final_url = str(getattr(resp, "url", "") or url)
                    if not self._allowed_url(final_url, _ASSET_HOSTS):
                        logger.warning(
                            f"[cs2] curl_cffi logo 重定向到非白名单地址: {final_url}"
                        )
                        return None
                    content_type = (
                        resp.headers.get("content-type", "") or ""
                    ).lower()
                    body = resp.content
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] curl_cffi logo 下载失败 {url}: {exc}")
                return None
        if not body or not content_type.startswith("image/"):
            logger.warning(
                f"[cs2] curl_cffi logo 响应无效 {url}: content-type={content_type!r}"
            )
            return None
        logger.debug(f"[cs2] curl_cffi logo 下载成功: {url}")
        return body

    async def _fetch_search_via_curl_cffi(
        self, term: str, priority: FetchPriority
    ) -> Optional[str]:
        """Fetch the HLTV typeahead JSON through the proxy-capable channel."""
        if not self._curl_cffi_enabled():
            return None
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            return None

        url = f"https://www.hltv.org/search?term={quote_plus(term)}"
        cookies = self._load_storage_cookies()
        session_kwargs: dict[str, object] = {
            "impersonate": "chrome",
            "proxy": self._curl_cffi_proxy(),
            "timeout": self._curl_cffi_timeout(),
        }
        if cookies:
            session_kwargs["cookies"] = cookies
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.hltv.org/matches",
        }
        async with self._gate.slot(priority):
            try:
                async with AsyncSession(**session_kwargs) as session:
                    resp = await session.get(url, headers=headers)
                    if resp.status_code != 200:
                        return None
                    body = resp.text
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] curl_cffi 搜索失败 term={term!r}: {exc}")
                return None
        if self._is_cloudflare_challenge_html(body):
            return None
        return body

    def _impersonate_candidates(self) -> list[str]:
        if self._working_impersonate:
            return [self._working_impersonate] + [
                profile
                for profile in IMPERSONATE_CANDIDATES
                if profile != self._working_impersonate
            ]
        return list(IMPERSONATE_CANDIDATES)

    async def fetch_impersonated_text(
        self,
        url: str,
        *,
        accept: str,
        priority: FetchPriority = PRIORITY_SCAN,
    ) -> Optional[str]:
        """Fetch text/XML with a browser-fingerprint fallback chain and proxy."""
        result = await self.fetch_impersonated_text_conditional(
            url,
            accept=accept,
            priority=priority,
        )
        return result.text

    async def fetch_impersonated_text_conditional(
        self,
        url: str,
        *,
        accept: str,
        etag: str = "",
        last_modified: str = "",
        priority: FetchPriority = PRIORITY_SCAN,
    ) -> ConditionalTextResult:
        """Fetch XML/JSON with optional ETag and Last-Modified validators.

        A 304 response is a successful no-op and avoids downloading or parsing
        the unchanged RSS body.  The fallback chain remains the same as the
        normal text fetcher so a challenged fingerprint does not poison the
        conditional path.
        """
        self._require_allowed_url(url, _PAGE_HOSTS)
        normalized_priority = self._priority(priority)
        if self._backoff_blocks(normalized_priority):
            self._metrics["suppressed"] += 1
            logger.warning(
                f"[cs2] Cloudflare 退避中,暂缓文本抓取({self._backoff_remaining():.0f}s): {url}"
            )
            return ConditionalTextResult()
        proxy = self._curl_cffi_proxy()
        cookies = self._load_storage_cookies()
        headers = {"Accept": accept}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        last_error: Exception | None = None
        saw_challenge = False
        for profile in self._impersonate_candidates():
            try:
                from curl_cffi.requests import AsyncSession

                session_kwargs: dict[str, object] = {
                    "impersonate": profile,
                    "proxy": proxy,
                    "timeout": self._curl_cffi_timeout(),
                }
                if cookies:
                    session_kwargs["cookies"] = cookies

                async with self._gate.slot(normalized_priority):
                    async with AsyncSession(**session_kwargs) as session:
                        resp = await session.get(url, headers=headers)
                        if resp.status_code == 304:
                            if profile != self._working_impersonate:
                                self._working_impersonate = profile
                            self._record_fetch_success()
                            return ConditionalTextResult(
                                etag=etag,
                                last_modified=last_modified,
                                not_modified=True,
                            )
                        if resp.status_code >= 400:
                            last_error = RuntimeError(
                                f"HTTP {resp.status_code} (impersonate={profile})"
                            )
                            continue
                        text = resp.text
                        response_etag = str(resp.headers.get("etag", "") or "").strip()
                        response_modified = str(
                            resp.headers.get("last-modified", "") or ""
                        ).strip()
                if self._is_cloudflare_challenge_html(text):
                    saw_challenge = True
                    last_error = RuntimeError(f"Cloudflare challenge (impersonate={profile})")
                    continue
                if profile != self._working_impersonate:
                    self._working_impersonate = profile
                    logger.info(f"[cs2] 文本抓取使用指纹 {profile}: {url}")
                self._record_fetch_success()
                return ConditionalTextResult(
                    text=text,
                    etag=response_etag,
                    last_modified=response_modified,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.info(f"[cs2] 指纹 {profile} 文本抓取失败 {url}: {exc}")
        if last_error is not None:
            logger.warning(f"[cs2] 所有指纹均无法抓取文本 {url}: {last_error}")
        if saw_challenge:
            self._record_challenge(arm_backoff=True)
        return ConditionalTextResult()

    async def fetch_impersonated_bytes(
        self,
        url: str,
        *,
        accept: str = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        priority: FetchPriority = PRIORITY_WARM,
    ) -> Optional[bytes]:
        """Fetch image bytes with the same browser-fingerprint fallback chain."""
        self._require_allowed_url(url, _ASSET_HOSTS)
        normalized_priority = self._priority(priority)
        if self._backoff_blocks(normalized_priority):
            self._metrics["suppressed"] += 1
            return None
        proxy = self._curl_cffi_proxy()
        cookies = self._load_storage_cookies()
        headers = {"Accept": accept, "Referer": "https://www.hltv.org/"}
        last_error: Exception | None = None
        for profile in self._impersonate_candidates():
            try:
                from curl_cffi.requests import AsyncSession

                session_kwargs: dict[str, object] = {
                    "impersonate": profile,
                    "proxy": proxy,
                    "timeout": self._curl_cffi_timeout(),
                }
                if cookies:
                    session_kwargs["cookies"] = cookies

                async with self._asset_semaphore:
                    async with AsyncSession(**session_kwargs) as session:
                        resp = await session.get(url, headers=headers)
                        if resp.status_code >= 400:
                            last_error = RuntimeError(
                                f"HTTP {resp.status_code} (impersonate={profile})"
                            )
                            continue
                        content_type = (
                            resp.headers.get("content-type", "") or ""
                        ).lower()
                        body = resp.content
                if not body or not content_type.startswith("image/"):
                    last_error = RuntimeError(
                        f"invalid content-type {content_type!r} (impersonate={profile})"
                    )
                    continue
                if profile != self._working_impersonate:
                    self._working_impersonate = profile
                    logger.info(f"[cs2] 图片抓取使用指纹 {profile}: {url}")
                return body
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.info(f"[cs2] 指纹 {profile} 图片抓取失败 {url}: {exc}")
        if last_error is not None:
            logger.warning(f"[cs2] 所有指纹均无法抓取图片 {url}: {last_error}")
        return None

    async def start(self) -> None:
        """Start browser once; concurrent callers share the same launch."""
        async with self._lifecycle_lock:
            if self._closing:
                raise RuntimeError("fetcher is shutting down")
            if self._browser:
                await self._ensure_context_locked()
                return

            backend = str(
                getattr(self.cfg, "cs2_browser_backend", "camoufox") or "camoufox"
            ).lower()
            launch_env: dict[str, str] | None = None
            headful = self._headful_enabled()
            if headful and os.name != "nt":
                display = os.environ.get("DISPLAY")
                if not display and self._display_ready(self._display):
                    display = self._display
                if not display:
                    display = await asyncio.to_thread(self._start_xvfb)
                launch_env = os.environ.copy()
                launch_env["DISPLAY"] = display

            browser = None
            pw = None

            # 1. 首选 Camoufox (C++ 级反检测 Firefox 内核)
            if backend == "camoufox":
                try:
                    from camoufox.async_api import AsyncCamoufox

                    try:
                        from camoufox.addons import DefaultAddons

                        exclude_addons = [DefaultAddons.UBO]
                    except Exception:
                        exclude_addons = None

                    proxy_url = self._curl_cffi_proxy()
                    camoufox_opts: dict[str, object] = {
                        "headless": not headful,
                        "geoip": False,
                        "os": getattr(self.cfg, "cs2_camoufox_os", "windows"),
                    }
                    if exclude_addons:
                        camoufox_opts["exclude_addons"] = exclude_addons
                    if proxy_url:
                        camoufox_opts["proxy"] = {"server": proxy_url}
                    if launch_env:
                        camoufox_opts["env"] = launch_env
                    cm = AsyncCamoufox(**camoufox_opts)
                    browser = await cm.start()
                    logger.info(
                        f"[cs2] Camoufox 反检测内核已启动"
                        f"(os={getattr(self.cfg, 'cs2_camoufox_os', 'windows')}, headless={not headful})"
                    )
                except ImportError:
                    logger.warning("[cs2] camoufox 未安装,回退 Playwright Chromium")
                except Exception as exc:
                    logger.warning(
                        f"[cs2] Camoufox 启动失败({exc}),回退 Playwright Chromium"
                    )

            # 2. 备用 Playwright Chromium (标准内核)
            if browser is None:
                from playwright.async_api import async_playwright

                pw = await async_playwright().start()
                args = self._chromium_launch_args()
                try:
                    browser = await pw.chromium.launch(
                        headless=not headful,
                        args=args,
                        ignore_default_args=["--enable-automation"],
                        env=launch_env,
                    )
                except asyncio.CancelledError:
                    await pw.stop()
                    raise
                except Exception as exc:
                    await pw.stop()
                    if (
                        "Executable doesn't exist" in str(exc)
                        or "playwright install" in str(exc)
                    ):
                        logger.error(
                            "[cs2] Chromium 抓取内核未安装或路径不存在。"
                            "请在服务器/容器终端执行: playwright install chromium"
                        )
                    raise

            try:
                context = await self._build_context(browser)
            except asyncio.CancelledError:
                await browser.close()
                if pw:
                    await pw.stop()
                raise
            except Exception:
                await browser.close()
                if pw:
                    await pw.stop()
                raise

            self._pw = pw
            self._browser = browser
            self._context = context
            self._browser_uses = 0
            self._browser_started_at = time.monotonic()
            mode = "无头"
            if headful:
                mode = "有头/可见" if self._visible_headful() else "有头/屏幕外"
            engine_name = "Camoufox" if pw is None else "Playwright Chromium"
            logger.info(
                f"[cs2] {engine_name} 抓取浏览器已启动({mode})"
            )

    def _start_xvfb(self) -> str:
        """Start a private X server for headful Chromium when no DISPLAY exists."""
        binary = shutil.which("Xvfb")
        if not binary:
            raise RuntimeError(
                "cs2_headful=True 但系统未安装 Xvfb;"
                "Debian/Ubuntu 可执行 apt-get install -y xvfb"
            )
        for number in range(99, 120):
            display = f":{number}"
            socket_path = Path(f"/tmp/.X11-unix/X{number}")
            lock_path = Path(f"/tmp/.X{number}-lock")
            if socket_path.exists():
                if self._display_socket_alive(socket_path):
                    self._display = display
                    logger.info(f"[cs2] 复用已有 Xvfb {display}")
                    return display
            if lock_path.exists() and self._xvfb_lock_owner_alive(lock_path):
                continue
            if socket_path.exists():
                try:
                    socket_path.unlink()
                except OSError as exc:
                    logger.warning(f"[cs2] 无法清理陈旧 Xvfb socket {socket_path}: {exc}")
                    continue
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except OSError as exc:
                    logger.warning(f"[cs2] 无法清理陈旧 Xvfb lock {lock_path}: {exc}")
                    continue
            proc = subprocess.Popen(
                [
                    binary,
                    display,
                    "-screen",
                    "0",
                    "1366x900x24",
                    "-ac",
                    "-nolisten",
                    "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(30):
                if proc.poll() is not None:
                    raise RuntimeError(f"Xvfb 启动失败,DISPLAY={display}")
                if socket_path.exists():
                    self._xvfb_proc = proc
                    self._display = display
                    logger.info(f"[cs2] 已为有头 Chromium 启动 Xvfb {display}")
                    return display
                time.sleep(0.1)
            proc.terminate()
        raise RuntimeError("找不到可用的 Xvfb DISPLAY")

    @staticmethod
    def _xvfb_lock_owner_alive(lock_path: Path) -> bool:
        """Return whether the PID recorded in an Xvfb lock file still exists."""
        try:
            pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _display_socket_alive(socket_path: Path) -> bool:
        """Return whether an X11 socket still accepts connections."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(socket_path))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _display_ready(self, display: str | None) -> bool:
        """Check a locally managed Xvfb display without launching a new one."""
        if not display:
            return False
        if not display.startswith(":"):
            return True
        number = display[1:].split(".", 1)[0]
        if not number.isdecimal():
            return False
        proc = self._xvfb_proc
        if proc is not None and proc.poll() is not None:
            return False
        socket_path = Path(f"/tmp/.X11-unix/X{number}")
        return socket_path.exists() and self._display_socket_alive(socket_path)

    def _track_task(self, coro, *, name: str) -> asyncio.Task[None] | None:
        if self._closing:
            coro.close()
            return None
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Cancel/await all owned background work, then close Playwright."""
        self._closing = True
        tasks = tuple(self._background_tasks)
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self._gate.close()
            # Browser cleanup still runs if the shutdown coroutine itself is
            # cancelled while it is waiting for its children.
            async with self._lifecycle_lock:
                await self._close_playwright_stack(stop_xvfb=True)

    async def close(self) -> None:
        """Alias for shutdown to support context managers and standard cleanup."""
        await self.shutdown()

    @asynccontextmanager
    async def _navigation_slot(self, priority: FetchPriority) -> AsyncIterator[None]:
        async with self._gate.slot(priority):
            yield

    async def _build_context(self, browser):
        proxy_url = self._curl_cffi_proxy()
        context_options: dict[str, object] = {
            "locale": "en-US",
            "viewport": {"width": 1366, "height": 900},
        }
        # 仅标准 Playwright Chromium 需要显式设置 Chrome UA, Camoufox 会自动适配自身指纹
        if self._pw is not None:
            context_options["user_agent"] = UA
        if proxy_url:
            context_options["proxy"] = {"server": proxy_url}
        state_file = self._storage_state_file()
        if state_file.is_file():
            context_options["storage_state"] = str(state_file)
        return await browser.new_context(**context_options)

    async def _save_storage_state(self, *, context=None) -> None:
        """Persist cookies/local storage after Cloudflare verification."""
        ctx = context or self._context
        if ctx is None:
            return
        state_file = self._storage_state_file()
        tmp_file = state_file.with_name(f".{state_file.name}.tmp")
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            await ctx.storage_state(path=str(tmp_file))
            await asyncio.to_thread(os.replace, tmp_file, state_file)
            logger.info(f"[cs2] 已保存验证凭证状态: {state_file}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cs2] 保存验证凭证状态失败: {exc}")
        finally:
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass

    async def _has_cf_clearance(self) -> bool:
        context = self._context
        if context is None:
            return False
        try:
            cookies = await context.cookies("https://www.hltv.org")
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return any(cookie.get("name") == "cf_clearance" for cookie in cookies)

    async def manual_verify(self) -> ManualVerifyResult:
        """Trigger an automated Turnstile solving probe and persist clearance."""
        if self._closing:
            return ManualVerifyResult(False, "插件正在关闭,无法开始验证")

        if self._curl_cffi_enabled():
            try:
                html = await self._fetch_via_curl_cffi(hltv.URL_MATCHES, PRIORITY_USER)
                if html is not None and not TurnstileAutoSolver.is_challenge(self._html_title(html), html):
                    return ManualVerifyResult(
                        True,
                        "当前 Cloudflare 凭证有效且 curl_cffi 通信正常,无需额外解盾",
                    )
            except Exception as exc:
                logger.debug(f"[cs2] curl_cffi 验证探针预检异常: {exc}")

        try:
            async with self._navigation_slot(PRIORITY_USER):
                async with self._browser_operation_lock:
                    await self.start()
                    self._browser_uses += 1
                    context = await self._new_context()
                    page = None
                    try:
                        page = await context.new_page()
                        await self._guard_top_level_navigation(page, _PAGE_HOSTS)
                        html = await self._navigate_html(
                            page,
                            hltv.URL_MATCHES,
                            ".match",
                            PRIORITY_USER,
                        )
                        if html is None:
                            return ManualVerifyResult(
                                False, "Cloudflare 自动求解未通过或 HLTV 拒绝访问"
                            )
                        has_clearance = await self._has_cf_clearance()
                        await self._save_storage_state()
                        return ManualVerifyResult(
                            True,
                            (
                                "Cloudflare Turnstile 自动求解成功,已捕获并保存 cf_clearance"
                                if has_clearance
                                else "HLTV 已成功通过防风控验证,并已保存浏览器会话状态"
                            ),
                        )
                    finally:
                        if page is not None:
                            await self._close_page(page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cs2] 自动验证探针执行失败: {exc}")
            return ManualVerifyResult(False, f"验证失败: {exc}")

    async def _ensure_context_locked(self):
        if self._context is not None:
            is_closed = getattr(self._context, "is_closed", None)
            if callable(is_closed) and is_closed():
                self._context = None
        if self._context is None:
            browser = self._browser
            if browser is None:
                raise RuntimeError("fetcher browser is not started")
            self._context = await self._build_context(browser)
        return self._context

    async def _new_context(self):
        """Return the shared context so Cloudflare cookies survive fetches."""
        async with self._lifecycle_lock:
            if self._closing:
                raise RuntimeError("fetcher is shutting down")
            return await self._ensure_context_locked()

    async def _close_playwright_stack(self, *, stop_xvfb: bool) -> None:
        """Close Playwright resources. Caller must hold ``_lifecycle_lock``."""
        context, browser, pw = self._context, self._browser, self._pw
        self._context = self._browser = self._pw = None
        self._browser_uses = 0
        self._browser_started_at = None
        try:
            if context:
                try:
                    await self._save_storage_state(context=context)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[cs2] 浏览器栈关闭前保存验证状态失败: {exc}")
                await self._close_context(context)
        finally:
            try:
                if browser:
                    await browser.close()
            except Exception as exc:  # cleanup failure is useful but non-fatal
                logger.warning(f"[cs2] Chromium 关闭失败: {exc}")
            finally:
                if pw:
                    try:
                        await pw.stop()
                    except Exception as exc:
                        logger.warning(f"[cs2] Playwright 关闭失败: {exc}")

        if stop_xvfb:
            xvfb = self._xvfb_proc
            self._xvfb_proc = None
            self._display = None
            if xvfb and xvfb.poll() is None:
                xvfb.terminate()
                try:
                    await asyncio.to_thread(xvfb.wait, 5)
                except subprocess.TimeoutExpired:
                    xvfb.kill()

    async def _recycle_browser_if_needed(self) -> None:
        """Rebuild the Playwright stack after a browser operation.

        The caller holds ``_browser_operation_lock``, so no page or context
        from another operation can still be in use here.
        """
        if self._closing or self._browser is None:
            return

        use_limit = self.cfg.cs2_browser_recycle_uses
        age_limit_seconds = self.cfg.cs2_browser_recycle_hours * 3600.0
        uses_due = use_limit > 0 and self._browser_uses >= use_limit
        age_due = (
            age_limit_seconds > 0
            and self._browser_started_at is not None
            and time.monotonic() - self._browser_started_at >= age_limit_seconds
        )
        if not (uses_due or age_due):
            return

        reason = "次数" if uses_due else "运行时长"
        detail = (
            f"{self._browser_uses} 次"
            if uses_due
            else f"{self.cfg.cs2_browser_recycle_hours:g} 小时"
        )
        async with self._lifecycle_lock:
            if self._closing or self._browser is None:
                return
            logger.info(f"[cs2] Playwright 达到回收阈值({reason} {detail}),重建浏览器栈")
            await self._close_playwright_stack(stop_xvfb=False)
            logger.info("[cs2] Playwright 浏览器栈已回收,Xvfb 保持复用")

    @staticmethod
    async def _close_context(ctx) -> None:
        try:
            await ctx.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not let a best-effort browser cleanup error replace the real
            # navigation exception (especially an in-flight cancellation).
            logger.warning(f"[cs2] 浏览器 context 关闭失败: {exc}")

    @staticmethod
    async def _close_page(page) -> None:
        try:
            await page.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[cs2] 浏览器页面关闭失败: {exc}")

    @staticmethod
    def _is_challenge(title: str) -> bool:
        title = (title or "").lower()
        return "just a moment" in title or "attention required" in title

    @staticmethod
    def _is_error_page(title: str, html: str) -> bool:
        title = (title or "").strip().lower()
        if any(
            marker in title
            for marker in (
                "access denied",
                "service unavailable",
                "bad gateway",
                "gateway timeout",
                "internal server error",
                "page not found",
                "web server is down",
                "origin is unreachable",
                "sorry, you have been blocked",
            )
        ):
            return True
        # All normal HLTV documents currently brand their title.  This also
        # keeps generic proxy/ISP error documents with HTTP 200 out of cache.
        if "hltv.org" not in title:
            return True
        sample = (html or "")[:12000].lower()
        return "sorry, you have been blocked" in sample or (
            "cloudflare ray id" in sample and "access denied" in sample
        )

    async def _guard_top_level_navigation(self, page, hosts: frozenset[str]) -> None:
        """Abort a disallowed main-frame redirect before the request is sent."""

        async def _guard(route, request) -> None:
            if request.is_navigation_request() and request.frame == page.main_frame:
                if not self._allowed_url(request.url, hosts):
                    logger.warning(f"[cs2] 已阻止非 HLTV 重定向: {request.url}")
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await page.route("**/*", _guard)

    async def _navigate_html(
        self,
        page,
        url: str,
        wait_selector: str,
        priority: FetchPriority,
    ) -> Optional[str]:
        """Navigate and return only a verified, non-error HLTV document.

        **挑战重试留在同一个导航档位里**(2026-07-26 实测后改)。冷 context 打 HLTV
        几乎**必吃**一次 403「Just a moment...」(实测 5/5),而一次 ``reload`` 稳定
        1.5~1.7s 放行 —— 这是 Cloudflare 正常流程,不是被封的信号。原来每个 attempt
        各自 ``async with self._navigation_slot(...)``,而闸门在**授权时**就把
        ``_next_grant_at`` 推后了 ``min_gap``,于是那次 reload 要重新排队等满一个
        min_gap(本机 120s):一次抓页实际 ≥120s、且吃掉 **2 个档位**。改成整个重试
        循环共用一个档位后,一次抓页 = 一个档位 ≈ 3s。

        同理不再「等 10s 选择器 + sleep 4s」:挑战页上 ``wait_selector`` 不可能出现
        (实测干等 40s 也不会自己放行),只给一个很短的宽限窗口兜住「挑战自行放行」
        的情况,没放行就立刻 reload。

        ``cs2_fetch_budget_seconds`` 兜住病态情况:HLTV 超时(ERR_TIMED_OUT)时单次
        导航要耗到 ``cs2_nav_timeout``,不加预算的话连续重试会把闸门长占几分钟,
        把用户命令和直播轮询全堵在后面。
        """
        attempts = max(1, self.cfg.cs2_challenge_retries)
        budget = self.cfg.cs2_fetch_budget_seconds
        manual_verify = self._manual_verify_enabled()
        manual_timeout_ms = self.cfg.cs2_manual_verify_timeout * 1000
        started = time.monotonic()
        for attempt in range(attempts):
            try:
                response = await (
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.cfg.cs2_nav_timeout,
                    )
                    if attempt == 0
                    else page.reload(
                        wait_until="domcontentloaded",
                        timeout=self.cfg.cs2_nav_timeout,
                    )
                )
                initial_title = await page.title()
                was_challenge = self._is_challenge(initial_title)
                selector_found = True
                try:
                    await page.wait_for_selector(
                        wait_selector,
                        state="attached",
                        timeout=(
                            manual_timeout_ms
                            if (manual_verify and was_challenge)
                            else (
                                self.cfg.cs2_challenge_grace_ms
                                if was_challenge
                                else 25000
                            )
                        ),
                    )
                except Exception:  # timeout is validated below, never cached
                    selector_found = False

                # 还在挑战页(宽限窗口内没自行放行)
                if was_challenge and not selector_found:
                    # 尝试 Turnstile 自动求解 (免除人工 noVNC 依赖)
                    if getattr(self.cfg, "cs2_turnstile_auto_solve", True):
                        solved = await TurnstileAutoSolver.solve(
                            page, timeout=getattr(self.cfg, "cs2_turnstile_timeout", 30)
                        )
                        if solved:
                            await self._save_storage_state()
                            try:
                                await page.wait_for_selector(
                                    wait_selector,
                                    state="attached",
                                    timeout=15000,
                                )
                                selector_found = True
                                was_challenge = False
                            except Exception:
                                pass

                    if was_challenge and not selector_found:
                        if attempt + 1 < attempts and time.monotonic() - started < budget:
                            continue
                        self._record_challenge(arm_backoff=True)
                        logger.warning(f"[cs2] Cloudflare 挑战未通过: {url}")
                        return None

                final_url = page.url
                if not self._allowed_url(final_url, _PAGE_HOSTS):
                    logger.warning(f"[cs2] 页面重定向到非白名单地址: {final_url}")
                    return None
                title = await page.title()
                html = await page.content()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] 导航失败 {url}: {exc}")
                return None

            if self._is_challenge(title):
                if attempt + 1 < attempts and time.monotonic() - started < budget:
                    continue
                self._record_challenge(arm_backoff=True)
                logger.warning(f"[cs2] Cloudflare 挑战未通过: {url}")
                return None

            # A challenge response may replace itself with a successful document;
            # in that case its initial 403 is no longer the current page status.
            if not was_challenge and (response is None or not response.ok):
                status = response.status if response else "no response"
                logger.warning(f"[cs2] HLTV 返回异常状态 {status}: {url}")
                return None
            if not selector_found:
                logger.warning(f"[cs2] 页面缺少预期选择器 {wait_selector!r}: {url}")
                return None
            if self._is_error_page(title, html):
                logger.warning(f"[cs2] 拒绝缓存错误页 {title!r}: {url}")
                return None
            if (manual_verify and was_challenge) or (
                was_challenge and await self._has_cf_clearance()
            ):
                await self._save_storage_state()
            self._record_fetch_success()
            return html
        return None

    async def get_html(
        self,
        url: str,
        wait_selector: str = "body",
        max_age: float = 0,
        stale_age: float = 0,
        priority: FetchPriority = PRIORITY_USER,
    ) -> Optional[str]:
        """Fetch an HLTV page, optionally serving a stale cache immediately.

        ``priority`` accepts ``live``, ``user``, ``scan``, or ``warm``.  Existing
        callers default to ``user``.  A stale background refresh inherits the
        caller's priority and is tracked for orderly shutdown.
        """
        self._require_allowed_url(url, _PAGE_HOSTS)
        normalized_priority = self._priority(priority)
        cached = store.cache_get(url, max_age)
        if cached:
            return cached
        if stale_age > max_age:
            stale = store.cache_get(url, stale_age)
            if stale:
                self._spawn_refresh(url, wait_selector, normalized_priority)
                return stale
        return await self._fetch(url, wait_selector, normalized_priority)

    def _spawn_refresh(
        self,
        url: str,
        wait_selector: str,
        priority: FetchPriority,
    ) -> None:
        if self._closing or url in self._refreshing:
            return
        self._refreshing.add(url)

        async def _run() -> None:
            try:
                await self._fetch(url, wait_selector, priority)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] 后台刷新失败 {url}: {exc}")
            finally:
                self._refreshing.discard(url)

        task = self._track_task(_run(), name="cs2-page-refresh")
        if task is None:
            self._refreshing.discard(url)

    async def _fetch(
        self,
        url: str,
        wait_selector: str,
        priority: FetchPriority,
    ) -> Optional[str]:
        if self._backoff_blocks(priority):
            self._metrics["suppressed"] += 1
            logger.warning(
                f"[cs2] Cloudflare 退避中,暂缓页面抓取({self._backoff_remaining():.0f}s): {url}"
            )
            return None
        self._metrics["attempts"] += 1
        if self._curl_cffi_enabled():
            html = await self._fetch_via_curl_cffi(url, priority)
            if html is not None:
                await self._cache_html(url, html)
                return html
            logger.info(f"[cs2] curl_cffi 通道失败,回退浏览器管线: {url}")

        if getattr(self.cfg, "cs2_browser_backend", "camoufox").lower() == "nodriver":
            html = await self._fetch_via_nodriver(url, priority)
            if html is not None:
                await self._cache_html(url, html)
                return html
            logger.info("[cs2] nodriver 通道未放行,回退标准浏览器管线")

        async with self._navigation_slot(priority):
            cached = store.cache_get(url, 0)
            if cached:
                return cached
            async with self._browser_operation_lock:
                await self.start()
                self._browser_uses += 1
                page = None
                try:
                    ctx = await self._new_context()
                    page = await ctx.new_page()
                    await self._guard_top_level_navigation(page, _PAGE_HOSTS)
                    html = await self._navigate_html(page, url, wait_selector, priority)
                    if html is not None:
                        await self._cache_html(url, html)
                    else:
                        self._metrics["errors"] += 1
                    return html
                finally:
                    if page is not None:
                        await self._close_page(page)
                    await self._recycle_browser_if_needed()

    @staticmethod
    async def _cache_html(url: str, html: str) -> None:
        # 内存副本同步写(后续 cache_get 立刻命中),1~2MB 的落盘丢线程里做,
        # 别让一次页面写堵住整个 bot(直播轮询、投递重试都在同一个循环上)。
        store.cache_set_mem(url, html)
        await asyncio.to_thread(store.cache_write_disk, url, html)

    # HLTV typeahead 搜索端点 /search?term= 返回 JSON,直接 goto 会被 Chromium 的 JSON
    # 视图包裹、且冷 context 常吃 Cloudflare 挑战。可靠做法:先在同一 context 落地一个
    # 普通 HLTV 页(清掉 CF),再在页面上下文里 fetch 同源端点拿原始 JSON。
    _SEARCH_JS = (
        "async (term) => {"
        "  const r = await fetch('/search?term=' + encodeURIComponent(term),"
        "    {headers: {'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});"
        "  return {status: r.status, txt: await r.text()};"
        "}"
    )

    async def fetch_search(
        self, term: str, priority: FetchPriority = PRIORITY_USER
    ) -> Optional[str]:
        """查询 HLTV typeahead 搜索,返回原始 JSON 文本(战队/选手/赛事建议)。

        仅在用户订阅命令里调用(低频)。落地 + 端点 fetch 各占一个节流档。
        """
        term = (term or "").strip()
        if not term:
            return None
        normalized_priority = self._priority(priority)
        if self._backoff_blocks(normalized_priority):
            self._metrics["suppressed"] += 1
            logger.warning(
                f"[cs2] Cloudflare 退避中,暂缓搜索({self._backoff_remaining():.0f}s): {term}"
            )
            return None
        if self._curl_cffi_enabled():
            result = await self._fetch_search_via_curl_cffi(term, normalized_priority)
            if result is not None:
                return result
            logger.info("[cs2] curl_cffi 搜索通道失败,回退 Playwright")

        async with self._navigation_slot(normalized_priority):
            async with self._browser_operation_lock:
                await self.start()
                self._browser_uses += 1
                page = None
                try:
                    ctx = await self._new_context()
                    page = await ctx.new_page()
                    await self._guard_top_level_navigation(page, _PAGE_HOSTS)
                    landed = await self._navigate_html(
                        page, hltv.URL_MATCHES, ".match", normalized_priority
                    )
                    if landed is None:
                        return None
                    result = await page.evaluate(self._SEARCH_JS, term)
                    if not isinstance(result, dict) or result.get("status") != 200:
                        logger.warning(
                            f"[cs2] 搜索失败 term={term!r} status={result.get('status') if isinstance(result, dict) else result}"
                        )
                        return None
                    return result.get("txt")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[cs2] 搜索异常 term={term!r}: {exc}")
                    return None
                finally:
                    if page is not None:
                        await self._close_page(page)
                    await self._recycle_browser_if_needed()

    def spawn_logos(
        self,
        warm_url: str,
        logo_urls: list[str],
        priority: FetchPriority = PRIORITY_WARM,
    ) -> None:
        """Fetch missing logos in a tracked background task.

        Skips URLs that failed within ``cs2_logo_fail_cooldown`` — repeatedly
        re-hitting a batch that's 403-ing (image-CDN Cloudflare block) only
        keeps our IP flagged longer; back off and let the reputation recover.
        """
        self._require_allowed_url(warm_url, _PAGE_HOSTS)
        normalized_priority = self._priority(priority)
        for url in logo_urls:
            if url:
                self._require_allowed_url(url, _ASSET_HOSTS)

        cooldown = self.cfg.cs2_logo_fail_cooldown
        now = time.time()
        if self._backoff_blocks(normalized_priority):
            self._metrics["suppressed"] += 1
            return
        fresh = [
            url
            for url in dict.fromkeys(logo_urls)
            if url
            and url not in self._logo_refreshing
            and (not cooldown or now - self._logo_failed.get(url, 0.0) >= cooldown)
        ]
        if self._closing or not fresh:
            return
        self._logo_refreshing.update(fresh)

        async def _run() -> None:
            try:
                await self.get_logos(warm_url, fresh, normalized_priority)  # 内部逐张落盘
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[cs2] 后台 logo 抓取失败: {exc}")
            finally:
                self._logo_refreshing.difference_update(fresh)

        task = self._track_task(_run(), name="cs2-logo-fetch")
        if task is None:
            self._logo_refreshing.difference_update(fresh)

    # Logo pages navigate concurrently (one Playwright page each) after one warm
    # navigation.  They deliberately skip the global min-gap navigation slot
    # (holding it for ~N*2.5s during prewarm used to starve live match polls).
    #
    # A plain GET without browser-like TLS impersonation is not reliable: the image
    # CDN tolerates a couple of such requests, then 403s bursts. The curl_cffi
    # channel above is the explicit Chrome-impersonation path; the browser fallback
    # still uses the CF-clearance cookie from one warm navigation.
    _LOGO_FETCH_CONCURRENCY = 3

    _LOGO_HASH_RE = re.compile(r"/(teamlogo|eventlogo)/([^/.?&\"']+)")
    _LOGO_URL_RE = re.compile(r"https://img-cdn\.hltv\.org/[^\s\"'<>\\)]+")

    @classmethod
    def _resigned_logo_url(cls, warm_html: str, url: str) -> Optional[str]:
        """用刚拉到的预热页里的同一张图,换掉签名已过期的旧 URL。

        img-cdn 是 imgix,URL 末尾的 ``s=`` 是签名且**会轮换**(实测约小时级)。
        页面缓存/历史记录里的旧 URL 过期后取图恒 403(``text/plain``,与
        Cloudflare 的 ``text/html`` 拦截页不同)。路径哈希不变,故可按
        “同哈希 + 同 day/night 变体(``invert=true`` 与否)”在新页面里找替身;
        ``store.logo_key()`` 也只认哈希,换签名不影响缓存命中。
        """
        m = cls._LOGO_HASH_RE.search(url or "")
        if not (m and warm_html):
            return None
        want_prefix = f"/{m.group(1)}/{m.group(2)}."
        want_invert = "invert=true" in url
        for raw in cls._LOGO_URL_RE.findall(warm_html):
            cand = html_lib.unescape(raw)
            if want_prefix not in cand or cand == url:
                continue
            if ("invert=true" in cand) != want_invert:
                continue
            return cand
        return None

    async def get_logos(
        self,
        warm_url: str,
        logo_urls: list[str],
        priority: FetchPriority = PRIORITY_WARM,
    ) -> dict[str, bytes]:
        """Fetch logo bytes through curl_cffi first, then a CF-warmed browser.

        取到的每张图**当场 ``store.save_logo`` 落盘**(调用方无需再存),再一并返回。

        Only the warm HTML page uses the navigation gate. Individual logos are
        navigated on a small pool of pages, so a 20-logo prewarm no longer burns
        ~50s of serialized navigations.

        **坑(2026-07-21 实测)**:不能用 ``ctx.request.get`` 取图。Playwright 的
        APIRequestContext 走的是 Node 自己的 HTTP 栈(TLS/HTTP2 指纹不是
        Chromium),img-cdn.hltv.org 的 Cloudflare 对它**一律 403**——冷/热
        context、补齐 Referer/Sec-Fetch-* 请求头都无用;而同一 context 里对同一
        URL ``page.goto`` 稳定 200。队标必须走真实浏览器导航。
        """
        if not logo_urls:
            return {}
        self._require_allowed_url(warm_url, _PAGE_HOSTS)
        normalized_priority = self._priority(priority)
        unique_urls = [url for url in dict.fromkeys(logo_urls) if url]
        for url in unique_urls:
            self._require_allowed_url(url, _ASSET_HOSTS)
        if not unique_urls:
            return {}

        out: dict[str, bytes] = {}
        if self._curl_cffi_enabled():
            results = await asyncio.gather(
                *(
                    self._download_logo_via_curl_cffi(url, normalized_priority)
                    for url in unique_urls
                ),
                return_exceptions=True,
            )
            failed_urls: list[str] = []
            for url, result in zip(unique_urls, results, strict=True):
                if isinstance(result, BaseException) or not result:
                    failed_urls.append(url)
                    continue
                store.save_logo(url, result)
                out[url] = result
                self._logo_failed.pop(url, None)
            if not failed_urls:
                return out
            logger.info(
                f"[cs2] curl_cffi logo 通道成功 {len(out)}/{len(unique_urls)} 张,"
                f"剩余 {len(failed_urls)} 张回退 Playwright"
            )
            unique_urls = failed_urls

        async with self._navigation_slot(normalized_priority):
            async with self._browser_operation_lock:
                await self.start()
                self._browser_uses += 1
                try:
                    return await self._get_logos_via_browser(
                        warm_url, unique_urls, normalized_priority, out
                    )
                finally:
                    await self._recycle_browser_if_needed()

    async def _get_logos_via_browser(
        self,
        warm_url: str,
        unique_urls: list[str],
        normalized_priority: FetchPriority,
        out: dict[str, bytes],
    ) -> dict[str, bytes]:
        """Browser leg of ``get_logos``. Caller holds the browser operation lock."""
        out_lock = asyncio.Lock()
        ctx = await self._new_context()
        pages = []
        try:
            page = await ctx.new_page()
            pages.append(page)
            await self._guard_top_level_navigation(page, _ASSET_HOSTS)
            warm_html = await self._navigate_html(
                page,
                warm_url,
                ".mapholder, body",
                normalized_priority,
            )
            if warm_html is None:
                logger.warning(f"[cs2] logo 预热页验证失败: {warm_url}")
                return out

            pending: asyncio.Queue[str] = asyncio.Queue()
            for url in unique_urls:
                pending.put_nowait(url)

            async def _one(logo_page, url: str) -> None:
                """取一张图;签名过期(403 text/plain)则用预热页里的新签名重试一次。"""
                target = url
                for attempt in (0, 1):
                    try:
                        response = await logo_page.goto(
                            target, timeout=self.cfg.cs2_nav_timeout
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"[cs2] logo 抓取失败 {target}: {exc}")
                        break
                    if response is None:
                        logger.warning(f"[cs2] logo 无响应 {target}")
                        break
                    if not self._allowed_url(logo_page.url, _ASSET_HOSTS):
                        logger.warning(f"[cs2] logo 重定向到非白名单地址: {logo_page.url}")
                        break
                    content_type = (response.headers.get("content-type", "") or "").lower()
                    if response.ok and content_type.startswith("image/"):
                        try:
                            body = await response.body()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(f"[cs2] logo 读取失败 {target}: {exc}")
                            break
                        if body:
                            # 逐张落盘:一批几十张要几十秒,别等整批跑完才可用,
                            # 也不让关机取消把已抓到的字节全丢掉。
                            store.save_logo(url, body)
                            async with out_lock:
                                out[url] = body
                            self._logo_failed.pop(url, None)  # recovered
                            return
                    # imgix 签名过期返回 text/plain 403;Cloudflare 拦截返回 text/html
                    stale_sig = response.status == 403 and content_type.startswith("text/plain")
                    resigned = (
                        self._resigned_logo_url(warm_html, target)
                        if stale_sig and attempt == 0
                        else None
                    )
                    if resigned:
                        logger.info(f"[cs2] logo 签名过期,改用预热页新链接: {url}")
                        target = resigned
                        continue
                    logger.warning(
                        f"[cs2] logo 响应无效 {target}: "
                        f"status={response.status}, content-type={content_type!r}"
                    )
                    break
                self._logo_failed[url] = time.time()

            async def _worker(logo_page) -> None:
                while True:
                    try:
                        url = pending.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    await _one(logo_page, url)

            # 预热页那个 page 直接复用(它已落地 CF),再按并发上限补几个。
            for _ in range(min(self._LOGO_FETCH_CONCURRENCY, len(unique_urls)) - 1):
                extra = await ctx.new_page()
                pages.append(extra)
                await self._guard_top_level_navigation(extra, _ASSET_HOSTS)
            await asyncio.gather(*(_worker(p) for p in pages))
            return out
        finally:
            for page in pages:
                await self._close_page(page)
