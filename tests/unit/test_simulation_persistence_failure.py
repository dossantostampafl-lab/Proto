from __future__ import annotations

import pytest

from apps.api.app import main
from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    Side,
    SimulationOrder,
    SimulationRequest,
)


class FailingJournal:
    async def append(self, order, fill) -> None:
        del order, fill
        raise RuntimeError("database unavailable")


def _request() -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-usd-paper",
            asset=Asset.BTC,
            side=Side.BUY,
            quantity=0.01,
            limit_price=61_000.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-usd-paper",
            bid=60_000.0,
            ask=60_010.0,
        ),
    )


@pytest.mark.asyncio
async def test_persistence_failure_does_not_mutate_paper_portfolio(monkeypatch) -> None:
    main.portfolio.reset()
    main.metrics.reset()
    monkeypatch.setattr(main, "persistent_journal", FailingJournal())

    result = await main.simulate(_request())

    assert result.accepted is False
    assert result.fill is None
    assert result.reason == "simulation persistence unavailable"
    assert main.portfolio.snapshot()["positions"] == []
    counters = main.metrics.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["simulation_persistence_failures"] == 1
    assert counters.get("simulation_accepted", 0) == 0
    assert counters["simulation_rejected"] == 1
