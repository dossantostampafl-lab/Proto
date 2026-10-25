from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.inference.contracts import (
    InferenceAuthenticationError,
    InferenceConfigurationError,
    ProviderUnavailable,
)

_TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
_RESOURCE = "https://api.openai.com/v1"
_REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
_TERMINAL_REFRESH_CODES = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)


@dataclass(frozen=True)
class ChatGPTCredentialRecord:
    client_id: str
    access_token: str
    refresh_token: str
    scopes: frozenset[str]
    saved_at: datetime
    expires_in: int
    id_token: str | None = None
    email: str | None = None
    subject: str | None = None
    ext_agent_host_id: str | None = None
    earliest_refresh_at: str | int | float | None = None

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(
            self.saved_at.timestamp() + self.expires_in,
            tz=timezone.utc,
        )


class _HostFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    async def __aenter__(self) -> "_HostFileLock":
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
        self._fd = fd
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        import fcntl

        if self._fd is None:
            return
        await asyncio.to_thread(fcntl.flock, self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


class ChatGPTCredentialStore:
    """Protected self-hosted storage for Sign in with ChatGPT credentials.

    The credential file is deliberately outside source control and shared by the
    Creation OS containers through a named volume. Access tokens are refreshed
    from the latest on-disk refresh token so rotating refresh tokens do not get
    lost when multiple processes share the same host.
    """

    def __init__(
        self,
        path: str,
        *,
        refresh_skew_seconds: int = 120,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.refresh_skew_seconds = refresh_skew_seconds
        self.timeout_seconds = timeout_seconds
        self._process_lock = asyncio.Lock()

    def _read(self) -> ChatGPTCredentialRecord:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InferenceAuthenticationError(
                "chatgpt",
                f"ChatGPT credential file is missing: {self.path}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise InferenceAuthenticationError(
                "chatgpt",
                "ChatGPT credential file is unreadable",
            ) from exc

        try:
            scopes_raw = raw.get("scopes", raw.get("scope", []))
            if isinstance(scopes_raw, str):
                scopes = frozenset(item for item in scopes_raw.split() if item)
            else:
                scopes = frozenset(str(item) for item in scopes_raw)
            saved_at = datetime.fromisoformat(str(raw["saved_at"]).replace("Z", "+00:00"))
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            record = ChatGPTCredentialRecord(
                client_id=str(raw["client_id"]),
                access_token=str(raw["access_token"]),
                refresh_token=str(raw["refresh_token"]),
                scopes=scopes,
                saved_at=saved_at.astimezone(timezone.utc),
                expires_in=int(raw.get("expires_in", 3600)),
                id_token=str(raw["id_token"]) if raw.get("id_token") else None,
                email=str(raw["email"]) if raw.get("email") else None,
                subject=str(raw["subject"]) if raw.get("subject") else None,
                ext_agent_host_id=(
                    str(raw["ext_agent_host_id"])
                    if raw.get("ext_agent_host_id")
                    else None
                ),
                earliest_refresh_at=raw.get("earliest_refresh_at"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceAuthenticationError(
                "chatgpt",
                "ChatGPT credential file is incomplete",
            ) from exc

        if not record.client_id or not record.access_token or not record.refresh_token:
            raise InferenceAuthenticationError("chatgpt", "ChatGPT credentials are incomplete")
        if _REQUIRED_SCOPE not in record.scopes:
            raise InferenceAuthenticationError(
                "chatgpt",
                "ChatGPT plan usage was not authorized for this connection",
            )
        return record

    def _write(self, previous: ChatGPTCredentialRecord, payload: dict[str, Any]) -> ChatGPTCredentialRecord:
        scopes_raw = payload.get("scope", " ".join(sorted(previous.scopes)))
        scopes = (
            frozenset(item for item in scopes_raw.split() if item)
            if isinstance(scopes_raw, str)
            else previous.scopes
        )
        if _REQUIRED_SCOPE not in scopes:
            raise InferenceAuthenticationError(
                "chatgpt",
                "refreshed ChatGPT token lost plan-usage permission",
            )
        now = datetime.now(timezone.utc)
        document: dict[str, Any] = {
            "issuer": "https://auth.openai.com",
            "client_id": previous.client_id,
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token") or previous.refresh_token),
            "id_token": str(payload.get("id_token") or previous.id_token or ""),
            "token_type": str(payload.get("token_type") or "Bearer"),
            "expires_in": int(payload.get("expires_in", 3600)),
            "earliest_refresh_at": payload.get(
                "earliest_refresh_at",
                previous.earliest_refresh_at,
            ),
            "scopes": sorted(scopes),
            "plan_usage_enabled": True,
            "saved_at": now.isoformat(),
        }
        for key, value in {
            "email": previous.email,
            "subject": previous.subject,
            "ext_agent_host_id": previous.ext_agent_host_id,
        }.items():
            if value:
                document[key] = value

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)
        return self._read()

    def _clear_unusable_tokens(self, record: ChatGPTCredentialRecord) -> None:
        """Drop unusable bearer credentials while retaining the reusable registration mapping."""
        document: dict[str, Any] = {
            "issuer": "https://auth.openai.com",
            "client_id": record.client_id,
            "plan_usage_enabled": False,
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
        }
        for key, value in {
            "email": record.email,
            "subject": record.subject,
            "ext_agent_host_id": record.ext_agent_host_id,
        }.items():
            if value:
                document[key] = value

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    @staticmethod
    def _response_error_code(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "oauth_error"
        if not isinstance(payload, dict):
            return "oauth_error"
        return str(payload.get("error") or payload.get("code") or "oauth_error")

    @staticmethod
    def _needs_refresh(record: ChatGPTCredentialRecord, skew_seconds: int) -> bool:
        remaining = record.expires_at.timestamp() - datetime.now(timezone.utc).timestamp()
        return remaining <= skew_seconds

    async def access_token(self) -> str:
        record = self._read()
        if not self._needs_refresh(record, self.refresh_skew_seconds):
            return record.access_token

        async with self._process_lock:
            async with _HostFileLock(self.lock_path):
                record = self._read()
                if not self._needs_refresh(record, self.refresh_skew_seconds):
                    return record.access_token
                refreshed = await self._refresh(record)
                return refreshed.access_token

    async def _refresh(self, record: ChatGPTCredentialRecord) -> ChatGPTCredentialRecord:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    _TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": record.client_id,
                        "refresh_token": record.refresh_token,
                        "resource": _RESOURCE,
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "chatgpt",
                f"ChatGPT token refresh failed: {exc.__class__.__name__}",
            ) from exc

        error_code = self._response_error_code(response) if response.status_code >= 400 else ""
        if error_code in _TERMINAL_REFRESH_CODES:
            self._clear_unusable_tokens(record)
            raise InferenceAuthenticationError(
                "chatgpt",
                f"ChatGPT refresh token is no longer usable ({error_code}); sign in again with the saved client registration",
            )
        if error_code == "invalid_client":
            raise InferenceConfigurationError(
                "chatgpt",
                "ChatGPT refresh rejected the issued client registration (invalid_client)",
            )
        if response.status_code in {400, 401, 403}:
            raise InferenceAuthenticationError(
                "chatgpt",
                f"ChatGPT token refresh was rejected with HTTP {response.status_code}: {error_code}",
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                "chatgpt",
                f"ChatGPT token refresh returned HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            raise ProviderUnavailable(
                "chatgpt",
                f"ChatGPT token refresh returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("access_token"):
                raise ValueError("missing access token")
            return self._write(record, payload)
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderUnavailable(
                "chatgpt",
                "ChatGPT token refresh returned an invalid response",
            ) from exc
