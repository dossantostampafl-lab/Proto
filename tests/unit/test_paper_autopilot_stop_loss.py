from pydantic import ValidationError

from apps.api.app.paper_autopilot import PaperAutopilotConfig, PaperAutopilotService


def test_autopilot_requires_explicit_stop_loss_fraction() -> None:
    try:
        PaperAutopilotConfig()
    except ValidationError as error:
        assert "stop_loss_fraction" in str(error)
    else:
        raise AssertionError("autopilot must not invent a stop-loss fraction")


def test_autopilot_accepts_explicit_stop_loss_fraction() -> None:
    config = PaperAutopilotConfig(stop_loss_fraction=0.025)
    assert config.stop_loss_fraction == 0.025
    assert config.symbol == "BTC"


def test_unconfigured_service_exposes_no_synthetic_stop_loss() -> None:
    service = PaperAutopilotService()
    status = service.status()
    assert status["config"] is None
    assert status["last_stop_loss"] is None
    assert status["financial_connectivity"] is False
    assert status["real_money_execution"] is False
