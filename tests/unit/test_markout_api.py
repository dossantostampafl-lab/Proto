from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _snapshot(
    observed_at: str,
    *,
    bid: float,
    ask: float,
    generation: int = 1,
    record_index: int = 0,
) -> dict[str, object]:
    return {
        "record_index": record_index,
        "connection_generation": generation,
        "wire_sequence": record_index + 1,
        "snapshot": {
            "market_id": "btc-usd",
            "asset": "BTC",
            "bids": [{"price": bid, "size": 2.0}],
            "asks": [{"price": ask, "size": 2.0}],
            "observed_at": observed_at,
            "source": "HISTORICAL_REPLAY",
        },
    }


def _fill(*, generation: int = 1) -> dict[str, object]:
    return {
        "fill_id": "fill-1",
        "side": "BUY",
        "fill_price": 100.0,
        "filled_at": "2026-08-31T12:00:00+00:00",
        "connection_generation": generation,
        "asset": "BTC",
    }


def test_markout_api_exposes_fill_and_summary_metrics() -> None:
    payload = {
        "snapshots": [
            _snapshot("2026-08-31T12:00:00+00:00", bid=99.0, ask=101.0),
            _snapshot(
                "2026-08-31T12:00:01+00:00",
                bid=100.0,
                ask=102.0,
                record_index=1,
            ),
        ],
        "fills": [_fill()],
        "horizons_ms": [1000],
    }

    response = client.post("/research/execution-quality/markout", json=payload)
    body = response.json()

    assert response.status_code == 200
    assert body["method"] == "L2_POST_FILL_MARKOUT"
    assert body["fill_count"] == 1
    assert body["markouts"][0]["entry_mid"] == 100.0
    assert body["markouts"][0]["spread_capture_bps"] == 0.0
    assert body["markouts"][0]["points"][0]["markout_bps"] == 100.0
    assert body["markouts"][0]["points"][0]["adverse_selection_bps"] == 0.0
    assert body["summary"][0]["observation_count"] == 1
    assert body["summary"][0]["mean_markout_bps"] == 100.0
    assert body["connection_boundary_policy"] == "SAME_CONNECTION_GENERATION_ONLY"
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_markout_api_does_not_cross_reconnect_generation() -> None:
    payload = {
        "snapshots": [
            _snapshot("2026-08-31T12:00:00+00:00", bid=99.0, ask=101.0),
            _snapshot(
                "2026-08-31T12:00:01+00:00",
                bid=120.0,
                ask=122.0,
                generation=2,
                record_index=1,
            ),
        ],
        "fills": [_fill()],
        "horizons_ms": [1000],
    }

    response = client.post("/research/execution-quality/markout", json=payload)
    body = response.json()

    assert response.status_code == 200
    assert body["markouts"][0]["points"][0]["future_mid"] is None
    assert body["markouts"][0]["points"][0]["markout_bps"] is None
    assert body["summary"][0]["observation_count"] == 0


def test_markout_api_rejects_unsorted_snapshots_within_generation() -> None:
    payload = {
        "snapshots": [
            _snapshot("2026-08-31T12:00:01+00:00", bid=100.0, ask=102.0),
            _snapshot(
                "2026-08-31T12:00:00+00:00",
                bid=99.0,
                ask=101.0,
                record_index=1,
            ),
        ],
        "fills": [_fill()],
        "horizons_ms": [1000],
    }

    response = client.post("/research/execution-quality/markout", json=payload)

    assert response.status_code == 422


def test_markout_api_rejects_fill_without_same_generation_snapshot() -> None:
    payload = {
        "snapshots": [
            _snapshot(
                "2026-08-31T12:00:00+00:00",
                bid=99.0,
                ask=101.0,
                generation=2,
            )
        ],
        "fills": [_fill(generation=1)],
        "horizons_ms": [1000],
    }

    response = client.post("/research/execution-quality/markout", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "no L2 snapshot is available at or after the fill"
