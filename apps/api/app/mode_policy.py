from __future__ import annotations

from dataclasses import dataclass

from .models import SystemMode


@dataclass(frozen=True)
class ModeCapabilities:
    live_market_data: bool
    model_decisions: bool
    simulated_fills: bool
    external_order_submission: bool
    credentials_required: bool


_CAPABILITIES: dict[SystemMode, ModeCapabilities] = {
    SystemMode.SIMULATION: ModeCapabilities(
        live_market_data=False,
        model_decisions=True,
        simulated_fills=True,
        external_order_submission=False,
        credentials_required=False,
    ),
    SystemMode.PAPER_TRADING: ModeCapabilities(
        live_market_data=True,
        model_decisions=True,
        simulated_fills=True,
        external_order_submission=False,
        credentials_required=False,
    ),
    SystemMode.HISTORICAL_REPLAY: ModeCapabilities(
        live_market_data=False,
        model_decisions=True,
        simulated_fills=True,
        external_order_submission=False,
        credentials_required=False,
    ),
    SystemMode.LIVE_DATA_READ_ONLY: ModeCapabilities(
        live_market_data=True,
        model_decisions=True,
        simulated_fills=False,
        external_order_submission=False,
        credentials_required=False,
    ),
    SystemMode.SHADOW_TRADING: ModeCapabilities(
        live_market_data=True,
        model_decisions=True,
        simulated_fills=False,
        external_order_submission=False,
        credentials_required=False,
    ),
}


def capabilities_for(mode: SystemMode) -> ModeCapabilities:
    return _CAPABILITIES[mode]


def can_generate_fill(mode: SystemMode) -> bool:
    return capabilities_for(mode).simulated_fills


def can_submit_external_order(mode: SystemMode) -> bool:
    return capabilities_for(mode).external_order_submission
