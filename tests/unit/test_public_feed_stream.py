import asyncio
import json

import pytest

import services.market_data.live as live_module
from services.market_data.live import CoinbasePublicMarketDataAdapter


class FakeWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = list(messages)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_: object) -> None:
        return None


def _ticker_message() -> str:
    return json.dumps(
        {
            "channel": "ticker",
            "timestamp": "2026-08-29T20:15:00Z",
            "sequence_num": 42,
            "events": [
                {
                    "tickers": [
                        {
                            "product_id": "BTC-USD",
                            "price": "61000.25",
                            "best_bid": "61000.00",
                            "best_ask": "61000.50",
                            "best_bid_quantity": "1.2",
                            "best_ask_quantity": "0.8",
                            "volume_24_h": "123.4",
                        }
                    ]
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_public_stream_recovers_from_one_bad_frame_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(["not-json", _ticker_message()])
    monkeypatch.setattr(
        live_module,
        "connect",
        lambda *_args, **_kwargs: FakeConnection(websocket),
    )
    adapter = CoinbasePublicMarketDataAdapter(
        message_timeout_seconds=1.0,
        max_consecutive_parse_errors=2,
    )
    stream = adapter.stream()

    tick = await anext(stream)

    assert tick.symbol == "BTC"
    active = adapter.health()
    assert active.connected is True
    assert active.connection_generation == 1
    assert active.reconnect_count == 0
    assert active.parse_error_count == 1
    assert active.consecutive_parse_errors == 0
    assert active.ticks_emitted == 1

    subscriptions = [json.loads(message) for message in websocket.sent]
    assert subscriptions == [
        {
            "type": "subscribe",
            "product_ids": ["BTC-USD", "ETH-USD", "SOL-USD"],
            "channel": "ticker",
        },
        {"type": "subscribe", "channel": "heartbeats"},
    ]
    assert all("key" not in payload and "token" not in payload for payload in subscriptions)

    await stream.aclose()

    closed = adapter.health()
    assert closed.connected is False
    assert closed.connected_since is None
