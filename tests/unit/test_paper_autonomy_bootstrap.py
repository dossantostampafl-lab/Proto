from __future__ import annotations

import pytest

from apps.api.app.paper_autonomy_bootstrap import load_bootstrap_config


def _enabled_env() -> dict[str, str]:
    return {
        "PROTO_PAPER_AUTONOMY_ENABLED": "true",
        "PROTO_PAPER_AUTONOMY_SYMBOL": "BTC",
        "PROTO_PAPER_AUTONOMY_IMBALANCE_TRIGGER": "0.65",
        "PROTO_PAPER_AUTONOMY_COOLDOWN_SECONDS": "20",
        "PROTO_PAPER_AUTONOMY_QUANTITY": "0.001",
        "PROTO_PAPER_AUTONOMY_MAX_SPREAD_BPS": "20",
        "PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION": "0.02",
    }


def test_bootstrap_is_disabled_by_default() -> None:
    assert load_bootstrap_config({}) is None


def test_enabled_bootstrap_requires_every_execution_parameter() -> None:
    env = _enabled_env()
    env.pop("PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION")

    with pytest.raises(ValueError, match="STOP_LOSS_FRACTION"):
        load_bootstrap_config(env)


def test_enabled_bootstrap_builds_explicit_paper_config() -> None:
    config = load_bootstrap_config(_enabled_env())

    assert config is not None
    assert config.symbol == "BTC"
    assert config.quantity == pytest.approx(0.001)
    assert config.imbalance_trigger == pytest.approx(0.65)
    assert config.cooldown_seconds == pytest.approx(20.0)
    assert config.max_spread_bps == pytest.approx(20.0)
    assert config.stop_loss_fraction == pytest.approx(0.02)


def test_invalid_stop_loss_is_rejected_before_runtime_start() -> None:
    env = _enabled_env()
    env["PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION"] = "0"

    with pytest.raises(ValueError):
        load_bootstrap_config(env)
