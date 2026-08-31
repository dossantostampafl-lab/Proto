from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services.replay import ReplayEvent, ReplayPhase, ReplaySession

from .contracts import BookLevel, DataSource, OrderBookSnapshot, ResearchAsset
from .public_feed_parser import (
    MAX_EVENTS_PER_FRAME,
    MAX_PUBLIC_FRAME_BYTES,
    SUPPORTED_PUBLIC_PRODUCTS,
    PublicCryptoFeedError,
)

MAX_L2_UPDATES_PER_EVENT = 20_000
MAX_L2_BOOK_LEVELS_PER_SIDE = 10_000
MAX_L2_SNAPSHOT_LEVELS_PER_SIDE = 1_000

Level2Side = Literal["bid", "offer"]
Level2EventType = Literal["snapshot", "update"]


class PublicL2IntegrityError(PublicCryptoFeedError):
    """Raised when a public L2 stream cannot be reconstructed deterministically."""


class PublicL2Update(BaseModel):
    model_config = ConfigDict(frozen=True)

    side: Level2Side
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(ge=0)
    event_time: datetime


class PublicL2Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Level2EventType
    product_id: str
    asset: ResearchAsset
    updates: tuple[PublicL2Update, ...]


class PublicL2Frame(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    sequence: int = Field(ge=0)
    events: tuple[PublicL2Event, ...]


def _decode_payload(message: str | bytes | dict[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(message, bytes):
            if len(message) > MAX_PUBLIC_FRAME_BYTES:
                raise PublicL2IntegrityError("public L2 payload exceeds size limit")
            payload = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            if len(message.encode("utf-8")) > MAX_PUBLIC_FRAME_BYTES:
                raise PublicL2IntegrityError("public L2 payload exceeds size limit")
            payload = json.loads(message)
        else:
            payload = dict(message)
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise PublicL2IntegrityError("invalid public L2 payload") from error
    if not isinstance(payload, dict):
        raise PublicL2IntegrityError("public L2 payload must be an object")
    return payload


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PublicL2IntegrityError(f"{field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicL2IntegrityError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicL2IntegrityError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_sequence(value: object) -> int:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PublicL2IntegrityError("public L2 sequence is invalid")
    try:
        sequence = int(value)
    except ValueError as error:
        raise PublicL2IntegrityError("public L2 sequence is invalid") from error
    if sequence < 0:
        raise PublicL2IntegrityError("public L2 sequence must be non-negative")
    return sequence


def _parse_decimal(value: object, field_name: str, *, allow_zero: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PublicL2IntegrityError(f"{field_name} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise PublicL2IntegrityError(f"{field_name} is invalid") from error
    if not parsed.is_finite():
        raise PublicL2IntegrityError(f"{field_name} must be finite")
    if parsed < 0 or (not allow_zero and parsed == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise PublicL2IntegrityError(f"{field_name} must be {qualifier}")
    return parsed


def _normalize_side(value: object) -> Level2Side:
    if value == "bid":
        return "bid"
    if value in {"offer", "ask"}:
        return "offer"
    raise PublicL2IntegrityError("public L2 side is invalid")


def parse_public_l2_message(
    message: str | bytes | dict[str, Any],
) -> PublicL2Frame | None:
    root = _decode_payload(message)
    if root.get("channel") != "l2_data":
        return None

    timestamp = _parse_timestamp(root.get("timestamp"), "public L2 timestamp")
    sequence = _parse_sequence(root.get("sequence_num"))
    events_value = root.get("events", [])
    if not isinstance(events_value, list):
        raise PublicL2IntegrityError("public L2 events must be an array")
    if len(events_value) > MAX_EVENTS_PER_FRAME:
        raise PublicL2IntegrityError("public L2 event count exceeds limit")

    events: list[PublicL2Event] = []
    for event_value in events_value:
        if not isinstance(event_value, dict):
            raise PublicL2IntegrityError("public L2 event must be an object")
        event_type = event_value.get("type")
        if event_type not in {"snapshot", "update"}:
            raise PublicL2IntegrityError("public L2 event type is invalid")
        product_id = str(event_value.get("product_id", ""))
        asset = SUPPORTED_PUBLIC_PRODUCTS.get(product_id)
        if asset is None:
            continue
        updates_value = event_value.get("updates", [])
        if not isinstance(updates_value, list):
            raise PublicL2IntegrityError("public L2 updates must be an array")
        if len(updates_value) > MAX_L2_UPDATES_PER_EVENT:
            raise PublicL2IntegrityError("public L2 update count exceeds limit")

        updates: list[PublicL2Update] = []
        for update_value in updates_value:
            if not isinstance(update_value, dict):
                raise PublicL2IntegrityError("public L2 update must be an object")
            updates.append(
                PublicL2Update(
                    side=_normalize_side(update_value.get("side")),
                    price=_parse_decimal(
                        update_value.get("price_level"),
                        "public L2 price_level",
                        allow_zero=False,
                    ),
                    quantity=_parse_decimal(
                        update_value.get("new_quantity"),
                        "public L2 new_quantity",
                        allow_zero=True,
                    ),
                    event_time=_parse_timestamp(
                        update_value.get("event_time"),
                        "public L2 event_time",
                    ),
                )
            )
        events.append(
            PublicL2Event(
                event_type=event_type,
                product_id=product_id,
                asset=asset,
                updates=tuple(updates),
            )
        )
    return PublicL2Frame(timestamp=timestamp, sequence=sequence, events=tuple(events))


@dataclass(slots=True)
class _BookState:
    bids: dict[Decimal, Decimal]
    offers: dict[Decimal, Decimal]
    initialized: bool = False


class PublicL2Book:
    """Connection-scoped deterministic L2 reconstruction for public research data."""

    def __init__(
        self,
        *,
        max_levels_per_side: int = MAX_L2_BOOK_LEVELS_PER_SIDE,
        snapshot_depth: int = MAX_L2_SNAPSHOT_LEVELS_PER_SIDE,
    ) -> None:
        if (
            isinstance(max_levels_per_side, bool)
            or not isinstance(max_levels_per_side, int)
            or max_levels_per_side <= 0
        ):
            raise ValueError("max_levels_per_side must be a positive integer")
        if (
            isinstance(snapshot_depth, bool)
            or not isinstance(snapshot_depth, int)
            or snapshot_depth <= 0
            or snapshot_depth > MAX_L2_SNAPSHOT_LEVELS_PER_SIDE
            or snapshot_depth > max_levels_per_side
        ):
            raise ValueError("snapshot_depth is outside supported bounds")
        self.max_levels_per_side = max_levels_per_side
        self.snapshot_depth = snapshot_depth
        self._last_sequence: int | None = None
        self._books: dict[str, _BookState] = {}
        self._frames: list[PublicL2Frame] = []

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def frames(self) -> tuple[PublicL2Frame, ...]:
        return tuple(self._frames)

    def reset(self) -> None:
        self._last_sequence = None
        self._books.clear()
        self._frames.clear()

    def _validate_sequence(self, sequence: int) -> None:
        if self._last_sequence is None:
            return
        expected = self._last_sequence + 1
        if sequence == self._last_sequence:
            raise PublicL2IntegrityError("duplicate public L2 sequence")
        if sequence < self._last_sequence:
            raise PublicL2IntegrityError("regressed public L2 sequence")
        if sequence != expected:
            raise PublicL2IntegrityError(
                f"public L2 sequence gap: expected {expected}, received {sequence}"
            )

    def _state_for(self, product_id: str) -> _BookState:
        return self._books.setdefault(product_id, _BookState(bids={}, offers={}))

    def _apply_event(self, event: PublicL2Event) -> None:
        state = self._state_for(event.product_id)
        if event.event_type == "snapshot":
            state.bids.clear()
            state.offers.clear()
            state.initialized = True
        elif not state.initialized:
            raise PublicL2IntegrityError(
                f"public L2 update before snapshot for {event.product_id}"
            )

        for update in event.updates:
            levels = state.bids if update.side == "bid" else state.offers
            if update.quantity == 0:
                levels.pop(update.price, None)
            else:
                levels[update.price] = update.quantity
            if len(levels) > self.max_levels_per_side:
                raise PublicL2IntegrityError(
                    f"public L2 {update.side} depth exceeds configured limit"
                )

    def ingest(
        self,
        message: str | bytes | dict[str, Any],
    ) -> tuple[OrderBookSnapshot, ...]:
        frame = parse_public_l2_message(message)
        if frame is None:
            return ()
        self._validate_sequence(frame.sequence)

        snapshots: list[OrderBookSnapshot] = []
        for event in frame.events:
            self._apply_event(event)
            snapshots.append(
                self.snapshot(event.product_id, observed_at=frame.timestamp)
            )
        self._last_sequence = frame.sequence
        self._frames.append(frame)
        return tuple(snapshots)

    def snapshot(
        self,
        product_id: str,
        *,
        observed_at: datetime | None = None,
    ) -> OrderBookSnapshot:
        asset = SUPPORTED_PUBLIC_PRODUCTS.get(product_id)
        if asset is None:
            raise ValueError(f"unsupported public product: {product_id}")
        state = self._books.get(product_id)
        if state is None or not state.initialized:
            raise PublicL2IntegrityError(f"public L2 snapshot is not initialized: {product_id}")
        if not state.bids or not state.offers:
            raise PublicL2IntegrityError(
                f"public L2 book requires both sides: {product_id}"
            )

        bid_levels = sorted(state.bids.items(), reverse=True)[: self.snapshot_depth]
        offer_levels = sorted(state.offers.items())[: self.snapshot_depth]
        bids = tuple(
            BookLevel(price=float(price), size=float(size))
            for price, size in bid_levels
        )
        offers = tuple(
            BookLevel(price=float(price), size=float(size))
            for price, size in offer_levels
        )
        return OrderBookSnapshot(
            market_id=f"coinbase-public:{product_id}",
            asset=asset,
            bids=bids,
            asks=offers,
            observed_at=observed_at or datetime.now(UTC),
            source=DataSource.PUBLIC_READ_ONLY,
        )

    def replay_session(self, session_id: str, *, seed: int = 0) -> ReplaySession:
        events: list[ReplayEvent] = []
        for frame in self._frames:
            for index, event in enumerate(frame.events):
                payload = {
                    "channel": "l2_data",
                    "wire_sequence": frame.sequence,
                    "product_id": event.product_id,
                    "asset": event.asset,
                    "event_type": event.event_type,
                    "updates": [
                        {
                            "side": update.side,
                            "price_level": str(update.price),
                            "new_quantity": str(update.quantity),
                            "event_time": update.event_time.isoformat(),
                        }
                        for update in event.updates
                    ],
                }
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                event_id = sha256(
                    f"{frame.sequence}:{index}:{canonical}".encode("utf-8")
                ).hexdigest()
                replay_sequence = frame.sequence * MAX_EVENTS_PER_FRAME + index
                events.append(
                    ReplayEvent(
                        event_id=event_id,
                        observed_at=frame.timestamp,
                        phase=ReplayPhase.MARKET_DATA,
                        stream=f"coinbase-public-l2:{event.asset}",
                        sequence=replay_sequence,
                        event_type=f"public_l2.{event.event_type}",
                        payload=payload,
                    )
                )
        return ReplaySession(session_id=session_id, seed=seed, events=tuple(events))

    def corpus_fingerprint(self) -> str:
        canonical = json.dumps(
            [frame.model_dump(mode="json") for frame in self._frames],
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()
