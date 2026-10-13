"""Order-grid specs for PAPER-stage decisions.

These are conservative approximations used to size paper proposals. Phase 5
replaces them with the venue's own exchange metadata before any order is sent;
a mismatch there rejects the order instead of trusting these values.
"""

from __future__ import annotations

from decimal import Decimal

from services.market_data.instruments import AssetClass, SessionType

from .contracts import TradableInstrument, VenueId


def _binance_spot(
    base: str, quote: str, *, tick: str, lot: str, min_notional: str
) -> TradableInstrument:
    return TradableInstrument(
        instrument_id=f"BINANCE_SPOT:{base}{quote}",
        venue=VenueId.BINANCE_SPOT,
        asset_class=AssetClass.CRYPTO,
        base_asset=base,
        quote_asset=quote,
        tick_size=Decimal(tick),
        lot_size=Decimal(lot),
        min_quantity=Decimal(lot),
        min_notional=Decimal(min_notional),
        session_type=SessionType.CONTINUOUS_24_7,
        timezone="UTC",
    )


PAPER_INSTRUMENTS: dict[str, TradableInstrument] = {
    item.instrument_id: item
    for item in (
        _binance_spot("BTC", "BRL", tick="1", lot="0.00001", min_notional="10"),
        _binance_spot("ETH", "BRL", tick="0.1", lot="0.0001", min_notional="10"),
        _binance_spot("SOL", "BRL", tick="0.01", lot="0.001", min_notional="10"),
        _binance_spot("BTC", "USDT", tick="0.01", lot="0.00001", min_notional="5"),
        _binance_spot("ETH", "USDT", tick="0.01", lot="0.0001", min_notional="5"),
        _binance_spot("SOL", "USDT", tick="0.01", lot="0.001", min_notional="5"),
    )
}
