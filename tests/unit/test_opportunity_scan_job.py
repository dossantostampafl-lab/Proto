import pytest

from apps.api.app.orchestration_state import _opportunity_scan_job


def _policies() -> dict[str, object]:
    return {
        "regime_policy": {
            "trend_threshold": 0.01,
            "strong_trend_threshold": 0.03,
            "low_volatility_threshold": 0.10,
            "high_volatility_threshold": 0.40,
        },
        "opportunity_policy": {
            "minimum_liquidity": 0.50,
            "minimum_confidence": 0.60,
            "minimum_net_edge": 0.01,
            "minimum_calibration_quality": 0.50,
            "minimum_risk_quality": 0.50,
            "edge_scale": 0.10,
            "weight_edge": 1.0,
            "weight_confidence": 1.0,
            "weight_liquidity": 1.0,
            "weight_calibration": 1.0,
            "weight_risk": 1.0,
        },
        "limit": 10,
    }


@pytest.mark.asyncio
async def test_scan_ranks_only_complete_fact_based_observations() -> None:
    payload = {
        **_policies(),
        "observations": [
            {
                "instrument_id": "CRYPTO:BTC",
                "return_signal": 0.02,
                "realized_volatility": 0.20,
                "liquidity_score": 0.90,
                "confidence": 0.80,
                "net_edge": 0.04,
                "calibration_quality": 0.85,
                "risk_quality": 0.90,
                "provenance_complete": True,
            },
            {
                "instrument_id": "US:AAPL",
                "return_signal": 0.01,
                "realized_volatility": 0.25,
                "liquidity_score": 0.95,
                "confidence": None,
                "net_edge": None,
                "calibration_quality": None,
                "risk_quality": None,
                "provenance_complete": True,
            },
        ],
    }

    result = await _opportunity_scan_job(payload)

    assert result["observation_count"] == 2
    assert result["classified_count"] == 2
    assert result["opportunity_count"] == 1
    assert result["opportunities"][0]["instrument_id"] == "CRYPTO:BTC"
    assert result["states"][1]["instrument_id"] == "US:AAPL"
    assert result["states"][1]["confidence"] is None
    assert result["states"][1]["net_edge"] is None
    assert result["incomplete_evidence_policy"] == "OMIT_FROM_RANKING"
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


@pytest.mark.asyncio
async def test_scan_rejects_missing_policy_instead_of_inventing_defaults() -> None:
    with pytest.raises(ValueError, match="explicit regime_policy"):
        await _opportunity_scan_job(
            {
                "observations": [
                    {
                        "instrument_id": "CRYPTO:BTC",
                        "return_signal": 0.0,
                        "realized_volatility": 0.2,
                        "liquidity_score": 0.9,
                        "provenance_complete": True,
                    }
                ],
                "opportunity_policy": _policies()["opportunity_policy"],
                "limit": 5,
            }
        )


@pytest.mark.asyncio
async def test_scan_rejects_implicit_limit() -> None:
    policies = _policies()
    policies.pop("limit")
    with pytest.raises(ValueError, match="integer limit"):
        await _opportunity_scan_job(
            {
                **policies,
                "observations": [
                    {
                        "instrument_id": "CRYPTO:BTC",
                        "return_signal": 0.0,
                        "realized_volatility": 0.2,
                        "liquidity_score": 0.9,
                        "provenance_complete": False,
                    }
                ],
            }
        )
