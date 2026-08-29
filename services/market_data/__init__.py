"""Normalized market-data contracts and adapters for research/simulation/read-only data."""

from .adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    MarketDataAdapter,
    MockPredictionMarketAdapter,
    PublicReadOnlyHTTPAdapter,
    SyntheticAdapter,
)
from .core import (
    DataQualityIssue,
    DataQualityMonitor,
    DataQualityReport,
    MarketTick,
    OrderBookMetrics,
    compute_orderbook_metrics,
)

__all__ = [
    "CSVReplayAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "HistoricalReplayAdapter",
    "MarketDataAdapter",
    "MarketTick",
    "MockPredictionMarketAdapter",
    "OrderBookMetrics",
    "PublicReadOnlyHTTPAdapter",
    "SyntheticAdapter",
    "compute_orderbook_metrics",
]
