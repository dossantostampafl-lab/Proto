import asyncio
import json

import pytest

import services.market_data.public_l2_live as l2_live_module
from services.market_data.public_l2_live import CoinbasePublicL2StreamAdapter


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


def _l2_message(
    sequence: int,
    *,
    event_type: str = "snapshot",
    bid_quantity: str = "1.2",
) -> str:
    return json.dumps(
        {
            "channel": "l2_data",
            "timestamp": f"2026-08-31T21:00:{sequence % 60:02d}Z",
            "sequence_num": sequence,
            "events": [
                {
                    "type": event_type,
                    "product_id": "BTC-USD",
                    "updates": [
                        {
                            "side": "bid",
                            "event_time": "2026-08-31T21:00:00Z",
                            "price_level": "61000.00",
                            "new_quantity": bid_quantity,
                        },
                        {
                            "side": "offer",
                            "event_time": "2026-08-31T21:00:00Z",
                            "price_level": "61000.50",
                            "new_quantity": "0.8",
                        },
                    ],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_public_l2_stream_subscribes_without_credentials_and_emits_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket([_l2_message(10)])
    monkeypatch.setattr(
        l2_live_module,
        "connect",
        lambda *_args, **_kwargs: FakeConnection(websocket),
    )
    adapter = CoinbasePublicL2StreamAdapter(message_timeout_seconds=1.0)
    stream = adapter.stream()

    snapshot = await anext(stream)

    assert snapshot.asset == "BTC"
    assert snapshot.bids[0].price == 61000.0
    assert snapshot.asks[0].price == 61000.5
    health = adapter.health()
    assert health.connected is True
    assert health.connection_generation == 1
    assert health.frames_received == 1
    assert health.snapshots_emitted == 1
    assert health.last_sequence == 10

    subscriptions = [json.loads(message) for message in websocket.sent]
    assert subscriptions == [
        {
            "type": "subscribe",
            "product_ids": ["BTC-USD", "ETH-USD", "SOL-USD"],
            "channel": "level2",
        },
        {"type": "subscribe", "channel": "heartbeats"},
    ]
    assert all(
        "key" not in payload
        and "token" not in payload
        and "jwt" not in payload
        for payload in subscriptions
    )

    replay = adapter.connection_replay_session("stream-test", seed=3)
    assert len(replay.events) == 1
    assert replay.events[0].payload["wire_sequence"] == 10
    assert len(adapter.connection_corpus_fingerprint()) == 64

    await stream.aclose()
    assert adapter.health().connected is False


@pytest.mark.asyncio
async def test_public_l2_stream_reconnects_and_requires_fresh_snapshot_after_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeWebSocket([_l2_message(20), _l2_message(22, event_type="update")])
    second = FakeWebSocket([_l2_message(40)])
    connections = [first, second]

    def fake_connect(*_args: object, **_kwargs: object) -> FakeConnection:
        if not connections:
            raise AssertionError("unexpected extra reconnect")
        return FakeConnection(connections.pop(0))

    monkeypatch.setattr(l2_live_module, "connect", fake_connect)
    adapter = CoinbasePublicL2StreamAdapter(
        reconnect_min_seconds=0.001,
        reconnect_max_seconds=0.002,
        message_timeout_seconds=1.0,
    )
    stream = adapter.stream()

    first_snapshot = await anext(stream)
    recovered_snapshot = await anext(stream)

    assert first_snapshot.bids[0].size == 1.2
    assert recovered_snapshot.bids[0].size == 1.2
    health = adapter.health()
    assert health.connection_generation == 2
    assert health.connection_attempts == 2
    assert health.reconnect_count == 1
    assert health.integrity_error_count == 1
    assert health.last_sequence == 40
    assert len(adapter.connection_replay_session("recovered").events) == 1

    await stream.aclose()


def test_public_l2_stream_rejects_non_public_products_and_unsafe_endpoint() -> None:
    with pytest.raises(ValueError, match="unsupported public L2 products"):
        CoinbasePublicL2StreamAdapter(products=("DOGE-USD",))

    with pytest.raises(ValueError, match="public endpoint"):
        CoinbasePublicL2StreamAdapter(endpoint="wss://example.com")
