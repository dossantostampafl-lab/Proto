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
    PublicFeedHealth,
    PublicFeedTimeoutError,
    PublicMarketDataAdapter,
    parse_public_ticker_message,
)
from .live_status import evaluate_live_coverage, live_readiness_failures

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
    "PublicFeedHealth",
    "PublicFeedTimeoutError",
    "PublicMarketDataAdapter",
    "SyntheticAdapter",
    "compute_orderbook_metrics",
    "evaluate_live_coverage",
    "live_readiness_failures",
    "parse_public_ticker_message",
]
