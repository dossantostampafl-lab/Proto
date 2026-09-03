from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from services.market_data.equity_readonly import (
    AlpacaEquityReadOnlyProvider,
    AlpacaReadOnlyConfig,
    BrapiEquityReadOnlyProvider,
    BrapiReadOnlyConfig,
    ReadOnlyProviderError,
)
from services.market_data.instruments import AssetClass

from .settings import settings
from .universe_surface import build_instrument_registry

router = APIRouter(prefix="/equity-market", tags=["equity-market"])


@router.get("/{instrument_id:path}")
async def equity_market_observation(instrument_id: str) -> dict[str, object]:
    registry = build_instrument_registry()
    instrument = registry.get(instrument_id.strip().upper())
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not registered")
    if instrument.asset_class is not AssetClass.EQUITY:
        raise HTTPException(status_code=400, detail="instrument is not an equity")

    try:
        if instrument.venue == "US":
            if not settings.alpaca_market_data_configured:
                raise HTTPException(
                    status_code=503,
                    detail="US equity market-data provider is not configured",
                )
            provider = AlpacaEquityReadOnlyProvider(
                AlpacaReadOnlyConfig(
                    api_key_id=settings.alpaca_market_data_key_id or "",
                    api_secret_key=settings.alpaca_market_data_secret_key or "",
                    feed=settings.alpaca_equity_feed,
                    allowed_symbols=frozenset(settings.alpaca_equity_allowlist),
                )
            )
            event = await provider.latest_quote(instrument.symbol)
        elif instrument.venue == "B3":
            provider = BrapiEquityReadOnlyProvider(
                BrapiReadOnlyConfig(
                    token=settings.brapi_market_data_token,
                    allowed_symbols=frozenset(settings.brapi_equity_allowlist),
                )
            )
            event = await provider.latest_price(instrument.symbol)
        else:
            raise HTTPException(status_code=400, detail="equity venue is unsupported")
    except HTTPException:
        raise
    except (ReadOnlyProviderError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    threshold = settings.equity_market_data_max_age_seconds
    age_seconds = max(0.0, (datetime.now(UTC) - event.observed_at).total_seconds())
    currently_fresh = age_seconds <= threshold if threshold is not None else None

    return {
        "instrument": instrument.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "read_only_market_data": True,
        "observation_age_seconds": age_seconds,
        "freshness_threshold_seconds": threshold,
        "currently_fresh": currently_fresh,
        "execution_connected": False,
        "financial_connectivity": False,
        "real_money_execution": False,
    }
