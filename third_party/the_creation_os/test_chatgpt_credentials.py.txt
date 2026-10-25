from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.inference.chatgpt_credentials import ChatGPTCredentialRecord, ChatGPTCredentialStore


def _record() -> ChatGPTCredentialRecord:
    return ChatGPTCredentialRecord(
        client_id="oaiapp_test",
        access_token="access-secret",
        refresh_token="refresh-secret",
        scopes=frozenset({"openid", "chatgpt.tokens.use.direct"}),
        saved_at=datetime.now(timezone.utc),
        expires_in=3600,
        id_token="id-secret",
        email="creator@example.com",
        subject="subject-1",
        ext_agent_host_id="urn:uuid:11111111-1111-4111-8111-111111111111",
        earliest_refresh_at=1790033000,
    )


def test_terminal_refresh_cleanup_preserves_registration_but_drops_tokens(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = ChatGPTCredentialStore(str(path))

    store._clear_unusable_tokens(_record())

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["client_id"] == "oaiapp_test"
    assert payload["subject"] == "subject-1"
    assert payload["ext_agent_host_id"].startswith("urn:uuid:")
    assert payload["plan_usage_enabled"] is False
    assert "access_token" not in payload
    assert "refresh_token" not in payload
    assert "id_token" not in payload


def test_refresh_error_code_uses_machine_readable_oauth_error() -> None:
    import httpx

    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://auth.openai.com/api/accounts/oauth/token"),
        json={"error": "refresh_token_reused"},
    )

    assert ChatGPTCredentialStore._response_error_code(response) == "refresh_token_reused"


def test_refresh_write_preserves_production_issuer_and_plan_marker(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = ChatGPTCredentialStore(str(path))
    previous = _record()

    stored = store._write(
        previous,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "scope": "openid chatgpt.tokens.use.direct",
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issuer"] == "https://auth.openai.com"
    assert payload["plan_usage_enabled"] is True
    assert stored.access_token == "new-access"
