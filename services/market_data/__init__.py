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
    PublicFeedHealth,
    PublicFeedTimeoutError,
    PublicMarketDataAdapter,
)
from .live_status import evaluate_live_coverage, live_readiness_failures
from .live_storage import LiveTickJournal, LiveTickJournalError, PersistedLiveTick
from .public_feed_parser import PublicCryptoFeedError, parse_public_ticker_message

__all__ = [
    "CSVReplayAdapter",
    "CoinbasePublicMarketDataAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "HistoricalReplayAdapter",
    "LiveTickJournal",
    "LiveTickJournalError",
    "MarketDataAdapter",
    "MarketTick",
    "MockPredictionMarketAdapter",
    "OrderBookMetrics",
    "PersistedLiveTick",
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
