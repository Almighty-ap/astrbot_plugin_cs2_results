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
