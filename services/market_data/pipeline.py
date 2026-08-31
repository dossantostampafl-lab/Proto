from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.events.runtime import EventRuntime
from services.features.core import FeatureFrame, FeatureWindow, build_feature_frame

from .core import DataQualityMonitor, DataQualityReport, MarketTick


class NormalizedMarketEvent(BaseModel):
    """Canonical event envelope between normalization and downstream features."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=64, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    occurred_at: datetime
    received_at: datetime
    sequence: int = Field(ge=0)
    tick: MarketTick

    @field_validator("source", "symbol")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("normalized event identifiers must not be blank")
        return normalized.upper()


@dataclass(frozen=True, slots=True)
class MarketDataPipelineResult:
    event: NormalizedMarketEvent
    quality: DataQualityReport
    feature: FeatureFrame
    published_message_id: str | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class MarketDataPipelineSnapshot:
    accepted: int
    duplicates: int
    quality_rejections: int
    publish_failures: int
    published: int
    tracked_event_ids: int
    dedupe_capacity: int
    tracked_markets: int


class MarketDataPipeline:
    """Source -> normalization -> quality -> features -> event bus pipeline.

    Invalid or duplicate input never reaches the event bus. Event identifiers are
    deterministic across replay/restart, giving consumers an idempotency key.
    """

    def __init__(
        self,
        *,
        event_runtime: EventRuntime | None = None,
        stream: str = "proto.market.normalized",
        feature_window: FeatureWindow = FeatureWindow.S15,
        history_limit: int = 4_096,
        dedupe_limit: int = 100_000,
        quality_monitor: DataQualityMonitor | None = None,
    ) -> None:
        if history_limit < 2:
            raise ValueError("history_limit must be at least 2")
        if dedupe_limit < 2:
            raise ValueError("dedupe_limit must be at least 2")
        self._event_runtime = event_runtime
        self._stream = stream
        self._feature_window = feature_window
        self._history_limit = history_limit
        self._dedupe_limit = dedupe_limit
        self._quality = quality_monitor or DataQualityMonitor()
        self._history: dict[tuple[str, str], deque[MarketTick]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._seen_event_ids: set[str] = set()
        self._event_id_order: deque[str] = deque()
        self._accepted = 0
        self._duplicates = 0
        self._quality_rejections = 0
        self._publish_failures = 0
        self._published = 0

    @staticmethod
    def event_id(tick: MarketTick) -> str:
        canonical = "|".join(
            (
                tick.venue.strip().upper(),
                tick.symbol.strip().upper(),
                tick.timestamp.isoformat(),
                str(tick.sequence),
            )
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_received_at(received_at: datetime) -> None:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")

    def _remember_event_id(self, identifier: str) -> None:
        if len(self._event_id_order) >= self._dedupe_limit:
            evicted = self._event_id_order.popleft()
            self._seen_event_ids.remove(evicted)
        self._event_id_order.append(identifier)
        self._seen_event_ids.add(identifier)

    async def ingest(
        self,
        tick: MarketTick,
        *,
        received_at: datetime | None = None,
    ) -> MarketDataPipelineResult:
        received = received_at or datetime.now(UTC)
        self._validate_received_at(received)
        identifier = self.event_id(tick)
        duplicate = identifier in self._seen_event_ids

        quality = self._quality.evaluate(tick, now=received, commit=False)
        if duplicate:
            self._duplicates += 1
            return MarketDataPipelineResult(
                event=NormalizedMarketEvent(
                    event_id=identifier,
                    source=tick.venue,
                    symbol=tick.symbol,
                    occurred_at=tick.timestamp,
                    received_at=received,
                    sequence=tick.sequence,
                    tick=tick,
                ),
                quality=quality,
                feature=build_feature_frame([tick], window=self._feature_window),
                published_message_id=None,
                duplicate=True,
            )
        if not quality.valid:
            self._quality_rejections += 1
            raise ValueError(
                "market data quality rejection: "
                + ",".join(issue.value for issue in quality.issues)
            )

        event = NormalizedMarketEvent(
            event_id=identifier,
            source=tick.venue,
            symbol=tick.symbol,
            occurred_at=tick.timestamp,
            received_at=received,
            sequence=tick.sequence,
            tick=tick,
        )
        key = (event.source, event.symbol)
        history = self._history[key]
        feature = build_feature_frame([*history, tick], window=self._feature_window)

        published_message_id: str | None = None
        if self._event_runtime is not None:
            try:
                published_message_id = await self._event_runtime.publish(
                    self._stream,
                    {
                        "event_id": event.event_id,
                        "source": event.source,
                        "symbol": event.symbol,
                        "occurred_at": event.occurred_at.isoformat(),
                        "received_at": event.received_at.isoformat(),
                        "sequence": str(event.sequence),
                        "event": event.model_dump_json(),
                        "feature": feature.model_dump_json(),
                    },
                )
            except Exception:
                self._publish_failures += 1
                raise
            self._published += 1

        committed_quality = self._quality.evaluate(tick, now=received, commit=True)
        if not committed_quality.valid:
            raise RuntimeError("data quality state changed during atomic publish")
        history.append(tick)
        self._remember_event_id(identifier)
        self._accepted += 1
        return MarketDataPipelineResult(
            event=event,
            quality=committed_quality,
            feature=feature,
            published_message_id=published_message_id,
            duplicate=False,
        )

    def snapshot(self) -> MarketDataPipelineSnapshot:
        return MarketDataPipelineSnapshot(
            accepted=self._accepted,
            duplicates=self._duplicates,
            quality_rejections=self._quality_rejections,
            publish_failures=self._publish_failures,
            published=self._published,
            tracked_event_ids=len(self._seen_event_ids),
            dedupe_capacity=self._dedupe_limit,
            tracked_markets=len(self._history),
        )

    def reset(self) -> None:
        self._quality.reset()
        self._history.clear()
        self._seen_event_ids.clear()
        self._event_id_order.clear()
        self._accepted = 0
        self._duplicates = 0
        self._quality_rejections = 0
        self._publish_failures = 0
        self._published = 0
