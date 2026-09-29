from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_cs2_results import main


def test_member_role_resolves_owner_and_admin_from_group_api() -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})

    owner_event = SimpleNamespace(
        get_sender_id=lambda: "42",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )
    admin_event = SimpleNamespace(
        get_sender_id=lambda: "7",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )
    member_event = SimpleNamespace(
        get_sender_id=lambda: "9",
        get_group=AsyncMock(
            return_value=SimpleNamespace(
                group_owner="42",
                group_admins=["7"],
            )
        ),
    )

    assert asyncio.run(plugin._resolve_member_role(owner_event)) == "owner"
    assert asyncio.run(plugin._resolve_member_role(admin_event)) == "admin"
    assert asyncio.run(plugin._resolve_member_role(member_event)) == "member"


def test_group_origin_falls_back_to_aiocqhttp_session() -> None:
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp", id="napcat-main")
    )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[platform]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})

    original = main.store.get_group_origin
    main.store.get_group_origin = lambda _group_id: None
    try:
        assert plugin._resolve_group_origin(123456) == (
            "napcat-main:GroupMessage:123456"
        )
        assert plugin._resolve_user_origin(9988) == "napcat-main:FriendMessage:9988"
    finally:
        main.store.get_group_origin = original


def test_html_render_requests_local_file_from_astrbot() -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    plugin.html_render = AsyncMock(return_value="C:/tmp/card.png")

    result = asyncio.run(plugin._html_render("<html></html>"))

    assert result == "C:/tmp/card.png"
    assert plugin.html_render.await_args.args[0] == "<html></html>"
    assert plugin.html_render.await_args.kwargs["return_url"] is False
    assert plugin.html_render.await_args.kwargs["options"]["type"] == "png"


def test_group_leave_and_mute_notices_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[]),
        get_config=lambda: {"admins_id": []},
    )
    plugin = main.Cs2ResultsPlugin(context=context, config={})
    dropped: list[tuple[int, str]] = []
    pruned: list[tuple[int, int]] = []
    monkeypatch.setattr(
        main,
        "drop_unreachable_subscription",
        lambda group_id, *, reason: dropped.append((group_id, reason)),
    )
    monkeypatch.setattr(
        main.store,
        "prune_user_targets",
        lambda group_id, user_id: pruned.append((group_id, user_id)) or 1,
    )
    leave_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "kick_me",
                "group_id": 10001,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )
    member_leave_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "leave",
                "group_id": 10001,
                "user_id": 123,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )

    class _Worker:
        def __init__(self) -> None:
            self.muted: list[tuple[int, int]] = []
            self.cleared: list[int] = []

        def note_group_muted(self, group_id: int, *, seconds: int) -> None:
            self.muted.append((group_id, seconds))

        def clear_group_mute(self, group_id: int) -> bool:
            self.cleared.append(group_id)
            return True

    worker = _Worker()
    monkeypatch.setattr(main, "delivery_worker", worker, raising=False)
    ban_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 10001,
                "duration": 60,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )
    lift_event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "lift_ban",
                "group_id": 10001,
                "user_id": 999,
                "self_id": 999,
            }
        ),
        get_group_id=lambda: "10001",
        get_self_id=lambda: "999",
    )

    asyncio.run(plugin.handle_onebot_notice(leave_event))
    asyncio.run(plugin.handle_onebot_notice(member_leave_event))
    asyncio.run(plugin.handle_onebot_notice(ban_event))
    asyncio.run(plugin.handle_onebot_notice(lift_event))

    assert dropped == [(10001, "机器人离群 notice sub_type=kick_me")]
    assert pruned == [(10001, 123)]
    assert worker.muted == [(10001, 60)]
    assert worker.cleared == [10001]
