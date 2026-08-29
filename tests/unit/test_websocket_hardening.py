from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.api.app.websockets import WebSocketHub


class FakeWebSocket:
    def __init__(self, *, origin: str | None = None, fail_send: bool = False) -> None:
        self.headers = {"origin": origin} if origin else {}
        self.fail_send = fail_send
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.fail_send:
            raise RuntimeError("simulated transport failure")
        self.sent.append(payload)


class CoordinatedWebSocket(FakeWebSocket):
    def __init__(self, ready: asyncio.Event, starts: list[int]) -> None:
        super().__init__()
        self._ready = ready
        self._starts = starts

    async def send_json(self, payload: dict[str, Any]) -> None:
        self._starts.append(1)
        if len(self._starts) == 2:
            self._ready.set()
        await self._ready.wait()
        await super().send_json(payload)


@pytest.mark.asyncio
async def test_hub_rejects_cross_origin_and_channel_overflow() -> None:
    hub = WebSocketHub(max_connections_per_channel=1)
    allowed = FakeWebSocket(origin="http://localhost:5173")
    overflow = FakeWebSocket(origin="http://localhost:5173")
    cross_origin = FakeWebSocket(origin="https://example.invalid")

    assert await hub.connect("fills", allowed) is True  # type: ignore[arg-type]
    assert await hub.connect("fills", overflow) is False  # type: ignore[arg-type]
    assert await hub.connect("risk", cross_origin) is False  # type: ignore[arg-type]

    assert allowed.accepted is True
    assert overflow.closed == (1013, "channel capacity reached")
    assert cross_origin.closed == (1008, "origin not allowed")


@pytest.mark.asyncio
async def test_broadcast_prunes_failed_peer_without_losing_healthy_peer() -> None:
    hub = WebSocketHub(send_timeout_seconds=0.1)
    healthy = FakeWebSocket()
    failed = FakeWebSocket(fail_send=True)
    await hub.connect("analytics", healthy)  # type: ignore[arg-type]
    await hub.connect("analytics", failed)  # type: ignore[arg-type]

    await hub.broadcast("analytics", {"type": "replay"})

    assert healthy.sent == [{"type": "replay"}]
    assert hub.connection_count("analytics") == 1


@pytest.mark.asyncio
async def test_broadcast_fanout_starts_peers_concurrently() -> None:
    ready = asyncio.Event()
    starts: list[int] = []
    hub = WebSocketHub(send_timeout_seconds=0.5)
    first = CoordinatedWebSocket(ready, starts)
    second = CoordinatedWebSocket(ready, starts)
    await hub.connect("market-data", first)  # type: ignore[arg-type]
    await hub.connect("market-data", second)  # type: ignore[arg-type]

    await hub.broadcast("market-data", {"type": "frame"})

    assert len(starts) == 2
    assert first.sent == [{"type": "frame"}]
    assert second.sent == [{"type": "frame"}]

