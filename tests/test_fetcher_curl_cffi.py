from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import types
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_cs2_results import fetcher as fetcher_module
from astrbot_plugin_cs2_results import store
from astrbot_plugin_cs2_results.config import Config
from astrbot_plugin_cs2_results.fetcher import Fetcher


class _FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        text: str = "",
        content: bytes = b"",
        content_type: str = "text/html",
        status_code: int = 200,
        etag: str = "",
        last_modified: str = "",
    ) -> None:
        self.url = url
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if etag:
            self.headers["etag"] = etag
        if last_modified:
            self.headers["last-modified"] = last_modified

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    calls: ClassVar[list[dict[str, object]]] = []
    get_calls: ClassVar[list[dict[str, object]]] = []
    responses: ClassVar[list[_FakeResponse]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls.append(kwargs)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        if not self.responses:
            raise RuntimeError("no fake response configured")
        return self.responses.pop(0)


@pytest.fixture
def curl_cffi_stub(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.calls.clear()
    _FakeSession.get_calls.clear()
    _FakeSession.responses.clear()
    package = types.ModuleType("curl_cffi")
    package.__path__ = []  # type: ignore[attr-defined]
    requests = types.ModuleType("curl_cffi.requests")
    requests.AsyncSession = _FakeSession  # type: ignore[attr-defined]
    package.requests = requests  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "curl_cffi", package)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests)
    return _FakeSession


def test_curl_cffi_page_fetch_uses_chrome_and_explicit_proxy(
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    _FakeSession.responses.append(
        _FakeResponse(
            url="https://www.hltv.org/matches",
            text="<html><title>Matches | HLTV.org</title><body></body></html>",
        )
    )
    fetcher = Fetcher(
        Config(
            cs2_use_curl_cffi=True,
            cs2_browser_backend="playwright",
            cs2_proxy_url="http://127.0.0.1:7890",
            cs2_request_min_gap=0,
            cs2_nav_timeout=1000,
        )
    )
    def cache_set_mem(*_args: object) -> None:
        return None

    def cache_write_disk(*_args: object) -> None:
        return None

    monkeypatch.setattr(store, "cache_set_mem", cache_set_mem)
    monkeypatch.setattr(store, "cache_write_disk", cache_write_disk)
    fetcher.start = AsyncMock(side_effect=AssertionError("browser must not start"))

    async def run() -> str | None:
        return await fetcher._fetch("https://www.hltv.org/matches", ".match", "user")

    html = asyncio.run(run())

    assert "Matches | HLTV.org" in (html or "")
    assert _FakeSession.calls == [
        {
            "impersonate": "chrome",
            "proxy": "http://127.0.0.1:7890",
            "timeout": 5.0,
        }
    ]
    asyncio.run(fetcher.shutdown())


def test_conditional_text_fetch_sends_validators_and_handles_304(
    curl_cffi_stub: type[_FakeSession],
) -> None:
    _FakeSession.responses.extend(
        [
            _FakeResponse(
                url="https://www.hltv.org/rss/news",
                content_type="application/rss+xml",
                text="<rss><channel/></rss>",
                etag='"rss-v1"',
                last_modified="Tue, 06 Oct 2026 12:00:00 GMT",
            ),
            _FakeResponse(
                url="https://www.hltv.org/rss/news",
                content_type="application/rss+xml",
                status_code=304,
            ),
        ]
    )
    fetcher = Fetcher(Config(cs2_use_curl_cffi=True, cs2_request_min_gap=0))

    async def run():
        first = await fetcher.fetch_impersonated_text_conditional(
            "https://www.hltv.org/rss/news",
            accept="application/rss+xml",
        )
        second = await fetcher.fetch_impersonated_text_conditional(
            "https://www.hltv.org/rss/news",
            accept="application/rss+xml",
            etag=first.etag,
            last_modified=first.last_modified,
        )
        return first, second

    first, second = asyncio.run(run())

    assert first.text == "<rss><channel/></rss>"
    assert first.etag == '"rss-v1"'
    assert first.last_modified == "Tue, 06 Oct 2026 12:00:00 GMT"
    assert second.not_modified is True
    assert _FakeSession.get_calls[1]["headers"] == {
        "Accept": "application/rss+xml",
        "If-None-Match": '"rss-v1"',
        "If-Modified-Since": "Tue, 06 Oct 2026 12:00:00 GMT",
    }
    asyncio.run(fetcher.shutdown())


def test_cloudflare_backoff_pauses_low_priority_but_keeps_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetcher_module.random, "uniform", lambda _low, _high: 1.0)
    fetcher = Fetcher(Config(cs2_request_min_gap=0))

    fetcher._record_challenge(arm_backoff=True)

    assert fetcher._backoff_blocks("scan") is True
    assert fetcher._backoff_blocks("warm") is True
    assert fetcher._backoff_blocks("live") is False

    fetcher._record_fetch_success()

    assert fetcher._backoff_blocks("scan") is False


