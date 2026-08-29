from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_market_detail_is_explicitly_synthetic() -> None:
    response = client.get("/markets/btc-threshold")
    body = response.json()

    assert response.status_code == 200
    assert body["asset"] == "BTC"
    assert body["source"] == "SYNTHETIC_DEMO"
    assert body["real_money_execution"] is False


def test_market_data_exposes_normalized_fields() -> None:
    response = client.get("/market-data/BTC")
    body = response.json()

    assert response.status_code == 200
    assert body["venue"] == "SYNTHETIC_DEMO"
    assert body["symbol"] == "BTC"
    assert body["bid"] < body["ask"]
    assert body["mid"] == (body["bid"] + body["ask"]) / 2
    assert body["spread"] == body["ask"] - body["bid"]


def test_orderbook_exposes_microstructure_metrics() -> None:
    response = client.get("/orderbook/BTC")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_DEMO"
    assert body["depth"] == 8.0
    assert -1.0 <= body["imbalance"] <= 1.0
    assert body["best_bid"] < body["best_ask"]
    assert body["best_bid"] <= body["microprice"] <= body["best_ask"]


def test_data_quality_surface_reports_clean_synthetic_tick() -> None:
    response = client.get("/data-quality/ETH")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_DEMO"
    assert body["valid"] is True
    assert body["issues"] == []


def test_canonical_portfolio_positions_and_pnl_use_simulation_state() -> None:
    portfolio = client.get("/portfolio")
    positions = client.get("/positions")
    pnl = client.get("/pnl")

    assert portfolio.status_code == 200
    assert positions.status_code == 200
    assert pnl.status_code == 200
    assert portfolio.json()["source"] == "SIMULATION_PORTFOLIO"
    assert positions.json()["source"] == "SIMULATION_PORTFOLIO"
    assert pnl.json()["source"] == "SIMULATION_PORTFOLIO"
    assert portfolio.json()["real_money_execution"] is False
    assert positions.json()["real_money_execution"] is False
    assert pnl.json()["real_money_execution"] is False
    assert positions.json()["count"] == len(positions.json()["positions"])
    assert pnl.json()["pnl_after_fees"] == portfolio.json()["total_pnl_after_fees"]


def test_probability_and_edge_are_research_only() -> None:
    probability = client.get("/probability/btc-threshold")
    edge = client.get("/edge/btc-threshold")

    assert probability.status_code == 200
    assert edge.status_code == 200
    assert probability.json()["source"] == "SYNTHETIC_DEMO"
    assert 0.0 <= probability.json()["probability"] <= 1.0
    assert edge.json()["decision"] in {"APPROVE_CANDIDATE", "REJECT"}
    assert edge.json()["source"] == "SYNTHETIC_DEMO"


def test_expected_value_surface_decomposes_binary_contract_value() -> None:
    response = client.get("/expected-value/btc-threshold")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_DEMO"
    assert body["contract_price"] == 0.52
    assert body["total_costs"] >= 0.0
    assert body["risk_adjusted_ev"] <= body["ev_after_costs"] <= body["ev"]


def test_synthetic_greeks_are_labeled_model_sensitivities() -> None:
    response = client.get("/analytics/greeks/btc-threshold")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_MODEL_SENSITIVITY"
    assert body["market_probability_delta"] > 0.0
    assert body["time_theta"] == 0.0
    assert "d(model_probability)" in body["definition"]["market_probability_delta"]


def test_model_calibration_does_not_invent_metrics() -> None:
    response = client.get("/models/calibration")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "NOT_COMPUTED"
    assert body["observation_count"] == 0
    assert body["brier_score"] is None
    assert body["log_loss"] is None
    assert body["expected_calibration_error"] is None


def test_hawkes_surface_is_explicitly_baseline_only() -> None:
    response = client.get("/hawkes/SOL")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_RESEARCH_BASELINE"
    assert body["event_count"] == 0
    assert body["current_intensity"] == body["baseline_intensity"]
    assert 0.0 <= body["event_probability"] <= 1.0


def test_prometheus_metrics_endpoint_is_scrapeable_text() -> None:
    client.get("/health")
    response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "proto_http_requests_total" in response.text
    assert "proto_http_latency_ms" in response.text


def test_unknown_market_and_symbol_return_404() -> None:
    assert client.get("/markets/unknown").status_code == 404
    assert client.get("/market-data/DOGE").status_code == 404


def test_existing_research_routes_keep_their_paths() -> None:
    response = client.get("/research/metrics")
    assert response.status_code == 200
