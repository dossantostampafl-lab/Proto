from datetime import UTC, datetime, timedelta

from apps.api.app.models import Asset, MarketSnapshot, Side, SimulationOrder, SimulationRequest
from apps.api.app.simulation import PaperSimulator, SimulationConfig


def _request(*, observed_at: datetime) -> SimulationRequest:
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
            observed_at=observed_at,
        ),
    )


def test_paper_simulator_rejects_stale_market_snapshot() -> None:
    simulator = PaperSimulator(SimulationConfig(max_snapshot_age_seconds=5.0))

    result = simulator.simulate(
        _request(observed_at=datetime.now(UTC) - timedelta(seconds=6))
    )

    assert result.accepted is False
    assert result.reason == "stale market snapshot"
    assert result.fill is None


def test_paper_simulator_rejects_excessive_future_clock_skew() -> None:
    simulator = PaperSimulator(SimulationConfig(max_future_skew_seconds=1.0))

    result = simulator.simulate(
        _request(observed_at=datetime.now(UTC) + timedelta(seconds=2))
    )

    assert result.accepted is False
    assert result.reason == "market snapshot timestamp is in the future"
    assert result.fill is None


def test_paper_simulator_accepts_fresh_market_snapshot() -> None:
    simulator = PaperSimulator()

    result = simulator.simulate(_request(observed_at=datetime.now(UTC)))

    assert result.accepted is True
    assert result.fill is not None
