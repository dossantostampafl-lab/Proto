"""Hybrid trading agents: deterministic fusion and sizing, AI review that can only shrink."""

from .evaluation import ReplayCase, ReviewerEvaluation, evaluate_reviewer
from .fusion import Candidate, FusionDecision, FusionPolicy, SizingPolicy, build_candidate, fuse
from .pipeline import AgentDecision, DecisionOutcome, HybridDecisionPipeline, PortfolioView
from .review import (
    AgentReview,
    AgentReviewer,
    ClaudeReviewer,
    DeterministicReviewer,
    ReviewContext,
    Verdict,
    apply_review,
)

__all__ = [
    "AgentDecision",
    "AgentReview",
    "AgentReviewer",
    "Candidate",
    "ClaudeReviewer",
    "DecisionOutcome",
    "DeterministicReviewer",
    "FusionDecision",
    "FusionPolicy",
    "HybridDecisionPipeline",
    "PortfolioView",
    "ReplayCase",
    "ReviewContext",
    "ReviewerEvaluation",
    "SizingPolicy",
    "Verdict",
    "apply_review",
    "build_candidate",
    "evaluate_reviewer",
    "fuse",
]
