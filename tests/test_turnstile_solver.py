from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_cs2_results.fetcher import TurnstileAutoSolver


def test_turnstile_challenge_detection() -> None:
    # 标题检测
    assert TurnstileAutoSolver.is_challenge("Just a moment...") is True
    assert TurnstileAutoSolver.is_challenge("Attention Required! | Cloudflare") is True
    assert TurnstileAutoSolver.is_challenge("Matches | HLTV.org") is False

    # HTML 关键词检测
    html_blocked = "<html><body>Sorry, you have been blocked by Cloudflare</body></html>"
    assert TurnstileAutoSolver.is_challenge("Access", html_blocked) is True

    html_cf_ray = "<html><body>Cloudflare Ray ID: 89ab12cd Access Denied</body></html>"
    assert TurnstileAutoSolver.is_challenge("", html_cf_ray) is True

    html_turnstile_iframe = '<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2"></iframe>'
    assert TurnstileAutoSolver.is_challenge("HLTV", html_turnstile_iframe) is True

    # 正常页面
    html_normal = "<html><head><title>HLTV.org - CS2</title></head><body>Matches list</body></html>"
    assert TurnstileAutoSolver.is_challenge("HLTV.org", html_normal) is False


def test_bezier_trajectory_generation() -> None:
    start_x, start_y = 100.0, 100.0
    target_x, target_y = 500.0, 400.0
    steps = 25

    trajectory = TurnstileAutoSolver.generate_bezier_trajectory(
        start_x=start_x,
        start_y=start_y,
        target_x=target_x,
        target_y=target_y,
        steps=steps,
    )

    assert len(trajectory) == steps
    # 起始点第一步
    first_x, first_y = trajectory[0]
    assert abs(first_x - start_x) < 50
    assert abs(first_y - start_y) < 50

    # 终点对齐
    last_x, last_y = trajectory[-1]
    assert last_x == pytest.approx(target_x, abs=0.5)
    assert last_y == pytest.approx(target_y, abs=0.5)


def test_turnstile_solver_success_flow() -> None:
    # 模拟包含 Turnstile iframe 的 Playwright Page
    page = MagicMock()
    page.title = AsyncMock(side_effect=["Just a moment...", "Just a moment...", "Matches | HLTV.org"])
    page.content = AsyncMock(side_effect=[
        '<html><iframe src="https://challenges.cloudflare.com/turnstile"></iframe></html>',
        '<html><iframe src="https://challenges.cloudflare.com/turnstile"></iframe></html>',
        '<html><body>Normal HLTV Content</body></html>',
    ])

    element = MagicMock()
    element.bounding_box = AsyncMock(return_value={"x": 200, "y": 150, "width": 300, "height": 65})
    page.wait_for_selector = AsyncMock(return_value=element)

    mouse = MagicMock()
    mouse.move = AsyncMock()
    mouse.down = AsyncMock()
    mouse.up = AsyncMock()
    page.mouse = mouse

    async def run():
        return await TurnstileAutoSolver.solve(page, timeout=5)

    solved = asyncio.run(run())
    assert solved is True
    # 确认模拟鼠标操作已被调用
    assert mouse.move.call_count > 0
    mouse.down.assert_awaited_once()
    mouse.up.assert_awaited_once()


def test_turnstile_solver_timeout_when_element_missing() -> None:
    page = MagicMock()
    page.title = AsyncMock(return_value="Just a moment...")
    page.content = AsyncMock(return_value="<html>Challenge without iframe</html>")
    page.wait_for_selector = AsyncMock(return_value=None)
    page.mouse = MagicMock()

    async def run():
        return await TurnstileAutoSolver.solve(page, timeout=1)

    solved = asyncio.run(run())
    assert solved is False
