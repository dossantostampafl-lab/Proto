from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import isfinite

from .core import MarketTick


def evaluate_live_coverage(
    *,
    expected_symbols: Sequence[str],
    latest: Mapping[str, MarketTick],
    symbol_connection_generation: Mapping[str, int],
    current_generation: int,
    connected: bool,
    stale_after_seconds: float,
    received_times: Mapping[str, datetime] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    if not isfinite(stale_after_seconds) or stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be finite and positive")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    symbols = tuple(dict.fromkeys(expected_symbols))
    if not symbols:
        raise ValueError("expected_symbols must not be empty")

    receipt_tracking_enabled = received_times is not None
    receipt_map = received_times or {}
    for symbol, received_at in receipt_map.items():
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError(f"received time for {symbol} must be timezone-aware")

    symbol_health: dict[str, dict[str, object]] = {}
    missing_symbols: list[str] = []
    stale_symbols: list[str] = []
    fresh_symbols: list[str] = []
    missing_receipt_symbols: list[str] = []
    stale_receipt_symbols: list[str] = []
    fresh_receipt_symbols: list[str] = []
    current_connection_symbols: list[str] = []
    observed_ticks: list[MarketTick] = []
    observed_receipts: list[datetime] = []

    for symbol in symbols:
        tick = latest.get(symbol)
        received_at = receipt_map.get(symbol)
        if tick is None:
            missing_symbols.append(symbol)
            symbol_health[symbol] = {
                "observed": False,
                "fresh": False,
                "receipt_fresh": False if receipt_tracking_enabled else None,
                "current_connection": False,
                "connection_generation": None,
                "latest_observed_at": None,
                "age_seconds": None,
                "latest_received_at": None,
                "receipt_age_seconds": None,
            }
            continue

        observed_ticks.append(tick)
        age_seconds = max((current_time - tick.timestamp).total_seconds(), 0.0)
        receipt_age_seconds: float | None = None
        receipt_fresh: bool | None = None
        if receipt_tracking_enabled:
            if received_at is None:
                missing_receipt_symbols.append(symbol)
                receipt_fresh = False
            else:
                observed_receipts.append(received_at)
                receipt_age_seconds = max(
                    (current_time - received_at).total_seconds(),
                    0.0,
                )
                receipt_fresh = receipt_age_seconds <= stale_after_seconds
                if receipt_fresh:
                    fresh_receipt_symbols.append(symbol)
                else:
                    stale_receipt_symbols.append(symbol)
        elif received_at is not None:
            observed_receipts.append(received_at)
            receipt_age_seconds = max(
                (current_time - received_at).total_seconds(),
                0.0,
            )

        fresh = age_seconds <= stale_after_seconds
        observed_generation = symbol_connection_generation.get(symbol)
        current_connection = bool(
            connected
            and current_generation > 0
            and observed_generation == current_generation
        )
        if fresh:
            fresh_symbols.append(symbol)
        else:
            stale_symbols.append(symbol)
        if current_connection:
            current_connection_symbols.append(symbol)
        symbol_health[symbol] = {
            "observed": True,
            "fresh": fresh,
            "receipt_fresh": receipt_fresh,
            "current_connection": current_connection,
            "connection_generation": observed_generation,
            "latest_observed_at": tick.timestamp.isoformat(),
            "age_seconds": round(age_seconds, 6),
            "latest_received_at": received_at.isoformat() if received_at is not None else None,
            "receipt_age_seconds": (
                round(receipt_age_seconds, 6) if receipt_age_seconds is not None else None
            ),
        }

    latest_tick = max(observed_ticks, key=lambda item: item.timestamp, default=None)
    latest_received_at = max(observed_receipts, default=None)
    latest_age_seconds: float | None = None
    latest_receipt_age_seconds: float | None = None
    if latest_tick is not None:
        latest_age_seconds = max(
            (current_time - latest_tick.timestamp).total_seconds(),
            0.0,
        )
    if latest_received_at is not None:
        latest_receipt_age_seconds = max(
            (current_time - latest_received_at).total_seconds(),
            0.0,
        )

    all_symbols_fresh = not missing_symbols and not stale_symbols
    all_symbols_receipts_fresh: bool | None = None
    if receipt_tracking_enabled:
        all_symbols_receipts_fresh = bool(
            not missing_symbols
            and not missing_receipt_symbols
            and not stale_receipt_symbols
        )
    all_symbols_current_connection = bool(
        connected
        and current_generation > 0
        and not missing_symbols
        and len(current_connection_symbols) == len(symbols)
    )
    return {
        "receiving_data": bool(fresh_symbols),
        "complete": not missing_symbols,
        "all_symbols_fresh": all_symbols_fresh,
        "all_symbols_receipts_fresh": all_symbols_receipts_fresh,
        "all_symbols_current_connection": all_symbols_current_connection,
        "stale": bool(stale_symbols) or not observed_ticks,
        "latest_observed_at": (
            latest_tick.timestamp.isoformat() if latest_tick is not None else None
        ),
        "last_frame_age_seconds": (
            round(latest_age_seconds, 6) if latest_age_seconds is not None else None
        ),
        "latest_received_at": (
            latest_received_at.isoformat() if latest_received_at is not None else None
        ),
        "last_receipt_age_seconds": (
            round(latest_receipt_age_seconds, 6)
            if latest_receipt_age_seconds is not None
            else None
        ),
        "fresh_symbols": fresh_symbols,
        "missing_symbols": missing_symbols,
        "stale_symbols": stale_symbols,
        "fresh_receipt_symbols": fresh_receipt_symbols,
        "missing_receipt_symbols": missing_receipt_symbols,
        "stale_receipt_symbols": stale_receipt_symbols,
        "current_connection_symbols": current_connection_symbols,
        "symbol_health": symbol_health,
    }


def _parse_degraded(coverage: Mapping[str, object]) -> bool:
    feed_health = coverage.get("feed_health")
    if not isinstance(feed_health, Mapping):
        return False
    value = feed_health.get("consecutive_parse_errors", 0)
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def live_readiness_failures(
    *,
    running: bool,
    connected: bool,
    message_fresh: bool,
    coverage: Mapping[str, object],
    parse_degraded: bool | None = None,
) -> list[str]:
    failures: list[str] = []
    if not running:
        failures.append("MONITOR_STOPPED")
    if not connected:
        failures.append("SOURCE_DISCONNECTED")
    elif not message_fresh:
        failures.append("SOURCE_MESSAGES_STALE")

    degraded = _parse_degraded(coverage) if parse_degraded is None else parse_degraded
    if connected and degraded:
        failures.append("SOURCE_PARSE_DEGRADED")

    if not bool(coverage.get("receiving_data")):
        failures.append("NO_FRESH_DATA")
    if not bool(coverage.get("complete")):
        failures.append("INCOMPLETE_SYMBOL_COVERAGE")
    elif not bool(coverage.get("all_symbols_fresh")):
        failures.append("STALE_SYMBOL_COVERAGE")

    if coverage.get("all_symbols_receipts_fresh") is False:
        if coverage.get("missing_receipt_symbols"):
            failures.append("MISSING_SYMBOL_RECEIPTS")
        else:
            failures.append("STALE_SYMBOL_RECEIPTS")

    if connected and not bool(coverage.get("all_symbols_current_connection")):
        failures.append("CURRENT_CONNECTION_INCOMPLETE")
    return failures
