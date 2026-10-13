from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.execution import OrderStatus, OrderType, TradableInstrument, VenueId
from services.execution.venue import (
    PaperVenueAdapter,
    Quote,
    VenueAdapter,
    VenueOrder,
)
from services.market_data.instruments import AssetClass, SessionType

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

BTC = TradableInstrument(
    instrument_id="PAPER:BTCBRL",
    venue=VenueId.PAPER,
    asset_class=AssetClass.CRYPTO,
    base_asset="BTC",
    quote_asset="BRL",
    tick_size=Decimal("1"),
    lot_size=Decimal("0.00001"),
    min_quantity=Decimal("0.00001"),
    min_notional=Decimal("10"),
    session_type=SessionType.CONTINUOUS_24_7,
    timezone="UTC",
)

DEFAULT_QUOTE = Quote(bid=Decimal("349990"), ask=Decimal("350010"), observed_at=NOW)


def adapter(
    *,
    quote: Quote | None = DEFAULT_QUOTE,
    brl: str = "10000",
) -> PaperVenueAdapter:
    return PaperVenueAdapter(
        instruments=(BTC,),
        quotes=lambda _instrument_id: quote,
        balances={"BRL": Decimal(brl)},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        clock=lambda: NOW,
    )


def order(**overrides: object) -> VenueOrder:
    values: dict[str, object] = {
        "client_order_id": "PXabc",
        "instrument_id": "PAPER:BTCBRL",
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "order_type": OrderType.MARKET,
    }
    values.update(overrides)
    return VenueOrder.model_validate(values)


async def test_paper_adapter_satisfies_the_venue_protocol_and_is_never_real_money() -> None:
    paper = adapter()
    assert isinstance(paper, VenueAdapter)
    assert paper.venue is VenueId.PAPER
    assert paper.real_money_execution is False


async def test_market_buy_fills_at_ask_plus_slippage_and_debits_fee() -> None:
    paper = adapter()
    report = await paper.submit(order())
    assert report.status is OrderStatus.FILLED
    # 350010 * (1 + 5bps) = 350185.005 -> rounded up to the 1 BRL tick for a buy
    assert report.average_price == Decimal("350186")
    assert report.fee == Decimal("3.50186")
    assert report.fee_asset == "BRL"
    balances = await paper.balances()
    assert balances["BTC"] == Decimal("0.01")
    assert balances["BRL"] == Decimal("10000") - Decimal("3501.86") - Decimal("3.50186")


async def test_market_sell_fills_at_bid_minus_slippage() -> None:
    paper = adapter()
    await paper.submit(order())
    report = await paper.submit(order(client_order_id="PXsell", side="SELL"))
    assert report.status is OrderStatus.FILLED
    # 349990 * (1 - 5bps) = 349815.005 -> rounded down for a sell
    assert report.average_price == Decimal("349815")
    assert (await paper.balances())["BTC"] == Decimal("0")


async def test_resubmitting_the_same_client_order_id_never_fills_twice() -> None:
    paper = adapter()
    first = await paper.submit(order())
    second = await paper.submit(order())
    assert second == first
    assert (await paper.balances())["BTC"] == Decimal("0.01")


async def test_reusing_a_client_order_id_for_a_different_order_is_rejected() -> None:
    paper = adapter()
    await paper.submit(order())
    with pytest.raises(ValueError, match="client_order_id"):
        await paper.submit(order(quantity=Decimal("0.02")))


async def test_non_marketable_limit_rests_and_can_be_canceled() -> None:
    paper = adapter()
    resting = await paper.submit(order(order_type=OrderType.LIMIT, limit_price=Decimal("340000")))
    assert resting.status is OrderStatus.ACKNOWLEDGED
    assert resting.filled_quantity == 0
    assert await paper.query("PXabc") == resting
    canceled = await paper.cancel("PXabc")
    assert canceled.status is OrderStatus.CANCELED
    assert (await paper.balances())["BRL"] == Decimal("10000")


async def test_marketable_limit_fills_no_worse_than_its_limit() -> None:
    paper = adapter()
    report = await paper.submit(order(order_type=OrderType.LIMIT, limit_price=Decimal("350100")))
    assert report.status is OrderStatus.FILLED
    assert report.average_price == Decimal("350100")


async def test_orders_off_the_venue_grid_are_rejected() -> None:
    paper = adapter()
    report = await paper.submit(order(quantity=Decimal("0.000015")))
    assert report.status is OrderStatus.REJECTED
    assert report.reason == "quantity not on lot grid"


async def test_insufficient_balance_is_rejected_without_side_effects() -> None:
    paper = adapter(brl="100")
    report = await paper.submit(order())
    assert report.status is OrderStatus.REJECTED
    assert report.reason == "insufficient BRL balance"
    sell = await paper.submit(order(client_order_id="PXsell", side="SELL"))
    assert sell.reason == "insufficient BTC balance"
    assert await paper.balances() == {"BRL": Decimal("100")}


async def test_missing_quote_and_unknown_instrument_are_rejected() -> None:
    no_quote = await adapter(quote=None).submit(order())
    assert no_quote.status is OrderStatus.REJECTED
    assert no_quote.reason == "no quote available"
    unknown = await adapter().submit(order(instrument_id="PAPER:ETHBRL"))
    assert unknown.reason == "unknown instrument"


async def test_crossed_quotes_are_rejected() -> None:
    crossed = Quote(bid=Decimal("350010"), ask=Decimal("349990"), observed_at=NOW)
    report = await adapter(quote=crossed).submit(order())
    assert report.reason == "crossed or invalid quote"


async def test_cancel_and_query_unknown_orders() -> None:
    paper = adapter()
    assert await paper.query("PXnope") is None
    with pytest.raises(KeyError):
        await paper.cancel("PXnope")
    filled = await paper.submit(order())
    assert await paper.cancel("PXabc") == filled


def test_paper_adapter_only_trades_paper_instruments() -> None:
    real = BTC.model_copy(
        update={"venue": VenueId.BINANCE_SPOT, "instrument_id": "BINANCE_SPOT:BTCBRL"}
    )
    with pytest.raises(ValueError, match="PAPER"):
        PaperVenueAdapter(
            instruments=(real,),
            quotes=lambda _instrument_id: None,
            balances={},
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
        )
