"""FinRobot contracts (v1): what a specialist reports, and what it can never do.

A FinRobot is an analyst. It reads evidence PROTO already produces (market data, signals,
models, the Risk Engine's and OMS's state) and returns a typed ``FinRobotReport``. It has no
order authority: the contract has no field that could carry an order, a permit or a limit
change, and the boundary flags are pinned to ``False``. Trading still goes signal -> agents ->
Risk Engine permit -> OMS, unchanged (AGENTS.md).

The shared spec, versioned for both branches, is ``docs/finrobots/contract.md``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "finrobots/v1"

# Free text from a report reaches people and ChatGPT. Control characters are stripped so a
# report cannot smuggle terminal escapes or hidden markup; length is bounded per field.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")


def clean_text(value: str, limit: int) -> str:
    return _CONTROL_RE.sub("", value).strip()[:limit]


class FinRobotId(StrEnum):
    """The eight specialists. Their engines are owned by the Codex branch (contract.md)."""

    MARKET_INTELLIGENCE = "market-intelligence"
    QUANT_RESEARCH = "quant-research"
    STRATEGY_RESEARCH = "strategy-research"
    PORTFOLIO_INTELLIGENCE = "portfolio-intelligence"
    MODEL_VALIDATOR = "model-validator"
    RISK_GUARDIAN = "risk-guardian"
    EXECUTION_SUPERVISOR = "execution-supervisor"
    AUDIT_RECOVERY = "audit-recovery"


class Stance(StrEnum):
    """A specialist's view on the instrument (or on the system, when it has no instrument)."""

    ABSTAIN = "ABSTAIN"  # not enough evidence; the default on any doubt
    SUPPORT = "SUPPORT"  # evidence agrees with taking risk in the stated direction
    CAUTION = "CAUTION"  # evidence argues for less risk
    VETO = "VETO"  # evidence argues for no new risk at all (only some roles may veto)


class DataProvenance(StrEnum):
    """Where an evidence item's data came from, labeled truthfully (AGENTS.md)."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    SIMULATED = "SIMULATED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class Evidence(BaseModel):
    """One piece of evidence a report rests on. ``source`` names the PROTO module or route."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    source: str = Field(min_length=1, max_length=120)
    provenance: DataProvenance
    observed_at: datetime
    summary: str = Field(min_length=1, max_length=400)
    # Numbers stay as exact text: no float rounding and nothing a reader could mistake for a
    # computed figure the report did not have.
    metrics: dict[str, str] = Field(default_factory=dict, max_length=24)

    @field_validator("source", "summary")
    @classmethod
    def _clean(cls, value: str, info) -> str:
        cleaned = clean_text(value, 400 if info.field_name == "summary" else 120)
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        return cleaned

    @field_validator("metrics")
    @classmethod
    def _clean_metrics(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, item in value.items():
            name = clean_text(str(key), 48)
            if name:
                out[name] = clean_text(str(item), 64)
        return out

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class FinRobotReport(BaseModel):
    """What one specialist returns for one cycle. Advisory only, never an order."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["finrobots/v1"] = CONTRACT_VERSION
    report_id: UUID = Field(default_factory=uuid4)
    robot_id: FinRobotId
    implementation: str = Field(min_length=1, max_length=80)  # e.g. "risk-guardian@1.0.0"
    cycle_id: UUID
    instrument_id: str | None = Field(default=None, max_length=160)
    stance: Stance = Stance.ABSTAIN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    headline: str = Field(min_length=1, max_length=200)
    rationale: tuple[str, ...] = Field(default=(), max_length=8)
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=16)
    produced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Pinned: a report can never claim financial connectivity or real-money execution.
    financial_connectivity: Literal[False] = False
    real_money_execution: Literal[False] = False

    @field_validator("headline")
    @classmethod
    def _clean_headline(cls, value: str) -> str:
        cleaned = clean_text(value, 200)
        if not cleaned:
            raise ValueError("headline must not be empty")
        return cleaned

    @field_validator("rationale")
    @classmethod
    def _clean_rationale(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(c for c in (clean_text(item, 280) for item in value) if c)

    @field_validator("implementation")
    @classmethod
    def _clean_implementation(cls, value: str) -> str:
        return clean_text(value, 80) or "unknown"

    @model_validator(mode="after")
    def _evidence_backs_a_view(self) -> FinRobotReport:
        # A view without evidence is not a view: anything but ABSTAIN must cite evidence, and
        # evidence that is itself unavailable cannot back one.
        if self.stance is not Stance.ABSTAIN:
            usable = [e for e in self.evidence if e.provenance is not DataProvenance.UNAVAILABLE]
            if not usable:
                raise ValueError("a non-ABSTAIN stance must cite usable evidence")
        return self


def valid_robot_key(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))
