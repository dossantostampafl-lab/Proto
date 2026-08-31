from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FeedHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DARK = "DARK"
    RECOVERING = "RECOVERING"


class FeedHealthSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: FeedHealthState
    age_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    consecutive_clean_updates: int = Field(ge=0)
    risk_multiplier: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    allow_new_risk: bool


class FeedHealthTracker:
    """Deterministic feed-health state machine driven only by caller timestamps."""

    def __init__(
        self,
        *,
        degraded_after_seconds: float = 1.0,
        stale_after_seconds: float = 5.0,
        dark_after_seconds: float = 15.0,
        recovery_clean_updates: int = 3,
    ) -> None:
        if not 0.0 < degraded_after_seconds < stale_after_seconds < dark_after_seconds:
            raise ValueError("feed health thresholds must be strictly increasing")
        if recovery_clean_updates < 1:
            raise ValueError("recovery_clean_updates must be positive")
        self.degraded_after_seconds = degraded_after_seconds
        self.stale_after_seconds = stale_after_seconds
        self.dark_after_seconds = dark_after_seconds
        self.recovery_clean_updates = recovery_clean_updates
        self._state = FeedHealthState.HEALTHY
        self._consecutive_clean_updates = 0

    @property
    def state(self) -> FeedHealthState:
        return self._state

    def reset(self) -> None:
        self._state = FeedHealthState.HEALTHY
        self._consecutive_clean_updates = 0

    def evaluate(
        self,
        *,
        observed_at: datetime,
        now: datetime,
        data_valid: bool = True,
    ) -> FeedHealthSnapshot:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        age_seconds = max((now - observed_at).total_seconds(), 0.0)
        target = self._target_state(age_seconds=age_seconds, data_valid=data_valid)

        if target is FeedHealthState.HEALTHY and self._state in {
            FeedHealthState.STALE,
            FeedHealthState.DARK,
            FeedHealthState.RECOVERING,
        }:
            self._consecutive_clean_updates += 1
            if self._consecutive_clean_updates >= self.recovery_clean_updates:
                self._state = FeedHealthState.HEALTHY
                self._consecutive_clean_updates = 0
            else:
                self._state = FeedHealthState.RECOVERING
        else:
            self._state = target
            if target is not FeedHealthState.RECOVERING:
                self._consecutive_clean_updates = 0

        risk_multiplier, allow_new_risk = self._risk_policy(self._state)
        return FeedHealthSnapshot(
            state=self._state,
            age_seconds=age_seconds,
            consecutive_clean_updates=self._consecutive_clean_updates,
            risk_multiplier=risk_multiplier,
            allow_new_risk=allow_new_risk,
        )

    def _target_state(self, *, age_seconds: float, data_valid: bool) -> FeedHealthState:
        if not data_valid or age_seconds >= self.dark_after_seconds:
            return FeedHealthState.DARK
        if age_seconds >= self.stale_after_seconds:
            return FeedHealthState.STALE
        if age_seconds >= self.degraded_after_seconds:
            return FeedHealthState.DEGRADED
        return FeedHealthState.HEALTHY

    @staticmethod
    def _risk_policy(state: FeedHealthState) -> tuple[float, bool]:
        if state is FeedHealthState.HEALTHY:
            return 1.0, True
        if state is FeedHealthState.DEGRADED:
            return 0.5, True
        if state is FeedHealthState.RECOVERING:
            return 0.25, False
        return 0.0, False
