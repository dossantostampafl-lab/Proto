from apps.api.app.paper_stop_loss import evaluate_stop_loss


def test_long_stop_loss_uses_executable_bid() -> None:
    decision = evaluate_stop_loss(
        position_quantity=2.0,
        average_price=100.0,
        bid=94.9,
        ask=95.1,
        stop_loss_fraction=0.05,
    )
    assert decision.triggered is True
    assert decision.side == "SELL"
    assert decision.quantity == 2.0
    assert decision.threshold_price == 95.0


def test_short_stop_loss_uses_executable_ask() -> None:
    decision = evaluate_stop_loss(
        position_quantity=-3.0,
        average_price=100.0,
        bid=105.0,
        ask=105.1,
        stop_loss_fraction=0.05,
    )
    assert decision.triggered is True
    assert decision.side == "BUY"
    assert decision.quantity == 3.0
    assert decision.threshold_price == 105.0


def test_position_within_stop_is_not_closed() -> None:
    decision = evaluate_stop_loss(
        position_quantity=1.0,
        average_price=100.0,
        bid=96.0,
        ask=96.1,
        stop_loss_fraction=0.05,
    )
    assert decision.triggered is False
    assert decision.side is None
