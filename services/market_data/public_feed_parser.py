from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .core import MarketTick

MAX_PUBLIC_FRAME_BYTES = 256 * 1024
MAX_EVENTS_PER_FRAME = 32
MAX_TICKERS_PER_EVENT = 32
SUPPORTED_PUBLIC_PRODUCTS: dict[str, str] = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
}


class PublicCryptoFeedError(RuntimeError):
    """Raised when a public crypto market-data frame cannot be normalized safely."""


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicCryptoFeedError("expected object payload")
    return value


def _decode_payload(message: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        if isinstance(message, bytes):
            if len(message) > MAX_PUBLIC_FRAME_BYTES:
                raise PublicCryptoFeedError("public feed payload exceeds size limit")
            payload = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            if len(message.encode("utf-8")) > MAX_PUBLIC_FRAME_BYTES:
                raise PublicCryptoFeedError("public feed payload exceeds size limit")
            payload = json.loads(message)
        else:
            payload = dict(message)
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise PublicCryptoFeedError("invalid public feed payload") from error
    return _as_mapping(payload)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise PublicCryptoFeedError("ticker timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicCryptoFeedError("ticker timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicCryptoFeedError("ticker timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def parse_public_ticker_message(
    message: str | bytes | Mapping[str, Any],
) -> list[MarketTick]:
    root = _decode_payload(message)
    if root.get("channel") != "ticker":
        return []

    timestamp = _parse_timestamp(root.get("timestamp"))
    try:
        sequence = int(root.get("sequence_num", 0))
    except (TypeError, ValueError) as error:
        raise PublicCryptoFeedError("ticker sequence is invalid") from error
    if sequence < 0:
        raise PublicCryptoFeedError("ticker sequence must be non-negative")

    events = root.get("events", [])
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise PublicCryptoFeedError("ticker events must be an array")
    if len(events) > MAX_EVENTS_PER_FRAME:
        raise PublicCryptoFeedError("ticker event count exceeds limit")

    ticks: list[MarketTick] = []
    for event_value in events:
        event = _as_mapping(event_value)
        ticker_values = event.get("tickers", [])
        if not isinstance(ticker_values, Sequence) or isinstance(
            ticker_values,
            (str, bytes),
        ):
            raise PublicCryptoFeedError("ticker list must be an array")
        if len(ticker_values) > MAX_TICKERS_PER_EVENT:
            raise PublicCryptoFeedError("ticker count exceeds limit")
        for ticker_value in ticker_values:
            ticker = _as_mapping(ticker_value)
            product_id = str(ticker.get("product_id", ""))
            symbol = SUPPORTED_PUBLIC_PRODUCTS.get(product_id)
            if symbol is None:
                continue
            try:
                tick = MarketTick(
                    timestamp=timestamp,
                    venue="coinbase-public",
                    symbol=symbol,
                    bid=float(ticker["best_bid"]),
                    ask=float(ticker["best_ask"]),
                    last=float(ticker["price"]),
                    volume=float(ticker.get("volume_24_h", 0.0)),
                    bid_size=float(ticker.get("best_bid_quantity", 0.0)),
                    ask_size=float(ticker.get("best_ask_quantity", 0.0)),
                    sequence=sequence,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PublicCryptoFeedError("invalid public ticker frame") from error
            ticks.append(tick)
    return ticks
