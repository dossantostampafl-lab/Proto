from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LiveSequenceState:
    """Connection-scoped sequence integrity state for the public live monitor."""

    last_sequence: dict[str, int] = field(default_factory=dict)
    rejections: dict[str, dict[str, int]] = field(default_factory=dict)

    def reset(self) -> None:
        self.last_sequence.clear()
        self.rejections.clear()

    def previous(self, symbol: str) -> int | None:
        return self.last_sequence.get(symbol)

    def accept(self, symbol: str, sequence: int) -> None:
        self.last_sequence[symbol] = sequence

    def record_rejection(self, symbol: str, reason: str) -> None:
        if reason not in {"duplicate", "regression"}:
            raise ValueError("unsupported live sequence rejection reason")
        counts = self.rejections.setdefault(symbol, {"duplicate": 0, "regression": 0})
        counts[reason] += 1

    def rejection_snapshot(
        self,
        expected_symbols: tuple[str, ...],
    ) -> tuple[int, dict[str, dict[str, int]]]:
        by_symbol: dict[str, dict[str, int]] = {}
        total = 0
        for symbol in expected_symbols:
            counts = self.rejections.get(symbol, {})
            duplicate = int(counts.get("duplicate", 0))
            regression = int(counts.get("regression", 0))
            symbol_total = duplicate + regression
            total += symbol_total
            by_symbol[symbol] = {
                "duplicate": duplicate,
                "regression": regression,
                "total": symbol_total,
            }
        return total, by_symbol

    def last_sequence_snapshot(self) -> dict[str, int]:
        return dict(sorted(self.last_sequence.items()))
