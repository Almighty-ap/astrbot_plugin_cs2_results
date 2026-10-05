from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
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
    ) -> None:
        self.url = url
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    calls: ClassVar[list[dict[str, object]]] = []
    responses: ClassVar[list[_FakeResponse]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls.append(kwargs)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        if not self.responses:
            raise RuntimeError("no fake response configured")
        return self.responses.pop(0)


@pytest.fixture
def curl_cffi_stub(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.calls.clear()
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
