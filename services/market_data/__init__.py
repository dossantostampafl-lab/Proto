"""Normalized market-data contracts and adapters for research and monitoring."""

from .adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    MarketDataAdapter,
    MockPredictionMarketAdapter,
    SyntheticAdapter,
)
from .contracts import (
    BinaryContractSnapshot,
    BookLevel,
    Candle,
    DataSource,
    OrderBookSnapshot,
    ResearchAsset,
)
from .core import (
    DataQualityIssue,
    DataQualityMonitor,
    DataQualityReport,
    MarketTick,
    OrderBookMetrics,
    compute_orderbook_metrics,
)
from .l2_corpus_storage import (
    PublicL2CorpusError,
    PublicL2CorpusManifest,
    PublicL2CorpusSink,
    PublicL2CorpusWriter,
    PublicL2DatasetProvenance,
    verify_public_l2_corpus,
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
from .pipeline import (
    MarketDataPipeline,
    MarketDataPipelineResult,
    MarketDataPipelineSnapshot,
    NormalizedMarketEvent,
)
from .public_feed_parser import PublicCryptoFeedError, parse_public_ticker_message
from .public_l2 import (
    PublicL2Book,
    PublicL2Event,
    PublicL2Frame,
    PublicL2IntegrityError,
    PublicL2Update,
    parse_public_l2_message,
)
from .public_l2_live import CoinbasePublicL2StreamAdapter, PublicL2StreamHealth

__all__ = [
    "BinaryContractSnapshot",
    "BookLevel",
    "CSVReplayAdapter",
    "Candle",
    "CoinbasePublicL2StreamAdapter",
    "CoinbasePublicMarketDataAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "DataSource",
    "HistoricalReplayAdapter",
    "LiveHistoryCursorError",
    "LiveTickJournal",
    "LiveTickJournalError",
    "MarketDataAdapter",
    "MarketDataPipeline",
    "MarketDataPipelineResult",
    "MarketDataPipelineSnapshot",
    "MarketTick",
    "MockPredictionMarketAdapter",
    "NormalizedMarketEvent",
    "OrderBookMetrics",
    "OrderBookSnapshot",
    "PersistedLiveTick",
    "PersistedLiveTickPage",
    "PublicCryptoFeedError",
    "PublicFeedHealth",
    "PublicFeedTimeoutError",
    "PublicL2Book",
    "PublicL2CorpusError",
    "PublicL2CorpusManifest",
    "PublicL2CorpusSink",
    "PublicL2CorpusWriter",
    "PublicL2DatasetProvenance",
    "PublicL2Event",
    "PublicL2Frame",
    "PublicL2IntegrityError",
    "PublicL2StreamHealth",
    "PublicL2Update",
    "PublicMarketDataAdapter",
    "ResearchAsset",
    "SyntheticAdapter",
    "compute_orderbook_metrics",
    "evaluate_live_coverage",
    "live_readiness_failures",
    "parse_public_l2_message",
    "parse_public_ticker_message",
    "verify_public_l2_corpus",
]
