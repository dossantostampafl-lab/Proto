from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .core import MarketTick


def evaluate_live_coverage(
    *,
    expected_symbols: Sequence[str],
    latest: Mapping[str, MarketTick],
    symbol_connection_generation: Mapping[str, int],
    current_generation: int,
    connected: bool,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> dict[str, object]:
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    symbols = tuple(dict.fromkeys(expected_symbols))
    if not symbols:
        raise ValueError("expected_symbols must not be empty")

    symbol_health: dict[str, dict[str, object]] = {}
    missing_symbols: list[str] = []
    stale_symbols: list[str] = []
    fresh_symbols: list[str] = []
    current_connection_symbols: list[str] = []
    observed_ticks: list[MarketTick] = []

    for symbol in symbols:
        tick = latest.get(symbol)
        if tick is None:
            missing_symbols.append(symbol)
            symbol_health[symbol] = {
                "observed": False,
                "fresh": False,
                "current_connection": False,
                "connection_generation": None,
                "latest_observed_at": None,
                "age_seconds": None,
            }
            continue

        observed_ticks.append(tick)
        age_seconds = max((current_time - tick.timestamp).total_seconds(), 0.0)
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
            "current_connection": current_connection,
            "connection_generation": observed_generation,
            "latest_observed_at": tick.timestamp.isoformat(),
            "age_seconds": round(age_seconds, 6),
        }

    latest_tick = max(observed_ticks, key=lambda item: item.timestamp, default=None)
    latest_age_seconds: float | None = None
    if latest_tick is not None:
        latest_age_seconds = max(
            (current_time - latest_tick.timestamp).total_seconds(),
            0.0,
        )

    all_symbols_fresh = not missing_symbols and not stale_symbols
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
        "all_symbols_current_connection": all_symbols_current_connection,
        "stale": bool(stale_symbols) or not observed_ticks,
        "latest_observed_at": (
            latest_tick.timestamp.isoformat() if latest_tick is not None else None
        ),
        "last_frame_age_seconds": (
            round(latest_age_seconds, 6) if latest_age_seconds is not None else None
        ),
        "fresh_symbols": fresh_symbols,
        "missing_symbols": missing_symbols,
        "stale_symbols": stale_symbols,
        "current_connection_symbols": current_connection_symbols,
        "symbol_health": symbol_health,
    }
