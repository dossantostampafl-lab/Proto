"""Normalized market-data contracts and adapters for research and monitoring."""

from .adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    MarketDataAdapter,
    MockPredictionMarketAdapter,
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
from .live import (
    CoinbasePublicMarketDataAdapter,
    PublicCryptoFeedError,
    parse_public_ticker_message,
)

__all__ = [
    "CSVReplayAdapter",
    "CoinbasePublicMarketDataAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "HistoricalReplayAdapter",
    "MarketDataAdapter",
    "MarketTick",
    "MockPredictionMarketAdapter",
    "OrderBookMetrics",
    "PublicCryptoFeedError",
    "SyntheticAdapter",
    "compute_orderbook_metrics",
    "parse_public_ticker_message",
]
