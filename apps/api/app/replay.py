from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import MarketSnapshot


@dataclass(frozen=True)
class ReplayFrame:
    timestamp: datetime
    snapshot: MarketSnapshot


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
