from apps.api.app.mode_policy import (
    can_generate_fill,
    can_submit_external_order,
    capabilities_for,
)
from apps.api.app.models import SystemMode


def test_no_runtime_mode_can_submit_external_orders() -> None:
    for mode in SystemMode:
        assert can_submit_external_order(mode) is False
        assert capabilities_for(mode).credentials_required is False


def test_live_data_read_only_never_generates_fills() -> None:
    capabilities = capabilities_for(SystemMode.LIVE_DATA_READ_ONLY)

    assert capabilities.live_market_data is True
    assert capabilities.model_decisions is True
    assert capabilities.simulated_fills is False
    assert capabilities.external_order_submission is False


def test_shadow_trading_observes_decisions_without_fills() -> None:
    capabilities = capabilities_for(SystemMode.SHADOW_TRADING)

    assert capabilities.live_market_data is True
    assert capabilities.model_decisions is True
    assert capabilities.simulated_fills is False
    assert can_generate_fill(SystemMode.SHADOW_TRADING) is False


def test_simulation_paper_and_replay_remain_simulated_fill_modes() -> None:
    assert can_generate_fill(SystemMode.SIMULATION) is True
    assert can_generate_fill(SystemMode.PAPER_TRADING) is True
    assert can_generate_fill(SystemMode.HISTORICAL_REPLAY) is True
