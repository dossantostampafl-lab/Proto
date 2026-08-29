from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from apps.api.app.websockets import WebSocketHub
from services.market_data.core import DataQualityIssue, DataQualityMonitor, MarketTick

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (ROOT / "apps", ROOT / "services", ROOT / "engines")
FORBIDDEN_CALL_PATTERN = re.compile(
    r"\.(?:create|place|submit|cancel)_?order\s*\(|"
    r"\.(?:withdraw|deposit|transfer|set_?leverage)\s*\(",
    re.IGNORECASE,
)
PRIVATE_ENDPOINT_PATTERN = re.compile(
    r"(?:/api/v3/order|/fapi/|/sapi/|listenKey|X-MBX-APIKEY)",
    re.IGNORECASE,
)


class ProbeWebSocket:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.headers: dict[str, str] = {}
        self.delay = delay
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        return None

    async def close(self, *, code: int, reason: str) -> None:
        return None

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(payload)


def _tick(sequence: int, *, timestamp: datetime | None = None) -> MarketTick:
    return MarketTick(
        timestamp=timestamp or datetime.now(UTC),
        venue="public-feed",
        symbol="BTCUSDT",
        bid=60_000,
        ask=60_001,
        last=60_000.5,
        volume=1,
        bid_size=2,
        ask_size=3,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_slow_websocket_peer_is_pruned_without_blocking_healthy_peer() -> None:
    hub = WebSocketHub(send_timeout_seconds=0.02)
    healthy = ProbeWebSocket()
    stalled = ProbeWebSocket(delay=1.0)
    await hub.connect("market-data", healthy)  # type: ignore[arg-type]
    await hub.connect("market-data", stalled)  # type: ignore[arg-type]

    started = perf_counter()
    await hub.broadcast("market-data", {"type": "market-data", "data": {"sequence": 1}})
    elapsed = perf_counter() - started

    assert elapsed < 0.25
    assert healthy.sent == [{"type": "market-data", "data": {"sequence": 1}}]
    assert hub.connection_count("market-data") == 1


@pytest.mark.asyncio
async def test_market_data_fanout_remains_bounded_under_connection_pressure() -> None:
    peer_count = 128
    hub = WebSocketHub(
        max_connections_per_channel=peer_count,
        send_timeout_seconds=0.25,
    )
    peers = [ProbeWebSocket() for _ in range(peer_count)]
    for peer in peers:
        assert await hub.connect("market-data", peer) is True  # type: ignore[arg-type]

    started = perf_counter()
    await hub.broadcast("market-data", {"type": "market-data", "data": {"sequence": 9}})
    elapsed = perf_counter() - started

    assert elapsed < 1.0
    assert all(len(peer.sent) == 1 for peer in peers)
    assert hub.connection_count("market-data") == peer_count


def test_quality_monitor_rejects_stale_duplicate_and_out_of_order_ticks() -> None:
    now = datetime.now(UTC)
    monitor = DataQualityMonitor(stale_after_seconds=2)

    assert monitor.evaluate(_tick(10, timestamp=now), now=now).valid is True
    duplicate = monitor.evaluate(_tick(10, timestamp=now), now=now)
    out_of_order = monitor.evaluate(_tick(9, timestamp=now), now=now)
    stale = monitor.evaluate(
        _tick(11, timestamp=now - timedelta(seconds=3)),
        now=now,
    )

    assert DataQualityIssue.DUPLICATE_SEQUENCE in duplicate.issues
    assert DataQualityIssue.OUT_OF_ORDER_SEQUENCE in out_of_order.issues
    assert DataQualityIssue.STALE_FEED in stale.issues


def test_quality_monitor_handles_50k_ticks_with_constant_key_cardinality() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=60)
    now = datetime.now(UTC)
    started = perf_counter()

    for sequence in range(50_000):
        report = monitor.evaluate(_tick(sequence, timestamp=now), now=now)
        assert report.valid

    assert perf_counter() - started < 5.0
    assert len(monitor._last_by_key) == 1


def test_production_modules_contain_no_private_execution_capability() -> None:
    production_files = [
        path
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*")
        if path.suffix in {".py", ".rs", ".ts", ".tsx"}
    ]

    findings: list[str] = []
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        if FORBIDDEN_CALL_PATTERN.search(source):
            findings.append(f"{path.relative_to(ROOT)} invokes an order/funds capability")
        if PRIVATE_ENDPOINT_PATTERN.search(source):
            findings.append(f"{path.relative_to(ROOT)} references a private exchange endpoint")

    assert findings == []
