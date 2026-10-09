from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from astrbot_plugin_cs2_results import delivery
from astrbot_plugin_cs2_results.config import Config


class _FakeStore:
    def __init__(self) -> None:
        self.deferred: list[tuple[int, str]] = []
        self.sent: list[tuple[str, str, int]] = []
        self.unsubscribed: list[int] = []

    def get_subscriptions(self) -> set[int]:
        return {10001}

    def claim_due_deliveries(
        self, _worker_id: str, *, lease_seconds: int, limit: int
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                match_id="4242",
                map_key="map-1",
                group_id=10001,
                attempts=0,
                created_at=time.time(),
                mentions=(123,),
            )
        ]

    def get_delivery_payload(self, _match_id: str, _map_key: str) -> bytes:
        return b"png payload"

    def mark_delivery_sent(
        self, match_id: str, map_key: str, group_id: int, *, worker_id: str
    ) -> object:
        self.sent.append((match_id, map_key, group_id))
        return object()

    def defer_delivery(
        self,
        _match_id: str,
        _map_key: str,
        group_id: int,
        _retry_at: float,
        error: str,
        *,
        worker_id: str,
    ) -> object:
        self.deferred.append((group_id, error))
        return object()

    def mark_delivery_failed(self, *_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("unexpected permanent failure")

    def release_claim(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def release_claims(self, _worker_id: str) -> int:
        return 0

    def unsubscribe(self, group_id: int) -> bool:
        self.unsubscribed.append(group_id)
        return True


class _FakeNewsStore:
    def __init__(self) -> None:
        self.claimed = False
        self.sent: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, int | None, str]] = []
        self.deferred: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    def claim_due_deliveries(
        self, _worker_id: str, *, lease_seconds: int, limit: int
    ) -> list[SimpleNamespace]:
        return []

    def claim_due_news_deliveries(
        self, _worker_id: str, *, lease_seconds: int, limit: int
    ) -> list[SimpleNamespace]:
        if self.claimed:
            return []
        self.claimed = True
        return [
            SimpleNamespace(
                guid="news-1",
                unified_msg_origin="napcat:FriendMessage:2002",
                group_id=0,
                attempts=0,
                created_at=time.time(),
                mentions=(123,),
            )
        ]

    def news_subscribers(self) -> set[str]:
        return {"napcat:FriendMessage:2002"}

    def get_news_delivery_payload(self, _guid: str) -> bytes:
        return "news text".encode()

    def get_news_delivery_payload_kind(self, _guid: str) -> str:
        return "text"

    def mark_news_delivery_sent(
        self, guid: str, unified_msg_origin: str, *, worker_id: str
    ) -> object:
        self.sent.append((guid, unified_msg_origin))
        return object()

    def mark_news_delivery_failed(
        self,
        guid: str,
        unified_msg_origin: str,
        _error: str,
        *,
        next_retry_at: float | None = None,
        dead: bool = False,
        max_attempts: int | None = None,
        worker_id: str,
    ) -> object:
        self.failed.append((guid, unified_msg_origin, next_retry_at, worker_id))
        return SimpleNamespace(status="dead" if dead else "retry")

    def defer_news_delivery(
        self,
        guid: str,
        unified_msg_origin: str,
        _retry_at: float,
        _error: str,
        *,
        worker_id: str,
    ) -> object:
        self.deferred.append((guid, unified_msg_origin))
        return object()

    def cancel_news_deliveries_for_umo(
        self, unified_msg_origin: str, *, reason: str = ""
    ) -> int:
        self.cancelled.append(unified_msg_origin)
        return 1

    def news_unsubscribe(self, unified_msg_origin: str) -> bool:
        self.cancelled.append(unified_msg_origin)
        return True


def test_successful_active_delivery_uses_umo_and_mentions(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(send_message=AsyncMock(return_value=True))
    worker = delivery.DeliveryWorker(
        Config(),
        context=context,
        origin_resolver=lambda group_id: f"napcat:GroupMessage:{group_id}",
    )

    result = asyncio.run(worker.run_once())

    assert result.sent == 1
    assert fake_store.sent == [("4242", "map-1", 10001)]
    umo, chain = context.send_message.await_args.args
    assert umo == "napcat:GroupMessage:10001"
    assert len(chain.chain) == 2


def test_missing_origin_defers_without_consuming_attempt(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(send_message=AsyncMock(return_value=True))
    worker = delivery.DeliveryWorker(Config(), context=context, origin_resolver=lambda _gid: None)

    result = asyncio.run(worker.run_once())

    assert result.deferred == 1
    assert fake_store.sent == []
    assert fake_store.deferred[0][1].startswith("unified_msg_origin unavailable")
    context.send_message.assert_not_awaited()


def test_muted_group_is_deferred_without_exponential_retry(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("机器人已被禁言"))
    )
    worker = delivery.DeliveryWorker(
        Config(),
        context=context,
        origin_resolver=lambda group_id: f"napcat:GroupMessage:{group_id}",
    )

    result = asyncio.run(worker.run_once())

    assert result.deferred == 1
    assert result.retried == 0
    assert fake_store.sent == []
    assert "禁言中" in fake_store.deferred[0][1]


def test_send_timeout_is_closed_without_retry(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(
        send_message=AsyncMock(side_effect=TimeoutError())
    )
    worker = delivery.DeliveryWorker(
        Config(),
        context=context,
        origin_resolver=lambda group_id: f"napcat:GroupMessage:{group_id}",
    )

    result = asyncio.run(worker.run_once())

    assert result.sent == 1
    assert result.retried == 0
    assert fake_store.sent == [("4242", "map-1", 10001)]


def test_news_delivery_uses_stored_umo_and_text_payload(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeNewsStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(send_message=AsyncMock(return_value=True))
    worker = delivery.DeliveryWorker(Config(), context=context)

    result = asyncio.run(worker.run_once())

    assert result.sent == 1
    assert fake_store.sent == [("news-1", "napcat:FriendMessage:2002")]
    umo, chain = context.send_message.await_args.args
    assert umo == "napcat:FriendMessage:2002"
    assert chain.chain[0].qq == 123
    assert chain.chain[1].text == "news text"


def test_news_delivery_failure_enters_retry(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeNewsStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("network unavailable"))
    )
    worker = delivery.DeliveryWorker(Config(), context=context)

    result = asyncio.run(worker.run_once())

    assert result.retried == 1
    assert fake_store.sent == []
    assert fake_store.failed[0][0] == "news-1"
    assert fake_store.failed[0][3] == worker._worker_id


def test_news_send_timeout_is_closed_without_retry(
    monkeypatch: Any,
) -> None:
    fake_store = _FakeNewsStore()
    monkeypatch.setattr(delivery, "store", fake_store)
    context = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("Timeout: sendMsg"))
    )
    worker = delivery.DeliveryWorker(Config(), context=context)

    result = asyncio.run(worker.run_once())

    assert result.sent == 1
    assert result.retried == 0
    assert fake_store.sent == [("news-1", "napcat:FriendMessage:2002")]
