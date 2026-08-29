from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from io import StringIO
from random import Random
from typing import Any, Protocol
from urllib.parse import urlparse

from .core import MarketTick


class MarketDataAdapter(Protocol):
    def stream(self) -> Iterator[MarketTick]: ...


class SyntheticAdapter:
    def __init__(
        self,
        *,
        symbol: str,
        start_price: float,
        seed: int = 7,
        count: int = 1_000,
        interval_ms: int = 250,
        start_at: datetime | None = None,
    ) -> None:
        if start_price <= 0:
            raise ValueError("start_price must be positive")
        if count < 1:
            raise ValueError("count must be positive")
        if interval_ms < 1:
            raise ValueError("interval_ms must be positive")
        self.symbol = symbol
        self.start_price = start_price
        self.seed = seed
        self.count = count
        self.interval_ms = interval_ms
        self.start_at = start_at or datetime(2026, 1, 1, tzinfo=UTC)

    def stream(self) -> Iterator[MarketTick]:
        rng = Random(self.seed)
        price = self.start_price
        for sequence in range(self.count):
            shock = rng.gauss(0.0, 0.0008)
            price = max(price * (1.0 + shock), 0.01)
            spread_bps = 1.0 + abs(rng.gauss(0.0, 0.7))
            half_spread = price * spread_bps / 20_000.0
            bid = price - half_spread
            ask = price + half_spread
            bid_size = 0.5 + rng.random() * 4.0
            ask_size = 0.5 + rng.random() * 4.0
            volume = rng.random() * 10.0
            timestamp = self.start_at + timedelta(
                milliseconds=sequence * self.interval_ms
            )
            yield MarketTick(
                timestamp=timestamp,
                venue="synthetic",
                symbol=self.symbol,
                bid=bid,
                ask=ask,
                last=price,
                volume=volume,
                bid_size=bid_size,
                ask_size=ask_size,
                sequence=sequence,
            )


class CSVReplayAdapter:
    def __init__(self, csv_text: str) -> None:
        self.csv_text = csv_text

    def stream(self) -> Iterator[MarketTick]:
        reader = csv.DictReader(StringIO(self.csv_text))
        required = {
            "timestamp",
            "venue",
            "symbol",
            "bid",
            "ask",
            "last",
            "volume",
            "bid_size",
            "ask_size",
            "sequence",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("CSV is missing required market-data columns")

        for row in reader:
            yield MarketTick(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                venue=row["venue"],
                symbol=row["symbol"],
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                last=float(row["last"]),
                volume=float(row["volume"]),
                bid_size=float(row["bid_size"]),
                ask_size=float(row["ask_size"]),
                sequence=int(row["sequence"]),
            )


class HistoricalReplayAdapter:
    def __init__(self, ticks: Iterable[MarketTick]) -> None:
        self._ticks = tuple(ticks)

    def stream(self) -> Iterator[MarketTick]:
        yield from sorted(self._ticks, key=lambda tick: (tick.timestamp, tick.sequence))


class MockPredictionMarketAdapter(HistoricalReplayAdapter):
    """Explicit simulation adapter for binary-contract research fixtures."""


PublicJSONFetcher = Callable[[str], Mapping[str, Any]]
PublicTickParser = Callable[[Mapping[str, Any], int], MarketTick]


class PublicReadOnlyHTTPAdapter:
    """Public market-data adapter with a deliberately narrow read-only capability surface.

    The adapter never stores credentials, never accepts request headers, and exposes no
    mutation/order method. Network transport is injected so tests remain deterministic and
    production deployments can enforce egress policy outside domain code.
    """

    def __init__(
        self,
        *,
        url: str,
        allowed_hosts: frozenset[str],
        fetch_json: PublicJSONFetcher,
        parse_tick: PublicTickParser,
        count: int = 1,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("public market-data URL must use https")
        if parsed.hostname is None or parsed.hostname not in allowed_hosts:
            raise ValueError("public market-data host is not allowlisted")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials are forbidden in public market-data URLs")
        if count < 1 or count > 10_000:
            raise ValueError("count must be between 1 and 10000")

        self._url = url
        self._fetch_json = fetch_json
        self._parse_tick = parse_tick
        self._count = count

    @property
    def read_only(self) -> bool:
        return True

    @property
    def requires_trading_credentials(self) -> bool:
        return False

    def stream(self) -> Iterator[MarketTick]:
        for sequence in range(self._count):
            payload = self._fetch_json(self._url)
            yield self._parse_tick(payload, sequence)
