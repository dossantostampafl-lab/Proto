from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from services.replay import ReplayEngine, ReplayEvent, ReplayPhase
from services.replay import ReplaySession as CoreReplaySession

from .models import MarketSnapshot

ReplaySpeed = Literal["1x", "5x", "10x", "50x", "100x", "MAX"]


@dataclass(frozen=True)
class ReplayFrame:
    timestamp: datetime
    snapshot: MarketSnapshot


class ReplayFrameInput(BaseModel):
    timestamp: datetime
    snapshot: MarketSnapshot


class ReplayStartRequest(BaseModel):
    frames: list[ReplayFrameInput] = Field(min_length=1, max_length=100_000)
    speed: ReplaySpeed = "1x"


class ReplaySeekRequest(BaseModel):
    cursor: int = Field(ge=0)


class ReplaySpeedRequest(BaseModel):
    speed: ReplaySpeed


class HistoricalReplay:
    def __init__(self, frames: list[ReplayFrame]) -> None:
        indexed_frames = list(enumerate(frames))
        indexed_frames.sort(
            key=lambda item: (
                item[1].timestamp,
                item[1].snapshot.market_id,
                item[0],
            )
        )

        sequence_by_stream: dict[str, int] = {}
        frame_by_event_id: dict[str, ReplayFrame] = {}
        events: list[ReplayEvent] = []
        for original_index, frame in indexed_frames:
            stream = frame.snapshot.market_id
            sequence = sequence_by_stream.get(stream, 0)
            sequence_by_stream[stream] = sequence + 1
            event_id = f"frame-{original_index}"
            frame_by_event_id[event_id] = frame
            events.append(
                ReplayEvent(
                    event_id=event_id,
                    observed_at=frame.timestamp,
                    phase=ReplayPhase.MARKET_DATA,
                    stream=stream,
                    sequence=sequence,
                    event_type="MARKET_SNAPSHOT",
                    payload=frame.snapshot.model_dump(mode="json"),
                )
            )

        session = CoreReplaySession(
            session_id="api-historical-replay",
            seed=0,
            events=tuple(events),
        )
        self._core_engine = ReplayEngine(session)
        self._frames = tuple(
            frame_by_event_id[event.event_id] for event in self._core_engine.ordered_events
        )
        self._cursor = 0

    @property
    def fingerprint(self) -> str:
        return self._core_engine.fingerprint()

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def finished(self) -> bool:
        return self._cursor >= len(self._frames)

    def reset(self) -> None:
        self._cursor = 0

    def seek(self, cursor: int) -> None:
        if cursor > len(self._frames):
            raise ValueError("replay cursor exceeds total frames")
        self._cursor = cursor

    def previous(self) -> ReplayFrame | None:
        if self._cursor == 0:
            return None
        return self._frames[self._cursor - 1]

    def next(self) -> ReplayFrame | None:
        if self.finished:
            return None
        frame = self._frames[self._cursor]
        self._cursor += 1
        return frame

    def run_all(self) -> list[ReplayFrame]:
        remaining = list(self._frames[self._cursor :])
        self._cursor = len(self._frames)
        return remaining


class ReplaySession:
    """Stateful deterministic replay controller for the simulation runtime."""

    def __init__(self, on_timeline_reset: Callable[[], None] | None = None) -> None:
        self._engine: HistoricalReplay | None = None
        self._speed: ReplaySpeed = "1x"
        self._paused = True
        self._last_frame: ReplayFrame | None = None
        self._on_timeline_reset = on_timeline_reset

    @property
    def active(self) -> bool:
        return self._engine is not None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed(self) -> ReplaySpeed:
        return self._speed

    @property
    def current_timestamp(self) -> datetime | None:
        return self._last_frame.timestamp if self._last_frame is not None else None

    def _reset_timeline_state(self) -> None:
        if self._on_timeline_reset is not None:
            self._on_timeline_reset()

    def start(self, request: ReplayStartRequest) -> dict[str, object]:
        frames = [
            ReplayFrame(
                timestamp=item.timestamp,
                snapshot=item.snapshot.model_copy(update={"observed_at": item.timestamp}),
            )
            for item in request.frames
        ]
        engine = HistoricalReplay(frames)
        self._reset_timeline_state()
        self._engine = engine
        self._speed = request.speed
        self._paused = False
        self._last_frame = None
        return self.status()

    def pause(self) -> dict[str, object]:
        self._require_engine()
        self._paused = True
        return self.status()

    def resume(self) -> dict[str, object]:
        engine = self._require_engine()
        if engine.finished:
            raise RuntimeError("replay is already finished")
        self._paused = False
        return self.status()

    def step(self) -> ReplayFrame | None:
        engine = self._require_engine()
        frame = engine.next()
        if frame is not None:
            self._last_frame = frame
        if engine.finished:
            self._paused = True
        return frame

    def restart(self) -> dict[str, object]:
        engine = self._require_engine()
        self._reset_timeline_state()
        engine.reset()
        self._paused = False
        self._last_frame = None
        return self.status()

    def seek(self, cursor: int) -> dict[str, object]:
        engine = self._require_engine()
        try:
            engine.seek(cursor)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        self._reset_timeline_state()
        self._last_frame = engine.previous()
        self._paused = True
        return self.status()

    def set_speed(self, speed: ReplaySpeed) -> dict[str, object]:
        self._require_engine()
        self._speed = speed
        return self.status()

    def reset(self) -> None:
        self._engine = None
        self._speed = "1x"
        self._paused = True
        self._last_frame = None

    def status(self) -> dict[str, object]:
        if self._engine is None:
            return {
                "active": False,
                "paused": True,
                "speed": self._speed,
                "cursor": 0,
                "total_frames": 0,
                "finished": False,
                "last_timestamp": None,
                "fingerprint": None,
            }
        return {
            "active": True,
            "paused": self._paused,
            "speed": self._speed,
            "cursor": self._engine.cursor,
            "total_frames": self._engine.total_frames,
            "finished": self._engine.finished,
            "last_timestamp": self._last_frame.timestamp if self._last_frame else None,
            "fingerprint": self._engine.fingerprint,
        }

    def _require_engine(self) -> HistoricalReplay:
        if self._engine is None:
            raise RuntimeError("replay session has not been started")
        return self._engine
