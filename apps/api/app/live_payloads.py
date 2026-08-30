from __future__ import annotations

from datetime import datetime

from services.market_data import MarketTick, OrderBookMetrics

PUBLIC_READ_ONLY_SOURCE = "PUBLIC_READ_ONLY"
FINANCIAL_CONNECTIVITY = False
REAL_MONEY_EXECUTION = False


def age_seconds(value: datetime | None, *, now: datetime) -> float | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return max((now - value).total_seconds(), 0.0)


def source_to_server_delta_ms(*, source_at: datetime, received_at: datetime) -> float:
    return round((received_at - source_at).total_seconds() * 1_000.0, 3)


def market_payload(
    tick: MarketTick,
    *,
    received_at: datetime | None,
    connection_generation: int,
) -> dict[str, object]:
    return {
        "timestamp": tick.timestamp.isoformat(),
        "received_at": received_at.isoformat() if received_at is not None else None,
        "source_to_server_delta_ms": (
            source_to_server_delta_ms(source_at=tick.timestamp, received_at=received_at)
            if received_at is not None
            else None
        ),
        "source": PUBLIC_READ_ONLY_SOURCE,
        "venue": tick.venue,
        "symbol": tick.symbol,
        "connection_generation": connection_generation,
        "bid": tick.bid,
        "ask": tick.ask,
        "mid": tick.mid,
        "last": tick.last,
        "spread": tick.spread,
        "volume_24h": tick.volume,
        "bid_size": tick.bid_size,
        "ask_size": tick.ask_size,
        "sequence": tick.sequence,
        "financial_connectivity": FINANCIAL_CONNECTIVITY,
        "real_money_execution": REAL_MONEY_EXECUTION,
    }


def orderbook_payload(
    tick: MarketTick,
    book: OrderBookMetrics,
    *,
    received_at: datetime,
    connection_generation: int,
) -> dict[str, object]:
    return {
        "timestamp": tick.timestamp.isoformat(),
        "received_at": received_at.isoformat(),
        "source_to_server_delta_ms": source_to_server_delta_ms(
            source_at=tick.timestamp,
            received_at=received_at,
        ),
        "source": PUBLIC_READ_ONLY_SOURCE,
        "symbol": tick.symbol,
        "connection_generation": connection_generation,
        "sequence": tick.sequence,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "bid_size": tick.bid_size,
        "ask_size": tick.ask_size,
        "mid_price": book.mid_price,
        "spread": book.spread,
        "microprice": book.microprice,
        "imbalance": book.imbalance,
        "depth": book.depth,
        "financial_connectivity": FINANCIAL_CONNECTIVITY,
        "real_money_execution": REAL_MONEY_EXECUTION,
    }
