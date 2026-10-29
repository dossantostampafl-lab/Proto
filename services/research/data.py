"""Historical klines from Binance public market data, cached locally (research only).

Reads the public, unauthenticated ``/api/v3/klines`` endpoint of ``data-api.binance.vision``
(no credentials, no order authority). Bars are cached as gzipped CSV under a gitignored data
directory (``data/research/klines`` by default, or ``$PROTO_RESEARCH_DATA``); the cache is never
committed. Tests never touch the network: they build bars in memory.
"""

from __future__ import annotations

import csv
import gzip
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_PAGE = 1000


@dataclass(frozen=True, slots=True)
class Bar:
    """One closed kline. ``open_time`` is epoch milliseconds (UTC)."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def at(self) -> datetime:
        return datetime.fromtimestamp(self.open_time / 1000, tz=UTC)


def data_dir() -> Path:
    return Path(os.environ.get("PROTO_RESEARCH_DATA", "data/research")) / "klines"


def cache_path(symbol: str, interval: str, root: Path | None = None) -> Path:
    return (root or data_dir()) / f"{symbol}-{interval}.csv.gz"


# Sub-minute klines (live EMA warm-up of second bars only; the research cache stays minutes).
SECOND_INTERVALS: dict[str, int] = {"1s": 1}


def interval_ms(interval: str) -> int:
    if interval in SECOND_INTERVALS:
        return SECOND_INTERVALS[interval] * 1000
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval {interval!r}")
    return INTERVALS[interval] * 60_000


def parse_klines(rows: Iterable[Sequence[object]]) -> list[Bar]:
    return [
        Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))  # type: ignore[arg-type]
        for r in rows
    ]


Fetch = Callable[[dict[str, object]], list[list[object]]]


def _http_fetch(params: dict[str, object]) -> list[list[object]]:
    import httpx

    for attempt in range(5):
        try:
            response = httpx.get(BASE_URL, params=params, timeout=30.0)
            if response.status_code in (418, 429):
                time.sleep(int(response.headers.get("Retry-After", "5")))
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TransportError:
            time.sleep(1 + attempt)
    raise RuntimeError(f"klines download failed for {params}")


def download(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    *,
    fetch: Fetch = _http_fetch,
) -> list[Bar]:
    """Closed bars with ``start <= open_time < end``, paging forward 1000 at a time."""

    step = interval_ms(interval)
    cursor = int(start.timestamp() * 1000)
    stop = int(end.timestamp() * 1000)
    bars: dict[int, Bar] = {}
    while cursor < stop:
        rows = fetch(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": stop - 1,
                "limit": _PAGE,
            }
        )
        page = parse_klines(rows)
        if not page:
            break
        for bar in page:
            if bar.open_time < stop:
                bars[bar.open_time] = bar
        cursor = page[-1].open_time + step
    # Drop a bar that has not closed yet.
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return [b for _, b in sorted(bars.items()) if b.open_time + step <= now_ms]


def save(bars: Sequence[Bar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow(
                [
                    b.open_time,
                    repr(b.open),
                    repr(b.high),
                    repr(b.low),
                    repr(b.close),
                    repr(b.volume),
                ]
            )


def load(path: Path) -> list[Bar]:
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        return [
            Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in reader
        ]


def load_cached(symbol: str, interval: str, root: Path | None = None) -> list[Bar]:
    path = cache_path(symbol, interval, root)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing: run `python -m services.research.lab download` first"
        )
    return load(path)


def download_all(
    symbols: Sequence[str] = SYMBOLS,
    intervals: Sequence[str] = ("1m", "5m", "15m", "1h"),
    *,
    days: int = 365,
    end: datetime | None = None,
    root: Path | None = None,
    log: Callable[[str], None] = print,
) -> None:
    end = end or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    for symbol in symbols:
        for interval in intervals:
            bars = download(symbol, interval, start, end)
            path = cache_path(symbol, interval, root)
            save(bars, path)
            log(f"{symbol} {interval}: {len(bars)} bars -> {path}")


def check_gaps(bars: Sequence[Bar], interval: str) -> int:
    """Number of missing bars between consecutive cached bars."""

    step = interval_ms(interval)
    return sum(
        (b.open_time - a.open_time) // step - 1
        for a, b in zip(bars, bars[1:], strict=False)
        if b.open_time - a.open_time > step
    )


def resample(bars: Sequence[Bar], minutes: int) -> list[Bar]:
    """Aggregate bars into ``minutes`` buckets (UTC-aligned); a partial last bucket is dropped."""

    return resample_ms(bars, minutes * 60_000)


def resample_ms(bars: Sequence[Bar], step: int) -> list[Bar]:
    """Aggregate bars into ``step``-millisecond buckets (UTC-aligned); a partial last bucket is
    dropped."""

    out: list[Bar] = []
    bucket: list[Bar] = []
    for b in bars:
        if bucket and b.open_time // step != bucket[0].open_time // step:
            out.append(_merge(bucket, step))
            bucket = []
        bucket.append(b)
    if bucket and bucket[-1].open_time + _spacing(bars) >= (bucket[0].open_time // step + 1) * step:
        out.append(_merge(bucket, step))
    return out


def _spacing(bars: Sequence[Bar]) -> int:
    return bars[1].open_time - bars[0].open_time if len(bars) > 1 else 60_000


def _merge(bucket: Sequence[Bar], step: int) -> Bar:
    return Bar(
        bucket[0].open_time // step * step,
        bucket[0].open,
        max(b.high for b in bucket),
        min(b.low for b in bucket),
        bucket[-1].close,
        sum(b.volume for b in bucket),
    )
