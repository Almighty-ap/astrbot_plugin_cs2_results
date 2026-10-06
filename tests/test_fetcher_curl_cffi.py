from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import ClassVar
from unittest.mock import AsyncMock

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


def test_manual_verify_uses_visible_chromium_window() -> None:
    fetcher = Fetcher(
        Config(cs2_manual_verify=True, cs2_request_min_gap=0)
    )

    assert fetcher._headful_enabled() is True
    args = fetcher._chromium_launch_args()
    assert "--window-position=0,0" in args


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


def test_manual_verify_saves_state_after_matches_page() -> None:
    class _Page:
        closed = False

        async def close(self) -> None:
            self.closed = True

    class _Context:
        def __init__(self) -> None:
            self.page = _Page()

        async def new_page(self) -> _Page:
            return self.page

    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    fetcher._display = ":99"
    fetcher.start = AsyncMock()
    fetcher._new_context = AsyncMock(return_value=_Context())
    fetcher._guard_top_level_navigation = AsyncMock()
    fetcher._navigate_html = AsyncMock(
        return_value="<html><title>Matches | HLTV.org</title></html>"
    )
    fetcher._has_cf_clearance = AsyncMock(return_value=True)
    fetcher._save_storage_state = AsyncMock()

    result = asyncio.run(fetcher.manual_verify())

    assert result.ok is True
    assert "cf_clearance" in result.message
    fetcher._save_storage_state.assert_awaited_once()
    assert fetcher._manual_verify_requested is False


def test_start_x11vnc_launches_temporary_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Proc:
        def poll(self) -> None:
            return None

    def fake_popen(args: list[str], **_kwargs: object) -> _Proc:
        calls.append(args)
        return _Proc()

    ready = iter((False, True))
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    monkeypatch.setattr(fetcher_module.shutil, "which", lambda _name: "/usr/bin/x11vnc")
    monkeypatch.setattr(fetcher_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fetcher, "_tcp_port_ready", lambda *_args: next(ready))

    fetcher._start_x11vnc(":99")

    assert calls
    assert calls[0][0] == "/usr/bin/x11vnc"
    assert calls[0][1:3] == ["-display", ":99"]
    assert "-forever" in calls[0]
    assert "-shared" in calls[0]


def test_start_novnc_launches_websockify_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Proc:
        def poll(self) -> None:
            return None

    def fake_popen(args: list[str], **_kwargs: object) -> _Proc:
        calls.append(args)
        return _Proc()

    ready = iter((False, True))
    fetcher = Fetcher(Config(cs2_request_min_gap=0))
    monkeypatch.setattr(fetcher_module.shutil, "which", lambda _name: "/usr/bin/websockify")
    monkeypatch.setattr(fetcher_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fetcher, "_tcp_port_ready", lambda *_args: next(ready))
    monkeypatch.setattr(fetcher_module.Path, "is_file", lambda _self: True)

    fetcher._start_novnc()

    assert calls
    assert calls[0][0] == "/usr/bin/websockify"
    assert calls[0][1].startswith("--web=")
    assert calls[0][1].endswith("novnc")
    assert "6080" in calls[0]
    assert "127.0.0.1:5900" in calls[0]


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