def test_curl_cffi_page_falls_back_after_cloudflare_challenge(
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    _FakeSession.responses.append(
        _FakeResponse(
            url="https://www.hltv.org/matches",
            text="<html><title>Just a moment...</title></html>",
        )
    )
    fetcher = Fetcher(
        Config(cs2_use_curl_cffi=True, cs2_request_min_gap=0, cs2_nav_timeout=1000)
    )
    fetcher.start = AsyncMock(side_effect=RuntimeError("playwright fallback"))

    async def run() -> None:
        with pytest.raises(RuntimeError, match="playwright fallback"):
            await fetcher._fetch("https://www.hltv.org/matches", ".match", "user")

    asyncio.run(run())
    asyncio.run(fetcher.shutdown())


def test_proxy_prefers_plugin_config_then_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:8080")
    with_config = Fetcher(
        Config(
            cs2_use_curl_cffi=True,
            cs2_proxy_url="http://configured:9000",
        )
    )
    assert with_config._curl_cffi_proxy() == "http://configured:9000"

    from_env = Fetcher(Config(cs2_use_curl_cffi=True, cs2_proxy_url=""))
    assert from_env._curl_cffi_proxy() == "http://env-proxy:8080"


def test_playwright_context_uses_same_proxy_and_is_reused() -> None:
    class _Browser:
        def __init__(self) -> None:
            self.calls = 0

        async def new_context(self, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            return kwargs

    fetcher = Fetcher(
        Config(
            cs2_use_curl_cffi=True,
            cs2_proxy_url="http://mihomo:7890",
        )
    )
    browser = _Browser()
    fetcher._browser = browser

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        return await fetcher._new_context(), await fetcher._new_context()

    first, second = asyncio.run(run())

    assert first is second
    assert browser.calls == 1
    assert first["proxy"] == {"server": "http://mihomo:7890"}


def test_browser_context_loads_saved_storage_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "playwright_storage_state.json"
    state_file.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    class _Browser:
        async def new_context(self, **kwargs: object) -> dict[str, object]:
            return kwargs

    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    monkeypatch.setattr(fetcher, "_storage_state_file", lambda: state_file)

    context = asyncio.run(fetcher._build_context(_Browser()))

    assert context["storage_state"] == str(state_file)


def test_manual_verify_curl_cffi_fast_path() -> None:
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    fetcher._fetch_via_curl_cffi = AsyncMock(
        return_value="<html><title>Matches | HLTV.org</title></html>"
    )
    result = asyncio.run(fetcher.manual_verify())
    assert result.ok is True
    assert "当前 Cloudflare 凭证有效" in result.message


def test_manual_verify_browser_auto_solve_success() -> None:
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    fetcher._fetch_via_curl_cffi = AsyncMock(return_value=None)
    fetcher.start = AsyncMock()
    page = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    fetcher._new_context = AsyncMock(return_value=context)
    fetcher._guard_top_level_navigation = AsyncMock()
    fetcher._navigate_html = AsyncMock(
        return_value="<html><title>Matches | HLTV.org</title></html>"
    )
    fetcher._has_cf_clearance = AsyncMock(return_value=True)
    fetcher._save_storage_state = AsyncMock()
    fetcher._close_page = AsyncMock()

    result = asyncio.run(fetcher.manual_verify())
    assert result.ok is True
    assert "自动求解成功" in result.message


def test_manual_verify_browser_solve_failure() -> None:
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    fetcher._fetch_via_curl_cffi = AsyncMock(return_value=None)
    fetcher.start = AsyncMock()
    page = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    fetcher._new_context = AsyncMock(return_value=context)
    fetcher._guard_top_level_navigation = AsyncMock()
    fetcher._navigate_html = AsyncMock(return_value=None)
    fetcher._close_page = AsyncMock()

    result = asyncio.run(fetcher.manual_verify())
    assert result.ok is False
    assert "未通过" in result.message


def test_manual_verify_exception_handling() -> None:
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    fetcher._fetch_via_curl_cffi = AsyncMock(return_value=None)
    fetcher.start = AsyncMock(side_effect=RuntimeError("Browser launch crash"))
    result = asyncio.run(fetcher.manual_verify())
    assert result.ok is False
    assert "Browser launch crash" in result.message


def test_page_fetch_closes_page_but_keeps_shared_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Page:
        def __init__(self) -> None:
            self.closed = False

        async def route(self, *_args: object) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    class _Context:
        def __init__(self) -> None:
            self.page = _Page()
            self.new_page_calls = 0

        async def new_page(self) -> _Page:
            self.new_page_calls += 1
            return self.page

    fetcher = Fetcher(Config(cs2_use_curl_cffi=False, cs2_request_min_gap=0))
    context = _Context()
    fetcher.start = AsyncMock()
    fetcher._new_context = AsyncMock(return_value=context)
    fetcher._navigate_html = AsyncMock(
        return_value="<html><title>Matches | HLTV.org</title></html>"
    )
    monkeypatch.setattr(store, "cache_set_mem", lambda *_args: None)
    monkeypatch.setattr(store, "cache_write_disk", lambda *_args: None)

    async def run() -> str | None:
        return await fetcher._fetch("https://www.hltv.org/matches", ".match", "user")

    html = asyncio.run(run())

    assert "Matches | HLTV.org" in (html or "")
    assert context.new_page_calls == 1
    assert context.page.closed is True
    fetcher._new_context.assert_awaited_once()


def test_shutdown_closes_persistent_context() -> None:
    class _Closable:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

        async def stop(self) -> None:
            self.closed = True

    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    context = _Closable()
    browser = _Closable()
    playwright = _Closable()
    fetcher._context = context
    fetcher._browser = browser
    fetcher._pw = playwright

    asyncio.run(fetcher.shutdown())

    assert context.closed is True
    assert browser.closed is True
    assert playwright.closed is True


def test_browser_recycle_rebuilds_playwright_stack_and_keeps_xvfb() -> None:
    class _Closable:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

        async def stop(self) -> None:
            self.closed = True

    class _Xvfb:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

    fetcher = Fetcher(
        Config(
            cs2_request_min_gap=0,
            cs2_browser_recycle_uses=1,
            cs2_browser_recycle_hours=0,
        )
    )
    context = _Closable()
    browser = _Closable()
    playwright = _Closable()
    xvfb = _Xvfb()
    fetcher._context = context
    fetcher._browser = browser
    fetcher._pw = playwright
    fetcher._xvfb_proc = xvfb  # type: ignore[assignment]
    fetcher._display = ":99"
    fetcher._browser_uses = 1

    asyncio.run(fetcher._recycle_browser_if_needed())

    assert context.closed is True
    assert browser.closed is True
    assert playwright.closed is True
    assert fetcher._context is None
    assert fetcher._browser is None
    assert fetcher._pw is None
    assert fetcher._browser_uses == 0
    assert fetcher._xvfb_proc is xvfb
    assert fetcher._display == ":99"
    assert xvfb.terminated is False


def test_xvfb_lock_owner_detection(tmp_path) -> None:
    lock = tmp_path / ".X99-lock"
    lock.write_text("999999999\n", encoding="ascii")
    assert Fetcher._xvfb_lock_owner_alive(lock) is False

    lock.write_text(f"{os.getpid()}\n", encoding="ascii")
    assert Fetcher._xvfb_lock_owner_alive(lock) is True


def test_logo_fetch_does_not_start_browser_when_curl_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    logo_url = "https://img-cdn.hltv.org/teamlogo/a.png"
    _FakeSession.responses.append(
        _FakeResponse(
            url=logo_url,
            content=b"png-bytes",
            content_type="image/png",
        )
    )
    fetcher = Fetcher(
        Config(cs2_use_curl_cffi=True, cs2_request_min_gap=0, cs2_nav_timeout=1000)
    )
    saved: dict[str, bytes] = {}
    monkeypatch.setattr(store, "save_logo", lambda url, body: saved.setdefault(url, body))
    fetcher.start = AsyncMock(side_effect=AssertionError("browser must not start"))

    async def run() -> dict[str, bytes]:
        return await fetcher.get_logos(
            "https://www.hltv.org/matches",
            [logo_url],
            priority="warm",
        )

    result = asyncio.run(run())

    assert result == {logo_url: b"png-bytes"}
    assert saved == {logo_url: b"png-bytes"}
    asyncio.run(fetcher.shutdown())


def test_storage_cookies_loaded_and_injected_into_curl_cffi(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    state_file = tmp_path / "playwright_storage_state.json"
    state_content = {
        "cookies": [
            {
                "name": "cf_clearance",
                "value": "mock_cf_clearance_123",
                "domain": ".hltv.org",
                "expires": time.time() + 3600,
            },
            {
                "name": "expired_cookie",
                "value": "old_val",
                "domain": ".hltv.org",
                "expires": time.time() - 3600,
            },
            {
                "name": "other_domain_cookie",
                "value": "other_val",
                "domain": ".google.com",
                "expires": time.time() + 3600,
            },
        ]
    }
    state_file.write_text(json.dumps(state_content), encoding="utf-8")

    fetcher = Fetcher(
        Config(cs2_use_curl_cffi=True, cs2_request_min_gap=0, cs2_nav_timeout=1000)
    )
    monkeypatch.setattr(fetcher, "_storage_state_file", lambda: state_file)

    cookies = fetcher._load_storage_cookies()
    assert cookies == {"cf_clearance": "mock_cf_clearance_123"}

    _FakeSession.responses.append(
        _FakeResponse(
            url="https://www.hltv.org/matches",
            text="<html><title>Matches | HLTV.org</title><body>Matches</body></html>",
        )
    )

    html = asyncio.run(fetcher.get_html("https://www.hltv.org/matches", priority="user"))
    assert html is not None
    assert _FakeSession.calls[-1].get("cookies") == {"cf_clearance": "mock_cf_clearance_123"}
    asyncio.run(fetcher.shutdown())


def test_logo_download_does_not_consume_gate_slot(
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    logo_url = "https://img-cdn.hltv.org/teamlogo/test.png"
    _FakeSession.responses.append(
        _FakeResponse(
            url=logo_url,
            content=b"logo-data",
            content_type="image/png",
        )
    )
    fetcher = Fetcher(
        Config(cs2_use_curl_cffi=True, cs2_request_min_gap=90.0, cs2_nav_timeout=1000)
    )
    initial_grant = fetcher._gate._next_grant_at
    initial_count = fetcher._gate._grant_count

    data = asyncio.run(fetcher._download_logo_via_curl_cffi(logo_url, priority="warm"))
    assert data == b"logo-data"
    assert fetcher._gate._next_grant_at == initial_grant
    assert fetcher._gate._grant_count == initial_count
    asyncio.run(fetcher.shutdown())


def test_close_playwright_stack_saves_storage_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    saved = False

    async def mock_save(*_args, **_kwargs):
        nonlocal saved
        saved = True

    fetcher._save_storage_state = mock_save

    class _MockCtx:
        async def close(self):
            pass

    class _MockBrowser:
        async def close(self):
            pass

    fetcher._context = _MockCtx()
    fetcher._browser = _MockBrowser()

    async def run():
        async with fetcher._lifecycle_lock:
            await fetcher._close_playwright_stack(stop_xvfb=False)

    asyncio.run(run())
    assert saved is True


def test_concurrent_logos_do_not_delay_subsequent_page_fetch(
    monkeypatch: pytest.MonkeyPatch,
    curl_cffi_stub: type[_FakeSession],
) -> None:
    fetcher = Fetcher(Config(cs2_use_curl_cffi=True, cs2_request_min_gap=90.0))
    _FakeSession.responses.append(
        _FakeResponse(
            url="https://img-cdn.hltv.org/teamlogo/1.png",
            content=b"1",
            content_type="image/png",
        )
    )
    _FakeSession.responses.append(
        _FakeResponse(
            url="https://img-cdn.hltv.org/teamlogo/2.png",
            content=b"2",
            content_type="image/png",
        )
    )
    _FakeSession.responses.append(
        _FakeResponse(
            url="https://www.hltv.org/matches",
            text="<html><title>Matches | HLTV.org</title><body>ok</body></html>",
        )
    )

    saved: dict[str, bytes] = {}
    monkeypatch.setattr(store, "save_logo", lambda url, b: saved.setdefault(url, b))

    async def run() -> None:
        await fetcher._download_logo_via_curl_cffi(
            "https://img-cdn.hltv.org/teamlogo/1.png", priority="warm"
        )
        await fetcher._download_logo_via_curl_cffi(
            "https://img-cdn.hltv.org/teamlogo/2.png", priority="warm"
        )
        assert fetcher._gate._next_grant_at == 0.0
        html = await fetcher._fetch_via_curl_cffi(
            "https://www.hltv.org/matches", priority="user"
        )
        assert html is not None
        assert fetcher._gate._grant_count == 1

    asyncio.run(run())
    asyncio.run(fetcher.shutdown())


def test_priority_user_preempts_live_and_scan() -> None:
    """验证 _FairPriorityGate 中 PRIORITY_USER (Rank 0) 优于 PRIORITY_LIVE (Rank 1) 与 SCAN。"""
    assert fetcher_module._PRIORITY_RANK[fetcher_module.PRIORITY_USER] == 0
    assert fetcher_module._PRIORITY_RANK[fetcher_module.PRIORITY_LIVE] == 1
    assert fetcher_module._PRIORITY_RANK[fetcher_module.PRIORITY_SCAN] == 2
    assert fetcher_module._PRIORITY_RANK[fetcher_module.PRIORITY_WARM] == 3

    gate = fetcher_module._FairPriorityGate(min_gap=0.01)

    async def run() -> None:
        order: list[str] = []

        # 先占用 gate
        async with gate.slot(fetcher_module.PRIORITY_SCAN):
            # 在 gate 被占用期间，排入 live 与 user 任务
            async def worker(pri: str) -> None:
                async with gate.slot(pri):
                    order.append(pri)

            t_scan = asyncio.create_task(worker(fetcher_module.PRIORITY_SCAN))
            t_live = asyncio.create_task(worker(fetcher_module.PRIORITY_LIVE))
            # 确保 scan 和 live 已经在 _waiters 队列中
            await asyncio.sleep(0.001)
            t_user = asyncio.create_task(worker(fetcher_module.PRIORITY_USER))
            await asyncio.sleep(0.001)

        # 释放后，user 应该第一个被放行，其次 live，最后 scan
        await asyncio.gather(t_scan, t_live, t_user)
        assert order == [fetcher_module.PRIORITY_USER, fetcher_module.PRIORITY_LIVE, fetcher_module.PRIORITY_SCAN]
        await gate.close()

    asyncio.run(run())

