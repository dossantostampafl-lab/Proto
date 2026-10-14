"""Promotion gates per account x venue x strategy x model version x credential (Phase 6)."""

from services.execution.constitution import CANARY_CAPITAL

from .gates import (
    EVIDENCE_MAX_AGE,
    MIN_PAPER_PERIOD,
    REQUIRED_EVIDENCE,
    STAGE_ORDER,
    Evidence,
    EvidenceKind,
    PromotionRefused,
    Scope,
    ScopeRecord,
    gate_check,
)
from .registry import PromotionRegistry, promotion_metadata, promotion_table

__all__ = [
    "CANARY_CAPITAL",
    "EVIDENCE_MAX_AGE",
    "MIN_PAPER_PERIOD",
    "REQUIRED_EVIDENCE",
    "STAGE_ORDER",
    "Evidence",
    "EvidenceKind",
    "PromotionRefused",
    "PromotionRegistry",
    "Scope",
    "ScopeRecord",
    "gate_check",
    "promotion_metadata",
    "promotion_table",
]
