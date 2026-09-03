from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services.market_data.instruments import (
    AssetClass,
    Instrument,
    InstrumentRegistry,
    SessionType,
)

from .live_monitor import live_monitor
from .settings import settings

router = APIRouter(prefix="/universe", tags=["universe"])


def _core_crypto() -> tuple[Instrument, ...]:
    return (
        Instrument(
            instrument_id="CRYPTO:BTC",
            symbol="BTC",
            name="Bitcoin",
            asset_class=AssetClass.CRYPTO,
            venue="CRYPTO",
            currency="USD",
            country=None,
            timezone="UTC",
            market_calendar="CONTINUOUS",
            session_type=SessionType.CONTINUOUS_24_7,
        ),
        Instrument(
            instrument_id="CRYPTO:ETH",
            symbol="ETH",
            name="Ethereum",
            asset_class=AssetClass.CRYPTO,
            venue="CRYPTO",
            currency="USD",
            country=None,
            timezone="UTC",
            market_calendar="CONTINUOUS",
            session_type=SessionType.CONTINUOUS_24_7,
        ),
        Instrument(
            instrument_id="CRYPTO:SOL",
            symbol="SOL",
            name="Solana",
            asset_class=AssetClass.CRYPTO,
            venue="CRYPTO",
            currency="USD",
            country=None,
            timezone="UTC",
            market_calendar="CONTINUOUS",
            session_type=SessionType.CONTINUOUS_24_7,
        ),
    )


def _configured_equities() -> tuple[Instrument, ...]:
    us = tuple(
        Instrument(
            instrument_id=f"US:{symbol}",
            symbol=symbol,
            name=symbol,
            asset_class=AssetClass.EQUITY,
            venue="US",
            currency="USD",
            country="US",
            timezone="America/New_York",
            market_calendar="US_EQUITIES",
            session_type=SessionType.EXCHANGE_SESSION,
        )
        for symbol in settings.alpaca_equity_allowlist
    )
    b3 = tuple(
        Instrument(
            instrument_id=f"B3:{symbol}",
            symbol=symbol,
            name=symbol,
            asset_class=AssetClass.EQUITY,
            venue="B3",
            currency="BRL",
            country="BR",
            timezone="America/Sao_Paulo",
            market_calendar="B3",
            session_type=SessionType.EXCHANGE_SESSION,
        )
        for symbol in settings.brapi_equity_allowlist
    )
    return us + b3


def build_instrument_registry() -> InstrumentRegistry:
    return InstrumentRegistry(_core_crypto() + _configured_equities())


def _coverage(instrument: Instrument) -> dict[str, object]:
    if instrument.asset_class is AssetClass.CRYPTO:
        live = live_monitor.status()
        symbols = {str(symbol).upper() for symbol in live.get("symbols", [])}
        fresh = {str(symbol).upper() for symbol in live.get("fresh_symbols", [])}
        return {
            "catalog_source": "PROTO_CORE",
            "market_data_provider": live.get("provider"),
            "market_data_source": live.get("source"),
            "read_only_market_data": instrument.symbol in symbols,
            "currently_fresh": instrument.symbol in fresh,
            "execution_connected": False,
        }
    if instrument.venue == "US":
        return {
            "catalog_source": "ALPACA_READ_ONLY_ALLOWLIST",
            "market_data_provider": "ALPACA",
            "market_data_source": "LICENSED_READ_ONLY",
            "read_only_market_data": False,
            "currently_fresh": False,
            "execution_connected": False,
        }
    return {
        "catalog_source": "BRAPI_READ_ONLY_ALLOWLIST",
        "market_data_provider": "BRAPI",
        "market_data_source": "PUBLIC_READ_ONLY",
        "read_only_market_data": False,
        "currently_fresh": False,
        "execution_connected": False,
    }


@router.get("")
def universe(
    asset_class: AssetClass | None = Query(default=None),
    venue: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, object]:
    registry = build_instrument_registry()
    instruments = registry.list(asset_class=asset_class, venue=venue)
    return {
        "count": len(instruments),
        "instruments": [
            {
                **instrument.model_dump(mode="json"),
                "coverage": _coverage(instrument),
            }
            for instrument in instruments
        ],
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/{instrument_id:path}")
def instrument_detail(instrument_id: str) -> dict[str, object]:
    registry = build_instrument_registry()
    instrument = registry.get(instrument_id.strip().upper())
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not registered")
    return {
        **instrument.model_dump(mode="json"),
        "coverage": _coverage(instrument),
        "financial_connectivity": False,
        "real_money_execution": False,
    }
