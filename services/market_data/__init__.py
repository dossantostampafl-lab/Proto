"""Normalized market-data contracts and adapters for research and monitoring."""

from .binance_live import (
    BinancePublicMarketDataAdapter,
)
from .contracts import (
    BinaryContractSnapshot,
    BookLevel,
    Candle,
    DataSource,
    OrderBookSnapshot,
)
from .core import (
    DataQualityIssue,
    DataQualityMonitor,
    MarketTick,
    OrderBookMetrics,
    compute_orderbook_metrics,
)
from .instruments import (
    AssetClass,
    Instrument,
    InstrumentRegistry,
    SessionType,
)
from .live import (
    CoinbasePublicMarketDataAdapter,
)
from .live_contracts import (
    PublicFeedHealth,
    PublicMarketDataAdapter,
)
from .live_status import (
    evaluate_live_coverage,
    live_readiness_failures,
)
from .live_storage import (
    LiveHistoryCursorError,
    LiveTickJournal,
    LiveTickJournalError,
    PersistedLiveTick,
    PersistedLiveTickPage,
)
from .markout import (
    FillObservation,
    PublicL2ReplaySnapshot,
    compute_fill_markout,
    summarize_markouts,
)
from .pipeline import (
    MarketDataPipeline,
)
from .universal import (
    MarketEvent,
    MarketEventKind,
    MarketEventProvenance,
)

__all__ = [
    "AssetClass",
    "BinancePublicMarketDataAdapter",
    "BinaryContractSnapshot",
    "BookLevel",
    "Candle",
    "CoinbasePublicMarketDataAdapter",
    "DataQualityIssue",
    "DataQualityMonitor",
    "DataSource",
    "FillObservation",
    "Instrument",
    "InstrumentRegistry",
    "LiveHistoryCursorError",
    "LiveTickJournal",
    "LiveTickJournalError",
    "MarketDataPipeline",
    "MarketEvent",
    "MarketEventKind",
    "MarketEventProvenance",
    "MarketTick",
    "OrderBookMetrics",
    "OrderBookSnapshot",
    "PersistedLiveTick",
    "PersistedLiveTickPage",
    "PublicFeedHealth",
    "PublicL2ReplaySnapshot",
    "PublicMarketDataAdapter",
    "SessionType",
    "compute_fill_markout",
    "compute_orderbook_metrics",
    "evaluate_live_coverage",
    "live_readiness_failures",
    "summarize_markouts",
]
