from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.inference.chatgpt_credentials import ChatGPTCredentialStore
from app.inference.contracts import (
    InferenceAuthenticationError,
    InferenceConfigurationError,
    InferenceRateLimitError,
    InferenceRequest,
    InferenceResponse,
    InferenceUpstreamResponseError,
    ProviderHealth,
    ProviderUnavailable,
)
from app.inference.provider_common import (
    CAPABILITY_INTENT_DESCRIPTION,
    CAPABILITY_INTENT_TOOL,
    capability_intent_parameters,
)

CHATGPT_USAGE_SETTINGS_URL = "https://chatgpt.com/#settings/Usage"


def listed_model_slugs(payload: Any) -> list[str]:
    """Return only display-visible SIWC model slugs, preserving server ordering."""
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    slugs: list[str] = []
    for item in models:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if isinstance(slug, str) and slug.strip():
            slugs.append(slug.strip())
    return slugs


class ChatGPTPlanProvider:
    """Responses API provider authenticated by Sign in with ChatGPT OAuth."""

    name = "chatgpt"
    usage_url = CHATGPT_USAGE_SETTINGS_URL

    def __init__(
        self,
        *,
        credentials: ChatGPTCredentialStore,
        default_model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not default_model.strip():
            raise ValueError("CHATGPT_MODEL is required")
        self.credentials = credentials
        self.default_model = default_model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def account_label(self) -> str | None:
        try:
            return self.credentials._read().email
        except (InferenceAuthenticationError, OSError, ValueError):
            return None

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self.credentials.access_token()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _error_details(payload: Any) -> tuple[str | None, str | None]:
        """Return only structured upstream error fields that actually exist.

        Sign in with ChatGPT can reject a request before a Responses stream opens
        with a direct-admission body such as {"detail": "..."}. Inventing the
        generic code "provider_error" for that shape hides the useful distinction
        between a transient 503 and a structured contract error.
        """
        if not isinstance(payload, dict):
            return None, None
        error = payload.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code") or error.get("type")
            code = str(raw_code) if raw_code is not None else None
            param = error.get("param")
            return code, str(param) if param is not None else None
        return None, None

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        for name in ("x-request-id", "openai-request-id", "request-id"):
            value = response.headers.get(name)
            if value:
                return value
        return None

    async def health(self) -> ProviderHealth:
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 10.0)) as client:
                response = await client.get(f"{self.base_url}/models", headers=headers)
        except (InferenceAuthenticationError, InferenceConfigurationError) as exc:
            return ProviderHealth(provider=self.name, available=False, detail=str(exc))
        except (ProviderUnavailable, httpx.HTTPError) as exc:
            return ProviderHealth(provider=self.name, available=False, detail=exc.__class__.__name__)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            code, _ = self._error_details(payload)
            direct_detail = payload.get("detail") if isinstance(payload, dict) else None
            diagnostic = code or (direct_detail if isinstance(direct_detail, str) else None) or "unstructured error"
            return ProviderHealth(
                provider=self.name,
                available=False,
                detail=f"model catalog returned HTTP {response.status_code}: {diagnostic}",
            )
        try:
            payload = response.json()
        except ValueError:
            return ProviderHealth(provider=self.name, available=False, detail="model catalog returned invalid JSON")
        slugs = listed_model_slugs(payload)
        if not slugs:
            return ProviderHealth(provider=self.name, available=False, detail="model catalog exposed no display-visible models")
        if self.default_model not in slugs:
            return ProviderHealth(
                provider=self.name,
                available=False,
                detail=f"configured model is not display-visible: {self.default_model}",
            )
        return ProviderHealth(provider=self.name, available=True)

    @staticmethod
    def _input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if item.get("role") == "system":
                item["role"] = "developer"
            normalized.append(item)
        return normalized

    def _payload(self, request: InferenceRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self.default_model,
            "input": self._input(request.messages),
            "store": False,
            "stream": True,
        }
        if request.metadata.get("enable_capability_intents"):
            payload["tools"] = [
                {
                    "type": "namespace",
                    "name": "creation",
                    "description": "Authorized local Creation OS capabilities.",
                    "tools": [
                        {
                            "type": "function",
                            "name": CAPABILITY_INTENT_TOOL,
                            "description": CAPABILITY_INTENT_DESCRIPTION,
                            "parameters": {
                                **capability_intent_parameters(),
                                "additionalProperties": False,
                            },
                            # Strict mode rejects this schema (free-form `arguments`, optional
                            # fields) with invalid_function_parameters; CapabilityIntent validates it.
                            "strict": False,
                        }
                    ],
                }
            ]
        return payload

    def _raise_http_error(self, response: httpx.Response) -> None:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"non_json_body": response.text[:2000]}
        code, param = self._error_details(payload)
        direct_detail = payload.get("detail") if isinstance(payload, dict) else None
        diagnostic = code or (direct_detail if isinstance(direct_detail, str) else None) or "unstructured error"
        message = f"ChatGPT returned HTTP {response.status_code}: {diagnostic}"
        request_id = self._request_id(response)
        if code == "subscription_sharing_usage_limit_exceeded" or response.status_code == 429:
            raise InferenceRateLimitError(
                self.name, message,
                upstream_status=response.status_code, upstream_code=code, upstream_param=param,
                request_id=request_id, upstream_body=payload,
            )
        if code in {
            "subscription_sharing_usage_unavailable",
            "subscription_sharing_user_unavailable",
            "subscription_sharing_user_not_eligible",
        }:
            raise ProviderUnavailable(
                self.name, message,
                upstream_status=response.status_code, upstream_code=code, upstream_param=param,
                request_id=request_id, upstream_body=payload,
            )
        if code in {
            "subscription_sharing_invalid_user",
            "chatpass_v2_scope_not_authorized",
            "chatpass_v2_invalid_authorization_context",
        } or response.status_code == 401:
            raise InferenceAuthenticationError(
                self.name, message,
                upstream_status=response.status_code, upstream_code=code, upstream_param=param,
                request_id=request_id, upstream_body=payload,
            )
        if code in {
            "subscription_sharing_unsupported_capability",
            "subscription_sharing_route_not_supported",
        }:
            raise InferenceUpstreamResponseError(
                self.name, message,
                upstream_status=response.status_code, upstream_code=code, upstream_param=param,
                request_id=request_id, upstream_body=payload,
            )
        if response.status_code in {402, 403} or response.status_code >= 500:
            raise ProviderUnavailable(
                self.name, message,
                upstream_status=response.status_code, upstream_code=code, upstream_param=param,
                request_id=request_id, upstream_body=payload,
            )
        raise InferenceUpstreamResponseError(
            self.name, message,
            upstream_status=response.status_code, upstream_code=code, upstream_param=param,
            request_id=request_id, upstream_body=payload,
        )

    @staticmethod
    def _capability_from_output(response_payload: dict[str, Any]) -> dict[str, Any] | None:
        output = response_payload.get("output")
        if not isinstance(output, list):
            return None
        for item in output:
            if not isinstance(item, dict) or item.get("type") not in {"function_call", "tool_call"}:
                continue
            if item.get("name") != CAPABILITY_INTENT_TOOL:
                continue
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = None
            if isinstance(arguments, dict):
                return arguments
        return None

    @staticmethod
    def _with_message(code: str, message: Any) -> str:
        """The stream's own error message, kept so a failure without a code can still be told apart."""
        if isinstance(message, str) and message.strip():
            return f"{code}: {message.strip()[:200]}"
        return code

    @staticmethod
    def _shape(event: dict[str, Any]) -> str:
        """Names of the fields an unrecognized error event carried; field names only, no values."""
        nested = event.get("error")
        keys = sorted(str(key) for key in event)
        if isinstance(nested, dict):
            keys.append("error{" + ",".join(sorted(str(key) for key in nested)) + "}")
        return "unrecognized error event: " + ",".join(keys)

    def _raise_stream_failure(self, event: dict[str, Any]) -> None:
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
        code = (
            str(error.get("code") or error.get("type") or "provider_error")
            if isinstance(error, dict)
            else "provider_error"
        )
        param = str(error.get("param")) if isinstance(error, dict) and error.get("param") is not None else None
        upstream_message = error.get("message") if isinstance(error, dict) else None
        if not upstream_message and isinstance(response, dict):
            upstream_message = f"response status {response.get('status')}"
        if not upstream_message:
            upstream_message = self._shape(event)
        code_text = self._with_message(code, upstream_message)
        if code == "subscription_sharing_usage_limit_exceeded":
            raise InferenceRateLimitError(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        if code in {
            "subscription_sharing_usage_unavailable",
            "subscription_sharing_user_unavailable",
            "subscription_sharing_user_not_eligible",
            "subscription_sharing_not_enabled",
        }:
            raise ProviderUnavailable(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        if code in {
            "subscription_sharing_invalid_user",
            "chatpass_v2_scope_not_authorized",
            "chatpass_v2_invalid_authorization_context",
        }:
            raise InferenceAuthenticationError(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        raise InferenceUpstreamResponseError(
            self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
        )

    def _raise_stream_error(self, event: dict[str, Any]) -> None:
        # The error fields arrive either on the event itself or nested under "error".
        raw_nested = event.get("error")
        nested: dict[str, Any] = raw_nested if isinstance(raw_nested, dict) else {}
        raw_code = event.get("code") or nested.get("code") or nested.get("type")
        code = str(raw_code or "provider_error")
        raw_param = event.get("param") if event.get("param") is not None else nested.get("param")
        param = str(raw_param) if raw_param is not None else None
        message = event.get("message") or nested.get("message") or self._shape(event)
        code_text = self._with_message(code, message)
        if code == "subscription_sharing_usage_limit_exceeded":
            raise InferenceRateLimitError(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        if code in {
            "subscription_sharing_usage_unavailable",
            "subscription_sharing_user_unavailable",
            "subscription_sharing_user_not_eligible",
            "subscription_sharing_not_enabled",
        }:
            raise ProviderUnavailable(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        if code in {
            "subscription_sharing_invalid_user",
            "chatpass_v2_scope_not_authorized",
            "chatpass_v2_invalid_authorization_context",
        }:
            raise InferenceAuthenticationError(
                self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
            )
        raise InferenceUpstreamResponseError(
            self.name, code_text, upstream_code=code, upstream_param=param, upstream_body=event
        )

    async def _events(self, request: InferenceRequest) -> AsyncIterator[dict[str, Any]]:
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/responses",
                    headers=headers,
                    json=self._payload(request),
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        self._raise_http_error(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line.removeprefix("data: ").strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        event_type = event.get("type")
                        if event_type == "response.failed":
                            self._raise_stream_failure(event)
                        if event_type == "error":
                            self._raise_stream_error(event)
                        if event_type == "response.incomplete":
                            raise ProviderUnavailable(
                                self.name,
                                "ChatGPT response was incomplete",
                                upstream_code="response_incomplete",
                                upstream_body=event,
                            )
                        yield event
        except (
            InferenceAuthenticationError,
            InferenceConfigurationError,
            InferenceRateLimitError,
            InferenceUpstreamResponseError,
            ProviderUnavailable,
        ):
            raise
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(self.name, "ChatGPT request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                self.name,
                f"ChatGPT request failed: {exc.__class__.__name__}",
            ) from exc

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        text_parts: list[str] = []
        completed: dict[str, Any] | None = None
        async for event in self._events(request):
            if event.get("type") == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event.get("type") == "response.completed":
                response = event.get("response")
                if isinstance(response, dict):
                    completed = response
        if completed is None:
            raise ProviderUnavailable(self.name, "ChatGPT stream ended without response.completed")
        metadata: dict[str, Any] = {}
        capability = self._capability_from_output(completed)
        if capability is not None:
            metadata["capability_intent"] = capability
        raw_usage = completed.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        normalized_usage = {
            key: int(value)
            for key, value in usage.items()
            if isinstance(value, int)
        }
        return InferenceResponse(
            provider=self.name,
            model=str(completed.get("model") or request.model or self.default_model),
            content="".join(text_parts),
            finish_reason=str(completed.get("status") or "completed"),
            usage=normalized_usage,
            metadata=metadata,
        )

    async def stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        completed = False
        async for event in self._events(request):
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield delta
            elif event_type == "response.completed":
                completed = True
        if not completed:
            raise ProviderUnavailable(self.name, "ChatGPT stream ended without response.completed")
