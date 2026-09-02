from __future__ import annotations

import json
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from .core import MarketTick
from .public_feed_parser import PublicCryptoFeedError

SUPPORTED_BINANCE_SYMBOLS: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}


def _as_object(payload: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise PublicCryptoFeedError("invalid Binance public feed payload") from error
    if not isinstance(decoded, dict):
        raise PublicCryptoFeedError("Binance public feed payload must be an object")
    return decoded


def _finite_float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise PublicCryptoFeedError(f"Binance ticker {field} is invalid") from error
    if not isfinite(result):
        raise PublicCryptoFeedError(f"Binance ticker {field} must be finite")
    return result


def _event_timestamp_ms(value: Any) -> datetime:
    if isinstance(value, bool):
        raise PublicCryptoFeedError("Binance ticker event time is invalid")
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as error:
        raise PublicCryptoFeedError("Binance ticker event time is invalid") from error
    if milliseconds <= 0:
        raise PublicCryptoFeedError("Binance ticker event time is invalid")
    return datetime.fromtimestamp(milliseconds / 1_000.0, tz=UTC)


def parse_binance_public_ticker_message(
    payload: str | bytes | dict[str, Any],
) -> list[MarketTick]:
    """Normalize Binance public 24hr ticker frames into the canonical tick contract.

    The adapter uses only anonymous public WebSocket data. Combined-stream envelopes are
    accepted, and unsupported symbols are ignored rather than widening the runtime allowlist.
    """

    message = _as_object(payload)
    data = message.get("data", message)
    if not isinstance(data, dict):
        raise PublicCryptoFeedError("Binance public stream data must be an object")

    event_type = data.get("e")
    if event_type not in (None, "24hrTicker"):
        return []

    raw_symbol = data.get("s")
    if not isinstance(raw_symbol, str):
        if event_type is None:
            return []
        raise PublicCryptoFeedError("Binance ticker symbol is invalid")
    symbol = SUPPORTED_BINANCE_SYMBOLS.get(raw_symbol.upper())
    if symbol is None:
        return []

    bid = _finite_float(data.get("b"), field="bid")
    ask = _finite_float(data.get("a"), field="ask")
    last = _finite_float(data.get("c"), field="last")
    volume = _finite_float(data.get("v"), field="volume")
    bid_size = _finite_float(data.get("B"), field="bid size")
    ask_size = _finite_float(data.get("A"), field="ask size")
    timestamp = _event_timestamp_ms(data.get("E"))

    # 24hr ticker frames do not expose a dedicated monotonic update id. Event time is
    # millisecond-resolution and the stream updates at most once per second per symbol,
    # making it a stable per-symbol sequence for the canonical live integrity checks.
    sequence = int(timestamp.timestamp() * 1_000)

    return [
        MarketTick(
            timestamp=timestamp,
            venue="binance-public-spot",
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            bid_size=bid_size,
            ask_size=ask_size,
            sequence=sequence,
        )
    ]
