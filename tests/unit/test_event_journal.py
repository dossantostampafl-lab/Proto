from fastapi.testclient import TestClient

from apps.api.app.main import app
from services.events.journal import HashChainJournal, ResearchEvent

client = TestClient(app)


def test_hash_chain_links_and_verifies_research_events() -> None:
    journal = HashChainJournal()
    first = journal.append(
        ResearchEvent(
            event_type="MARKET_DATA",
            payload={"market_id": "btc-usd-replay", "sequence": 1},
        )
    )
    second = journal.append(
        ResearchEvent(
            event_type="MODEL_ESTIMATE",
            payload={"probability": 0.61, "model": "baseline-logit-v0"},
        )
    )

    assert first.previous_hash == "GENESIS"
    assert second.previous_hash == first.record_hash
    assert journal.verify() is True

    second.record_hash = "tampered"
    assert journal.verify() is False


def test_research_event_api_appends_and_verifies_chain() -> None:
    response = client.post(
        "/research/events",
        json={
            "event_type": "EDGE_EVALUATION",
            "payload": {"market_id": "btc-threshold", "decision": "REJECT"},
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["event"]["event_type"] == "EDGE_EVALUATION"
    assert len(body["record_hash"]) == 64

    verification = client.get("/research/events/verify").json()
    assert verification["valid"] is True
    assert verification["count"] >= 1
    assert verification["mode"] == "SIMULATION"


def test_replay_api_returns_summary_and_window() -> None:
    payload = {
        "events": [
            {
                "sequence": 10,
                "event_type": "ORDER_BOOK",
                "observed_at": "2030-01-01T00:00:00Z",
                "data": {
                    "market_id": "eth-usd-replay",
                    "asset": "ETH",
                    "bids": [{"price": 4_000, "size": 2}],
                    "asks": [{"price": 4_001, "size": 3}],
                    "observed_at": "2030-01-01T00:00:00Z",
                    "source": "HISTORICAL_FIXTURE",
                },
            },
            {
                "sequence": 11,
                "event_type": "BINARY_CONTRACT",
                "observed_at": "2030-01-01T00:00:01Z",
                "data": {
                    "market_id": "eth-above-threshold",
                    "underlying_asset": "ETH",
                    "yes_bid": 0.48,
                    "yes_ask": 0.50,
                    "observed_at": "2030-01-01T00:00:01Z",
                    "expires_at": "2030-01-02T00:00:00Z",
                    "source": "HISTORICAL_FIXTURE",
                },
            },
        ]
    }

    summary = client.post("/research/replay/summary", json=payload)
    assert summary.status_code == 200
    assert summary.json()["count"] == 2
    assert summary.json()["sequence_gaps"] == []

    window = client.post(
        "/research/replay/window?after_sequence=10&limit=1",
        json=payload,
    )
    assert window.status_code == 200
    assert len(window.json()) == 1
    assert window.json()[0]["sequence"] == 11
    assert window.json()[0]["event_type"] == "BINARY_CONTRACT"
