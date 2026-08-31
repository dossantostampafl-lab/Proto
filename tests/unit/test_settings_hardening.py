import math

import pytest
from pydantic import ValidationError

from apps.api.app.settings import Settings


def test_settings_rejects_unrecognized_runtime_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(system_mode="LIVE")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_net_edge", math.nan),
        ("minimum_confidence", math.inf),
        ("max_position", 0.0),
        ("max_notional", -1.0),
        ("max_daily_drawdown", math.inf),
        ("simulation_max_order_notional", math.inf),
        ("simulation_max_position_notional", math.nan),
        ("simulation_max_slippage_bps", math.inf),
    ],
)
def test_settings_reject_unsafe_numeric_configuration(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_settings_accepts_safe_runtime_modes() -> None:
    for mode in ("SIMULATION", "PAPER_TRADING", "HISTORICAL_REPLAY", "LIVE_MONITORING"):
        assert Settings(system_mode=mode).system_mode == mode
