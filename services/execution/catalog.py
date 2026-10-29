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


# More liquid Binance USDT pairs for the EMA agents, added only with settings.agent_more_pairs.
# Not part of PAPER_INSTRUMENTS (the drills and the paper venue keep their three pairs). Grids
# from Binance's public exchangeInfo (2026-10-29); the executor loads the testnet's own grids and
# a mismatch rejects the order, so these only size proposals.
BINANCE_MORE_INSTRUMENTS: dict[str, TradableInstrument] = {
    item.instrument_id: item
    for item in (
        _binance_spot("BNB", "USDT", tick="0.01", lot="0.001", min_notional="5"),
        _binance_spot("XRP", "USDT", tick="0.0001", lot="0.1", min_notional="5"),
        _binance_spot("DOGE", "USDT", tick="0.00001", lot="1", min_notional="1"),
        _binance_spot("ADA", "USDT", tick="0.0001", lot="0.1", min_notional="5"),
        _binance_spot("AVAX", "USDT", tick="0.001", lot="0.01", min_notional="5"),
        _binance_spot("LINK", "USDT", tick="0.001", lot="0.01", min_notional="5"),
        _binance_spot("LTC", "USDT", tick="0.01", lot="0.001", min_notional="5"),
        _binance_spot("DOT", "USDT", tick="0.001", lot="0.01", min_notional="5"),
        _binance_spot("TRX", "USDT", tick="0.0001", lot="0.1", min_notional="5"),
        _binance_spot("BCH", "USDT", tick="0.1", lot="0.001", min_notional="5"),
    )
}


def _b3_fractional(symbol: str, *, asset_class: AssetClass) -> TradableInstrument:
    """B3 fractional-market symbol: a lot of one share, tick R$0.01.

    The executor loads the real grids from the terminal and a mismatch rejects the order; these
    values only size proposals. The venue is XP through MetaTrader 5.
    """

    return TradableInstrument(
        instrument_id=f"XP_B3:{symbol}",
        venue=VenueId.XP_B3,
        asset_class=asset_class,
        base_asset=symbol,
        quote_asset="BRL",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
        min_quantity=Decimal("1"),
        min_notional=Decimal("0.01"),
        session_type=SessionType.EXCHANGE_SESSION,
        timezone="America/Sao_Paulo",
    )


# Not part of PAPER_INSTRUMENTS: the drills, the strategy and the paper venue iterate that
# catalog and must keep seeing only Binance. The API adds these only when XP routing is on.
B3_INSTRUMENTS: dict[str, TradableInstrument] = {
    item.instrument_id: item
    for item in (
        _b3_fractional("PETR4F", asset_class=AssetClass.EQUITY),
        _b3_fractional("VALE3F", asset_class=AssetClass.EQUITY),
        _b3_fractional("BOVA11F", asset_class=AssetClass.ETF),
    )
}


# US ETFs Alpaca lists as plain us_equity; used only to label the asset class truthfully.
US_ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO"})


def _alpaca_us(symbol: str) -> TradableInstrument:
    """US equity or ETF on Alpaca's PAPER account: whole shares, tick US$0.01, quoted in USD.

    The executor loads tradability from Alpaca's asset list and a mismatch rejects the order; these
    values only size proposals. Regular session 09:30-16:00 New York (the Risk Engine's hours).
    """

    return TradableInstrument(
        instrument_id=f"ALPACA_US:{symbol}",
        venue=VenueId.ALPACA_US,
        asset_class=AssetClass.ETF if symbol in US_ETFS else AssetClass.EQUITY,
        base_asset=symbol,
        quote_asset="USD",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
        min_quantity=Decimal("1"),
        min_notional=Decimal("1"),
        session_type=SessionType.EXCHANGE_SESSION,
        timezone="America/New_York",
    )


# Not part of PAPER_INSTRUMENTS either: the API adds these only when Alpaca routing is on.
US_INSTRUMENTS: dict[str, TradableInstrument] = {
    item.instrument_id: item for item in (_alpaca_us("SPY"), _alpaca_us("QQQ"), _alpaca_us("AAPL"))
}
