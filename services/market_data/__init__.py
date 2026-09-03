"""Normalized market-data contracts and adapters for research and monitoring."""

from .adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    MarketDataAdapter,
    MockPredictionMarketAdapter,
    SyntheticAdapter,
)
from .binance_live import BinancePublicFeedTimeoutError, BinancePublicMarketDataAdapter
from .binance_public_feed import (
    SUPPORTED_BINANCE_SYMBOLS,
    parse_binance_public_ticker_message,
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
from .equity_readonly import (
    AlpacaEquityReadOnlyProvider,
    AlpacaReadOnlyConfig,
    BrapiEquityReadOnlyProvider,
    BrapiReadOnlyConfig,
    ReadOnlyProviderError,
)
from .instruments import AssetClass, Instrument, InstrumentRegistry, SessionType
from .l2_corpus_replay import (
    PublicL2CorpusRecord,
    PublicL2CorpusReplay,
    PublicL2ReplaySnapshot,
    load_public_l2_corpus,
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
from .markout import (
    DEFAULT_MARKOUT_HORIZONS_MS,
    FillMarkout,
    FillObservation,
    MarkoutPoint,
    MarkoutSummary,
    compute_fill_markout,
    summarize_markouts,
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
from .universal import MarketEvent, MarketEventKind, MarketEventProvenance

__all__ = [
    "AlpacaEquityReadOnlyProvider",
    "AlpacaReadOnlyConfig",
    "AssetClass",
    "BinaryContractSnapshot",
    "BinancePublicFeedTimeoutError",
    "BinancePublicMarketDataAdapter",
    "BookLevel",
    "BrapiEquityReadOnlyProvider",
    "BrapiReadOnlyConfig",
    "CSVReplayAdapter",
    "Candle",
    "CoinbasePublicL2StreamAdapter",
    "CoinbasePublicMarketDataAdapter",
    "DEFAULT_MARKOUT_HORIZONS_MS",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataQualityReport",
    "DataSource",
    "FillMarkout",
    "FillObservation",
    "HistoricalReplayAdapter",
    "Instrument",
    "InstrumentRegistry",
    "LiveHistoryCursorError",
    "LiveTickJournal",
    "LiveTickJournalError",
    "MarketDataAdapter",
    "MarketDataPipeline",
    "MarketDataPipelineResult",
    "MarketDataPipelineSnapshot",
    "MarketEvent",
    "MarketEventKind",
    "MarketEventProvenance",
    "MarketTick",
    "MarkoutPoint",
    "MarkoutSummary",
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
    "PublicL2CorpusRecord",
    "PublicL2CorpusReplay",
    "PublicL2CorpusSink",
    "PublicL2CorpusWriter",
    "PublicL2DatasetProvenance",
    "PublicL2Event",
    "PublicL2Frame",
    "PublicL2IntegrityError",
    "PublicL2ReplaySnapshot",
    "PublicL2StreamHealth",
    "PublicL2Update",
    "PublicMarketDataAdapter",
    "ReadOnlyProviderError",
    "ResearchAsset",
    "SUPPORTED_BINANCE_SYMBOLS",
    "SessionType",
    "SyntheticAdapter",
    "compute_fill_markout",
    "compute_orderbook_metrics",
    "evaluate_live_coverage",
    "live_readiness_failures",
    "load_public_l2_corpus",
    "parse_binance_public_ticker_message",
    "parse_public_l2_message",
    "parse_public_ticker_message",
    "summarize_markouts",
    "verify_public_l2_corpus",
]
