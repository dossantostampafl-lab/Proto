import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app.live import LiveDataController, LiveDataStartRequest
from apps.api.app.main import app
from services.market_data.core import MarketTick
from services.market_data.live import (
    BINANCE_PUBLIC_STREAM_HOST,
    BinancePublicWebSocketAdapter,
    normalize_binance_book_ticker,
)


class RecordingHub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        self.messages.append((channel, payload))


class OneTickAdapter:
    async def stream(self) -> AsyncIterator[MarketTick]:
        yield MarketTick(
            timestamp=datetime.now(UTC),
            venue="test-public",
            symbol="BTCUSDT",
            bid=60_000,
            ask=60_001,
            last=60_000.5,
            volume=0,
            bid_size=1,
            ask_size=2,
            sequence=1,
        )
        await asyncio.Event().wait()


def test_normalize_public_book_ticker() -> None:
    tick = normalize_binance_book_ticker(
        {
            "E": 1_788_000_000_000,
            "s": "BTCUSDT",
            "b": "60000",
            "B": "2",
            "a": "60001",
            "A": "3",
            "u": 42,
        },
        received_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert tick.venue == "binance-public"
    assert tick.symbol == "BTCUSDT"
    assert tick.sequence == 42
    assert tick.mid == 60_000.5


def test_public_adapter_uri_is_fixed_allowlisted_and_has_no_credentials() -> None:
    adapter = BinancePublicWebSocketAdapter(symbol="BTCUSDT")

    assert adapter.uri == f"wss://{BINANCE_PUBLIC_STREAM_HOST}/ws/btcusdt@bookTicker"
    assert "?" not in adapter.uri
    assert "@" in adapter.uri.split("/", maxsplit=3)[-1]
    assert "@" not in adapter.uri.split("/", maxsplit=3)[2]


@pytest.mark.asyncio
async def test_controller_publishes_only_read_only_market_data() -> None:
    hub = RecordingHub()
    controller = LiveDataController(
        websocket_hub=hub,  # type: ignore[arg-type]
        adapter_factory=lambda _source, _symbol: OneTickAdapter(),
    )

    await controller.start(LiveDataStartRequest())
    for _ in range(20):
        if len(hub.messages) == 2:
            break
        await asyncio.sleep(0)
    status = controller.status()
    await controller.stop()

    assert status["read_only"] is True
    assert status["received"] == 1
    assert {channel for channel, _ in hub.messages} == {"market-data", "orderbook"}
    assert all(message["data"]["read_only"] is True for _, message in hub.messages)


def test_live_api_rejects_non_allowlisted_sources_and_symbols() -> None:
    with TestClient(app) as client:
        source_response = client.post(
            "/live/start", json={"source": "custom-url", "symbol": "BTCUSDT"}
        )
        symbol_response = client.post(
            "/live/start", json={"source": "binance", "symbol": "DOGEUSDT"}
        )

    assert source_response.status_code == 422
    assert symbol_response.status_code == 422
