from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.websockets import WebSocketHub

client = TestClient(app)


class FakeWebSocket:
    def __init__(self, *, origin: str = "http://localhost:5173", fail_send: bool = False) -> None:
        self.headers = {"origin": origin}
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
            raise RuntimeError("synthetic transport failure")
        self.sent.append(payload)


def test_websocket_channels_accept_and_respond_to_ping() -> None:
    for path, channel in [
        ("/ws/market-data", "market-data"),
        ("/ws/orderbook", "orderbook"),
        ("/ws/signals", "signals"),
        ("/ws/risk", "risk"),
        ("/ws/portfolio", "portfolio"),
        ("/ws/fills", "fills"),
        ("/ws/analytics", "analytics"),
    ]:
        with client.websocket_connect(path) as websocket:
            subscribed = websocket.receive_json()
            assert subscribed == {"type": "subscribed", "channel": channel}
            websocket.send_text("ping")
            assert websocket.receive_json() == {"type": "pong", "channel": channel}


def test_simulated_fill_is_broadcast_to_fill_channel() -> None:
    client.post("/simulation/reset")
    payload = {
        "order": {
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "side": "BUY",
            "quantity": 0.01,
            "limit_price": 61_000,
        },
        "snapshot": {
            "symbol": "BTC",
            "market_id": "btc-usd-paper",
            "bid": 60_000,
            "ask": 60_010,
            "market_probability": 0.52,
        },
    }

    with client.websocket_connect("/ws/fills") as websocket:
        assert websocket.receive_json()["type"] == "subscribed"
        response = client.post("/v1/simulate", json=payload)
        assert response.status_code == 200
        event = websocket.receive_json()
        assert event["type"] == "fill"
        assert event["data"]["market_id"] == "btc-usd-paper"


@pytest.mark.asyncio
async def test_websocket_hub_rejects_untrusted_origin() -> None:
    hub = WebSocketHub(max_connections_per_channel=1)
    websocket = FakeWebSocket(origin="https://example.invalid")

    connected = await hub.connect("analytics", websocket)  # type: ignore[arg-type]

    assert connected is False
    assert websocket.accepted is False
    assert websocket.closed == (1008, "origin not allowed")
    assert hub.connection_count("analytics") == 0


@pytest.mark.asyncio
async def test_websocket_hub_enforces_channel_capacity() -> None:
    hub = WebSocketHub(max_connections_per_channel=1)
    first = FakeWebSocket()
    second = FakeWebSocket()

    assert await hub.connect("analytics", first) is True  # type: ignore[arg-type]
    connected = await hub.connect("analytics", second)  # type: ignore[arg-type]

    assert connected is False
    assert second.closed == (1013, "channel capacity reached")
    assert hub.connection_count("analytics") == 1


@pytest.mark.asyncio
async def test_websocket_hub_drops_failed_transport_without_breaking_broadcast() -> None:
    hub = WebSocketHub(send_timeout_seconds=0.1)
    healthy = FakeWebSocket()
    failing = FakeWebSocket(fail_send=True)
    await hub.connect("analytics", healthy)  # type: ignore[arg-type]
    await hub.connect("analytics", failing)  # type: ignore[arg-type]

    payload = {"type": "runtime", "data": {"scope": "SIMULATION_REPLAY_ONLY"}}
    await hub.broadcast("analytics", payload)

    assert healthy.sent == [payload]
    assert hub.connection_count("analytics") == 1
