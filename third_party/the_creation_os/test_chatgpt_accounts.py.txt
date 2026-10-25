from __future__ import annotations

import json
from pathlib import Path

from app.inference.chatgpt_accounts import _clear_local_tokens


def test_local_signout_drops_all_bearer_credentials_but_retains_registration(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    data = {
        "issuer": "https://auth.openai.com",
        "client_id": "oaiapp_issued",
        "email": "creator@example.com",
        "subject": "subject-1",
        "ext_agent_host_id": "urn:uuid:11111111-1111-4111-8111-111111111111",
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "id_token": "id-secret",
        "scopes": ["openid", "chatgpt.tokens.use.direct"],
        "expires_in": 3600,
    }

    _clear_local_tokens(path, data, revocation_confirmed=False)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["client_id"] == "oaiapp_issued"
    assert stored["email"] == "creator@example.com"
    assert stored["subject"] == "subject-1"
    assert stored["remote_revocation_confirmed"] is False
    assert stored["plan_usage_enabled"] is False
    assert "access_token" not in stored
    assert "refresh_token" not in stored
    assert "id_token" not in stored
    assert "scopes" not in stored
