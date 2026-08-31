from datetime import UTC, datetime
from uuid import uuid4

from apps.api.app.models import (
    Asset,
    Fill,
    MarketSnapshot,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.portfolio import PaperPortfolio
from apps.api.app.replay import ReplayFrameInput, ReplaySession, ReplayStartRequest
from apps.api.app.simulation import PaperSimulator


def _historical_request() -> ReplayStartRequest:
    timestamp = datetime(2024, 1, 2, 15, 30, tzinfo=UTC)
    return ReplayStartRequest(
        frames=[
            ReplayFrameInput(
                timestamp=timestamp,
                snapshot=MarketSnapshot(
                    symbol="BTC",
                    market_id="btc-historical",
                    bid=99.0,
                    ask=101.0,
                    bid_size=10.0,
                    ask_size=10.0,
                    volatility=0.2,
                    imbalance=0.1,
                    market_probability=0.55,
                ),
            )
        ]
    )


def test_api_replay_has_deterministic_core_fingerprint() -> None:
    first = ReplaySession()
    second = ReplaySession()

    first_status = first.start(_historical_request())
    second_status = second.start(_historical_request())

    assert first_status["fingerprint"] is not None
    assert first_status["fingerprint"] == second_status["fingerprint"]


def test_replay_frame_aligns_snapshot_to_replay_clock() -> None:
    session = ReplaySession()
    session.start(_historical_request())

    frame = session.step()

    assert frame is not None
    assert frame.snapshot.observed_at == frame.timestamp
    assert session.current_timestamp == frame.timestamp


def test_paper_simulator_accepts_historical_snapshot_on_replay_clock() -> None:
    session = ReplaySession()
    session.start(_historical_request())
    frame = session.step()
    assert frame is not None

    simulator = PaperSimulator(reference_time_provider=lambda: session.current_timestamp)
    order = SimulationOrder(
        id=uuid4(),
        market_id=frame.snapshot.market_id,
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=0.01,
        limit_price=102.0,
    )

    result = simulator.simulate(SimulationRequest(order=order, snapshot=frame.snapshot))

    assert result.accepted is True
    assert result.fill is not None


def test_timeline_changes_clear_portfolio_state() -> None:
    portfolio = PaperPortfolio()
    order = SimulationOrder(
        market_id="btc-historical",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=1.0,
        limit_price=100.0,
    )
    fill = Fill(
        order_id=order.id,
        market_id=order.market_id,
        asset=order.asset,
        side=order.side,
        filled_quantity=1.0,
        fill_price=100.0,
        fee=0.0,
        slippage_bps=0.0,
    )
    assert portfolio.apply_fill(order, fill) is True
    assert portfolio.snapshot()["open_position_count"] == 1

    session = ReplaySession(on_timeline_reset=portfolio.reset)
    session.start(_historical_request())
    assert portfolio.snapshot()["open_position_count"] == 0

    assert portfolio.apply_fill(order, fill) is True
    session.restart()
    assert portfolio.snapshot()["open_position_count"] == 0

    assert portfolio.apply_fill(order, fill) is True
    session.seek(0)
    assert portfolio.snapshot()["open_position_count"] == 0
