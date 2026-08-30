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
from .live import CoinbasePublicMarketDataAdapter, PublicFeedTimeoutError
from .live_contracts import PublicFeedHealth, PublicMarketDataAdapter
from .live_status import evaluate_live_coverage, live_readiness_failures
from .live_storage import (
    LiveHistoryCursorError,
    LiveTickJournal,
    LiveTickJournalError,
    PersistedLiveTick,
    PersistedLiveTickPage,
)
from .public_feed_parser import PublicCryptoFeedError, parse_public_ticker_message

__all__ = [
    "CSVReplayAdapter",
    "CoinbasePublicMarketDataAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "HistoricalReplayAdapter",
    "LiveHistoryCursorError",
    "LiveTickJournal",
    "LiveTickJournalError",
    "MarketDataAdapter",
    "MarketTick",
    "MockPredictionMarketAdapter",
    "OrderBookMetrics",
    "PersistedLiveTick",
    "PersistedLiveTickPage",
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
