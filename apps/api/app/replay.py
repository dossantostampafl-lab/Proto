from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .models import MarketSnapshot

ReplaySpeed = Literal["1x", "5x", "10x", "50x", "100x", "MAX"]


@dataclass(frozen=True)
class ReplayFrame:
    timestamp: datetime
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class ReplayCheckpoint:
    cursor: int
    speed: ReplaySpeed
    paused: bool


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
        self._frames = sorted(frames, key=lambda item: item.timestamp)
        self._cursor = 0

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
        remaining = self._frames[self._cursor :]
        self._cursor = len(self._frames)
        return remaining


class ReplaySession:
    """Stateful deterministic replay controller for the simulation runtime."""

    def __init__(self) -> None:
        self._engine: HistoricalReplay | None = None
        self._speed: ReplaySpeed = "1x"
        self._paused = True
        self._last_frame: ReplayFrame | None = None

    @property
    def active(self) -> bool:
        return self._engine is not None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed(self) -> ReplaySpeed:
        return self._speed

    def start(self, request: ReplayStartRequest) -> dict[str, object]:
        frames = [
            ReplayFrame(timestamp=item.timestamp, snapshot=item.snapshot)
            for item in request.frames
        ]
        self._engine = HistoricalReplay(frames)
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
        self._last_frame = engine.previous()
        self._paused = True
        return self.status()

    def set_speed(self, speed: ReplaySpeed) -> dict[str, object]:
        self._require_engine()
        self._speed = speed
        return self.status()

    def checkpoint(self) -> ReplayCheckpoint:
        engine = self._require_engine()
        return ReplayCheckpoint(
            cursor=engine.cursor,
            speed=self._speed,
            paused=self._paused,
        )

    def restore(self, checkpoint: ReplayCheckpoint) -> dict[str, object]:
        engine = self._require_engine()
        try:
            engine.seek(checkpoint.cursor)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        self._speed = checkpoint.speed
        self._paused = checkpoint.paused or engine.finished
        self._last_frame = engine.previous()
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
            }
        return {
            "active": True,
            "paused": self._paused,
            "speed": self._speed,
            "cursor": self._engine.cursor,
            "total_frames": self._engine.total_frames,
            "finished": self._engine.finished,
            "last_timestamp": self._last_frame.timestamp if self._last_frame else None,
        }

    def _require_engine(self) -> HistoricalReplay:
        if self._engine is None:
            raise RuntimeError("replay session has not been started")
        return self._engine
