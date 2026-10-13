import asyncio

import pytest
from fastapi.testclient import TestClient

from apps.api.app import core_routes
from apps.api.app.main import app
from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.settings import settings
from apps.api.app.simulation import RiskEngine
from apps.api.app.simulation_policy import authoritative_simulation_request

client = TestClient(app)


def _request(
    *,
    side: Side = Side.BUY,
    quantity: float = 1.0,
    current_quantity: float = 0.0,
    open_positions: int = 0,
    max_quantity: float = 10.0,
    max_open: int = 10,
) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-paper", asset=Asset.BTC, side=side, quantity=quantity, limit_price=100.0
        ),
        snapshot=MarketSnapshot(
            symbol="BTC", market_id="btc-paper", bid=99.0, ask=100.0, ask_size=1e6, bid_size=1e6
        ),
        current_position_quantity=current_quantity,
        current_open_positions=open_positions,
        limits=RiskLimits(
            max_order_notional=1e9,
            max_position_notional=1e9,
            max_gross_exposure=1e9,
            max_slippage_bps=1e4,
            max_position_quantity=max_quantity,
            max_open_positions=max_open,
        ),
    )


def _validate(request: SimulationRequest) -> tuple[bool, str]:
    return RiskEngine().validate(request, estimated_slippage_bps=1.0)


def test_rejects_order_that_grows_position_above_quantity_cap() -> None:
    assert _validate(_request(quantity=4.0, current_quantity=7.0)) == (
        False,
        "max position quantity exceeded",
    )


def test_risk_reducing_order_is_allowed_above_quantity_cap() -> None:
    request = _request(side=Side.SELL, quantity=1.0, current_quantity=12.0, open_positions=1)
    assert _validate(request) == (True, "accepted")


def test_rejects_new_position_when_open_position_limit_is_reached() -> None:
    assert _validate(_request(open_positions=3, max_open=3)) == (
        False,
        "max open positions exceeded",
    )


def test_adding_to_an_existing_position_ignores_open_position_limit() -> None:
    assert _validate(_request(current_quantity=1.0, open_positions=3, max_open=3)) == (
        True,
        "accepted",
    )


def test_server_policy_caps_client_limits_and_counts_open_positions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_position", 2.0)
    monkeypatch.setattr(settings, "simulation_max_open_positions", 4)
    request = _request(max_quantity=1_000.0, max_open=1_000)

    effective = authoritative_simulation_request(
        request,
        {"positions": [], "gross_exposure": 0.0, "open_position_count": 3},
        max_order_notional=1e9,
        max_position_notional=1e9,
        max_slippage_bps=1e4,
    )

    assert effective.limits.max_position_quantity == 2.0
    assert effective.limits.max_open_positions == 4
    assert effective.current_open_positions == 3


class _SlowJournal:
    """Yields to the event loop mid-commit, which is where an unlocked race would interleave."""

    async def append(self, order, fill) -> bool:
        await asyncio.sleep(0.05)
        return True


@pytest.mark.asyncio
async def test_concurrent_orders_cannot_both_pass_against_the_same_portfolio(monkeypatch) -> None:
    client.post("/simulation/reset")
    monkeypatch.setattr(core_routes, "persistent_journal", _SlowJournal())
    monkeypatch.setattr(settings, "max_position", 1.5)

    def order() -> SimulationRequest:
        return SimulationRequest(
            order=SimulationOrder(
                market_id="btc-paper",
                asset=Asset.BTC,
                side=Side.BUY,
                quantity=1.0,
                limit_price=100.0,
            ),
            snapshot=MarketSnapshot(
                symbol="BTC",
                market_id="btc-paper",
                bid=99.9,
                ask=100.0,
                ask_size=1e6,
                bid_size=1e6,
            ),
        )

    first, second = await asyncio.gather(
        core_routes.simulate(order()),
        core_routes.simulate(order()),
    )

    assert sorted([first.accepted, second.accepted]) == [False, True]
    assert "max position quantity exceeded" in {first.reason, second.reason}
    client.post("/simulation/reset")
