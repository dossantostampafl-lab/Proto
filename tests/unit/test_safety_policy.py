import pytest
from pydantic import ValidationError

from apps.api.app.safety_policy import SafetyPolicyError, policy_snapshot, validate_sandbox_mode
from apps.api.app.settings import Settings


@pytest.mark.parametrize(
    "mode",
    ["SIMULATION", "PAPER_TRADING", "HISTORICAL_REPLAY"],
)
def test_allowed_sandbox_modes(mode: str) -> None:
    state = validate_sandbox_mode(mode)

    assert state.synthetic_only is True
    assert state.real_money_execution is False
    assert mode in state.allowed_modes


def test_policy_rejects_live_mode() -> None:
    with pytest.raises(SafetyPolicyError):
        validate_sandbox_mode("LIVE")


def test_settings_fail_closed_for_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(system_mode="REAL_MONEY")


def test_policy_snapshot_is_explicit_about_execution_boundary() -> None:
    snapshot = policy_snapshot("SIMULATION")

    assert snapshot["synthetic_only"] is True
    assert snapshot["real_money_execution"] is False
    assert snapshot["allowed_modes"] == [
        "HISTORICAL_REPLAY",
        "PAPER_TRADING",
        "SIMULATION",
    ]
