import math

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from services.quant.synthetic_greeks import (
    BinaryContractInputs,
    probability_from_log_odds,
    synthetic_greeks,
    threshold_probability,
)

client = TestClient(app)


def test_threshold_probability_is_bounded_and_monotonic_in_spot() -> None:
    low = threshold_probability(
        BinaryContractInputs(
            spot=90.0,
            strike=100.0,
            volatility=0.30,
            time_to_expiry_years=0.25,
        )
    )
    high = threshold_probability(
        BinaryContractInputs(
            spot=110.0,
            strike=100.0,
            volatility=0.30,
            time_to_expiry_years=0.25,
        )
    )
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > low


def test_synthetic_delta_is_positive_near_threshold() -> None:
    greeks = synthetic_greeks(
        BinaryContractInputs(
            spot=100.0,
            strike=100.0,
            volatility=0.40,
            time_to_expiry_years=0.50,
        )
    )
    assert 0.0 < greeks.fair_probability < 1.0
    assert greeks.delta > 0.0
    assert all(
        math.isfinite(value)
        for value in (greeks.gamma, greeks.vega, greeks.theta_per_year)
    )


def test_tiny_positive_spot_keeps_finite_difference_domain_valid() -> None:
    greeks = synthetic_greeks(
        BinaryContractInputs(
            spot=1e-12,
            strike=1e-12,
            volatility=0.50,
            time_to_expiry_years=0.10,
        )
    )
    assert 0.0 <= greeks.fair_probability <= 1.0
    assert math.isfinite(greeks.delta)


def test_invalid_contract_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        threshold_probability(
            BinaryContractInputs(
                spot=0.0,
                strike=100.0,
                volatility=0.20,
                time_to_expiry_years=1.0,
            )
        )


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_non_finite_contract_inputs_are_rejected(invalid: float) -> None:
    with pytest.raises(ValueError):
        threshold_probability(
            BinaryContractInputs(
                spot=invalid,
                strike=100.0,
                volatility=0.20,
                time_to_expiry_years=1.0,
            )
        )


def test_log_odds_conversion_is_numerically_stable() -> None:
    assert probability_from_log_odds(1000.0) == pytest.approx(1.0)
    assert probability_from_log_odds(-1000.0) == pytest.approx(0.0)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_log_odds_conversion_rejects_non_finite_values(invalid: float) -> None:
    with pytest.raises(ValueError):
        probability_from_log_odds(invalid)


def test_synthetic_greeks_endpoint_is_explicitly_research_only() -> None:
    response = client.post(
        "/research/synthetic-greeks",
        json={
            "spot": 100.0,
            "strike": 100.0,
            "volatility": 0.35,
            "time_to_expiry_years": 0.25,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "RESEARCH_ONLY"
    assert 0.0 < body["fair_probability"] < 1.0
    assert body["delta"] > 0.0


def test_synthetic_greeks_endpoint_rejects_non_positive_spot() -> None:
    response = client.post(
        "/research/synthetic-greeks",
        json={
            "spot": 0.0,
            "strike": 100.0,
            "volatility": 0.35,
            "time_to_expiry_years": 0.25,
        },
    )
    assert response.status_code == 422
