from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CostTier(IntEnum):
    UNKNOWN = -1
    FREE = 0
    LOW = 1
    PREMIUM = 2
    FRONTIER = 3


class ModelRequirements(BaseModel):
    preferred_provider: str | None = None
    fallback_providers: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    max_cost_tier: CostTier | None = None
    routing_strategy: Literal["ordered", "benchmark"] = "ordered"
    requires_streaming: bool = False
    max_output_tokens: int | None = Field(default=None, ge=1)


class ProviderModelProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    cost_tier: CostTier = CostTier.UNKNOWN
    is_default: bool = False


class ProviderBenchmarkEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    suite_id: str = Field(..., min_length=1)
    score: float = Field(..., ge=0.0, le=1.0)
    sample_count: int = Field(..., ge=1)
    observed_at: datetime


class InferenceRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    model: str | None = None
    requirements: ModelRequirements = Field(default_factory=ModelRequirements)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderHealth(BaseModel):
    provider: str
    available: bool
    detail: str | None = None


class InferenceError(RuntimeError):
    code: str = "INFERENCE_ERROR"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        upstream_status: int | None = None,
        upstream_code: str | None = None,
        upstream_param: str | None = None,
        request_id: str | None = None,
        upstream_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.upstream_status = upstream_status
        self.upstream_code = upstream_code
        self.upstream_param = upstream_param
        self.request_id = request_id
        self.upstream_body = upstream_body
        # When every candidate failed, the error raised is the last one; this keeps the first
        # (the configured provider's) failure, which is usually the one worth knowing.
        self.first_failure: InferenceError | None = None


class InferenceConfigurationError(InferenceError):
    code: Literal["INFERENCE_CONFIGURATION_ERROR"] = "INFERENCE_CONFIGURATION_ERROR"


class InferenceAuthenticationError(InferenceError):
    code: Literal["INFERENCE_AUTHENTICATION_ERROR"] = "INFERENCE_AUTHENTICATION_ERROR"


class ProviderUnavailable(InferenceError):
    code: str = "PROVIDER_UNAVAILABLE"


class InferenceBudgetError(ProviderUnavailable):
    code: Literal["INFERENCE_BUDGET_ERROR"] = "INFERENCE_BUDGET_ERROR"


class InferenceRateLimitError(ProviderUnavailable):
    code: Literal["INFERENCE_RATE_LIMIT"] = "INFERENCE_RATE_LIMIT"


class InferenceTimeoutError(ProviderUnavailable):
    code: Literal["INFERENCE_TIMEOUT"] = "INFERENCE_TIMEOUT"


class InferenceUpstreamResponseError(ProviderUnavailable):
    code: Literal["INFERENCE_UPSTREAM_RESPONSE_ERROR"] = "INFERENCE_UPSTREAM_RESPONSE_ERROR"
