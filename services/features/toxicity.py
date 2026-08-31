from __future__ import annotations

from collections import deque
from math import isfinite


class RollingVPIN:
    """Volume-synchronized probability of informed trading estimator.

    Signed volume is positive for buyer-initiated flow and negative for seller-initiated flow.
    Trades that cross a bucket boundary are split deterministically across equal-volume buckets.
    VPIN is the rolling mean absolute buy/sell imbalance normalized by bucket volume.
    """

    def __init__(self, *, bucket_volume: float, window_buckets: int = 50) -> None:
        if not isfinite(bucket_volume) or bucket_volume <= 0.0:
            raise ValueError("bucket_volume must be finite and positive")
        if window_buckets <= 0:
            raise ValueError("window_buckets must be positive")
        self.bucket_volume = bucket_volume
        self.window_buckets = window_buckets
        self._completed_imbalances: deque[float] = deque(maxlen=window_buckets)
        self._bucket_buy = 0.0
        self._bucket_sell = 0.0
        self._bucket_filled = 0.0

    @property
    def completed_bucket_count(self) -> int:
        return len(self._completed_imbalances)

    @property
    def current(self) -> float | None:
        if not self._completed_imbalances:
            return None
        return sum(self._completed_imbalances) / (
            len(self._completed_imbalances) * self.bucket_volume
        )

    def update(self, signed_volume: float) -> float | None:
        if not isfinite(signed_volume):
            raise ValueError("signed_volume must be finite")
        remaining = abs(signed_volume)
        if remaining == 0.0:
            return self.current

        is_buy = signed_volume > 0.0
        while remaining > 0.0:
            capacity = self.bucket_volume - self._bucket_filled
            take = min(remaining, capacity)
            if is_buy:
                self._bucket_buy += take
            else:
                self._bucket_sell += take
            self._bucket_filled += take
            remaining -= take

            if self._bucket_filled >= self.bucket_volume - 1e-12:
                self._completed_imbalances.append(abs(self._bucket_buy - self._bucket_sell))
                self._bucket_buy = 0.0
                self._bucket_sell = 0.0
                self._bucket_filled = 0.0

        return self.current
