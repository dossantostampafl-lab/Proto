"""Pieces every HTTP inference provider shares, so they cannot drift apart."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.inference.contracts import ProviderHealth

CAPABILITY_INTENT_TOOL = "capability_intent"
CAPABILITY_INTENT_DESCRIPTION = "Request an authorized capability. This only requests execution; policy decides whether it may run."


def capability_intent_parameters() -> dict[str, Any]:
    """The JSON schema of the one tool an Agent may reach for: asking that a capability be run.

    Asking is not doing. The gateway's policy decides whether the Mission's authorization
    allows it, and the payload is validated against CapabilityIntent before anything runs.
    """
    return {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "action": {"type": "string"},
            "resource": {"type": ["string", "null"]},
            "arguments": {"type": "object", "additionalProperties": True},
            "external_effect": {"type": "boolean"},
            "idempotency_class": {"type": "string", "enum": ["SAFE", "IDEMPOTENT", "AT_MOST_ONCE"]},
            "idempotency_key": {"type": ["string", "null"]},
        },
        "required": ["capability", "action", "arguments", "external_effect", "idempotency_class"],
    }


def chat_delta_content(event: Any) -> str | None:
    """The text a Chat Completions stream event adds, if any."""
    if not isinstance(event, dict):
        return None
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    return content if isinstance(content, str) and content else None


async def probe_health(client: httpx.AsyncClient, provider: str, url: str) -> ProviderHealth:
    """GET a provider endpoint and classify the answer the same way for every provider."""
    try:
        async with client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return ProviderHealth(provider=provider, available=False, detail="timeout")
    except httpx.HTTPError:
        return ProviderHealth(provider=provider, available=False, detail="network_error")

    if 200 <= response.status_code < 300:
        return ProviderHealth(provider=provider, available=True)
    if response.status_code in {401, 403}:
        detail = "authentication_failed"
    elif response.status_code == 429:
        detail = "rate_limited"
    else:
        detail = f"upstream_status_{response.status_code}"
    return ProviderHealth(provider=provider, available=False, detail=detail)


def required_env(name: str, provider: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required when LLM_PROVIDER={provider}")
    return value


def positive_env(name: str, default: str, kind: type[float] | type[int]) -> Any:
    raw = os.getenv(name, default).strip()
    try:
        value = kind(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be {'numeric' if kind is float else 'an integer'}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value
