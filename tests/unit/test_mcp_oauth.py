from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from apps.api.app import finrobot_surface, main, mcp_oauth, mcp_surface, risk_surface
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings

OPERATOR = "o" * 32
MCP = "m" * 32
ORIGIN = "https://proto.example"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    settings = Settings(
        operator_api_token=OPERATOR, mcp_enabled=True, mcp_api_token=MCP, public_origin=ORIGIN
    )
    for module in (main, mcp_surface, mcp_oauth, finrobot_surface, risk_surface):
        monkeypatch.setattr(module, "settings", settings)
    mcp_oauth.reset_oauth_state()
    mcp_surface.rate_limiter.reset()
    finrobot_surface.reset_finrobot_state()
    yield settings
    mcp_oauth.reset_oauth_state()
    finrobot_surface.reset_finrobot_state()


@pytest.fixture
def client():
    return TestClient(main.app, follow_redirects=False)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def register(client, uris=(CALLBACK,)) -> str:
    response = client.post("/oauth/register", json={"redirect_uris": list(uris)})
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


def authorize_params(client_id: str, challenge: str, **extra) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "proto.read",
        "resource": f"{ORIGIN}/mcp",
        **extra,
    }


def approve(client, params, token=MCP, decision="approve"):
    return client.post(
        "/oauth/authorize", data={**params, "approval_token": token, "decision": decision}
    )


def code_from(response) -> str:
    assert response.status_code == 302, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


def exchange(client, client_id, code, verifier):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        },
    )


def mcp_call(client, token):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_discovery_points_chatgpt_at_this_server(client):
    unauthorized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert unauthorized.status_code == 401
    challenge = unauthorized.headers["WWW-Authenticate"]
    assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource"' in challenge
    resource = client.get("/.well-known/oauth-protected-resource").json()
    assert resource["resource"] == f"{ORIGIN}/mcp"
    assert resource["authorization_servers"] == [ORIGIN]
    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert server["token_endpoint_auth_methods_supported"] == ["none"]
    assert server["registration_endpoint"] == f"{ORIGIN}/oauth/register"


@pytest.mark.parametrize(
    "update",
    [{"mcp_enabled": False}, {"mcp_api_token": None}, {"public_origin": "http://proto.example"}],
)
def test_closed_without_its_preconditions(client, configured, monkeypatch, update):
    monkeypatch.setattr(mcp_oauth, "settings", configured.model_copy(update=update))
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 404


def test_full_flow_grants_read_only_access_and_refresh_rotates(client):
    client_id = register(client)
    verifier, challenge = pkce()
    params = authorize_params(client_id, challenge)
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200 and "somente leitura" in page.text
    assert (
        "form-action 'self' https://chat.openai.com https://chatgpt.com"
        in (page.headers["Content-Security-Policy"])
    )
    code = code_from(approve(client, params))
    tokens = exchange(client, client_id, code, verifier).json()
    assert tokens["token_type"] == "Bearer" and tokens["scope"] == "proto.read"
    listed = mcp_call(client, tokens["access_token"])
    assert listed.status_code == 200
    assert all(t["annotations"]["readOnlyHint"] for t in listed.json()["result"]["tools"])
    # The access token is not an operator token.
    refused = client.post(
        "/v1/finrobots/cycles", headers={"X-Proto-Operator-Token": tokens["access_token"]}
    )
    assert refused.status_code == 401
    # Refresh rotates: the new pair works, the old refresh token is dead.
    refresh = {"grant_type": "refresh_token", "client_id": client_id}
    renewed = client.post(
        "/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]}
    )
    assert renewed.status_code == 200
    again = client.post("/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]})
    assert again.json()["error"] == "invalid_grant"
    events = [e.event_type for e in operational_journal.journal.recent(10)]
    assert "MCP_OAUTH_APPROVED" in events and "MCP_OAUTH_TOKEN_ISSUED" in events


def test_codes_are_single_use_and_bound_to_the_pkce_verifier(client):
    client_id = register(client)
    verifier, challenge = pkce()
    params = authorize_params(client_id, challenge)
    code = code_from(approve(client, params))
    assert exchange(client, client_id, code, "x" * 50).json()["error"] == "invalid_grant"
    # A failed attempt burns the code too.
    assert exchange(client, client_id, code, verifier).json()["error"] == "invalid_grant"
    code = code_from(approve(client, params))
    assert exchange(client, client_id, code, verifier).status_code == 200
    assert exchange(client, client_id, code, verifier).json()["error"] == "invalid_grant"


def test_expired_code_is_refused(client, monkeypatch):
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    later = mcp_oauth._now() + timedelta(minutes=5)
    monkeypatch.setattr(mcp_oauth, "_now", lambda: later)
    assert exchange(client, client_id, code, verifier).json()["error"] == "invalid_grant"


def test_wrong_approval_token_or_denial_never_issues_a_code(client):
    client_id = register(client)
    _, challenge = pkce()
    params = authorize_params(client_id, challenge)
    wrong = approve(client, params, token=OPERATOR)
    assert wrong.status_code == 200 and "Token incorreto" in wrong.text
    denied = approve(client, params, decision="deny")
    query = parse_qs(urlsplit(denied.headers["location"]).query)
    assert query["error"] == ["access_denied"] and "code" not in query
    # Brute force is throttled.
    for _ in range(6):
        last = approve(client, params, token="nope")
    assert "Muitas tentativas" in last.text


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/cb",
        "http://chatgpt.com/cb",
        "https://chatgpt.com.evil.example/cb",
        "https://user@chatgpt.com/cb",
        "https://chatgpt.com:8443/cb",
    ],
)
def test_registration_only_accepts_chatgpt_https_callbacks(client, uri):
    response = client.post("/oauth/register", json={"redirect_uris": [uri]})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_authorize_never_redirects_to_an_unregistered_uri(client):
    client_id = register(client)
    _, challenge = pkce()
    params = authorize_params(client_id, challenge, redirect_uri="https://chatgpt.com/other")
    response = client.get("/oauth/authorize", params=params)
    assert response.status_code == 400 and "location" not in response.headers
    unknown = client.get("/oauth/authorize", params=authorize_params("nope", challenge))
    assert unknown.status_code == 400


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("code_challenge_method", "plain", "invalid_request"),
        ("scope", "proto.read proto.write", "invalid_scope"),
        ("resource", "https://other.example/mcp", "invalid_target"),
        ("response_type", "token", "unsupported_response_type"),
    ],
)
def test_bad_authorization_requests_return_errors_to_the_client(client, field, value, error):
    client_id = register(client)
    _, challenge = pkce()
    response = client.get(
        "/oauth/authorize", params=authorize_params(client_id, challenge, **{field: value})
    )
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [error]


def test_operator_revocation_signs_every_connector_out(client):
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    access = exchange(client, client_id, code, verifier).json()["access_token"]
    assert mcp_call(client, access).status_code == 200
    assert client.post("/v1/mcp/oauth/revoke").status_code == 401  # operator token required
    revoked = client.post("/v1/mcp/oauth/revoke", headers={"X-Proto-Operator-Token": OPERATOR})
    assert revoked.json()["revoked"]["clients"] == 1
    assert mcp_call(client, access).status_code == 401
    assert client.get("/v1/mcp/oauth/status").json()["active_access_tokens"] == 0


def test_expired_access_token_is_refused(client, monkeypatch):
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    access = exchange(client, client_id, code, verifier).json()["access_token"]
    later = mcp_oauth._now() + timedelta(hours=2)
    monkeypatch.setattr(mcp_oauth, "_now", lambda: later)
    assert mcp_call(client, access).status_code == 401


def test_duplicated_and_oversized_forms_are_refused(client):
    client_id = register(client)
    body = f"grant_type=refresh_token&client_id={client_id}&client_id={client_id}"
    duplicated = client.post(
        "/oauth/token",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert duplicated.json()["error"] == "invalid_request"
    big = client.post(
        "/oauth/token",
        content=b"a=" + b"x" * 10_000,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert big.json()["error"] == "invalid_request"


def test_static_token_still_works(client):
    assert mcp_call(client, MCP).status_code == 200


@pytest.fixture
async def durable_store(tmp_path):
    """A control store on a file. configure_control_store also repoints the operational
    journal, so everything it touches is put back (other tests must not inherit a file
    journal in an UNVERIFIED state)."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from apps.api.app import control_state
    from services.events import EventJournal

    saved = (
        control_state.control_store,
        operational_journal.store,
        operational_journal.journal,
        operational_journal.integrity,
        operational_journal.corruption,
    )
    operational_journal.journal = EventJournal()  # configure_control_store resets it in place
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    store = control_state.configure_control_store(engine)
    try:
        yield store
    finally:
        (
            control_state.control_store,
            operational_journal.store,
            operational_journal.journal,
            operational_journal.integrity,
            operational_journal.corruption,
        ) = saved
        await engine.dispose()


async def _restart() -> None:
    """What a deploy does to this process: memory is gone, the control store remains."""

    mcp_oauth.reset_oauth_state()
    await mcp_oauth.restore_oauth()


async def test_a_restart_keeps_chatgpt_signed_in(client, durable_store):
    assert await durable_store.start() is True
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    tokens = exchange(client, client_id, code, verifier).json()
    await _restart()
    assert mcp_call(client, tokens["access_token"]).status_code == 200
    refresh = {"grant_type": "refresh_token", "client_id": client_id}
    renewed = client.post(
        "/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]}
    )
    assert renewed.status_code == 200
    # The rotation is durable too: after another restart the old refresh token stays dead.
    await _restart()
    again = client.post("/oauth/token", data={**refresh, "refresh_token": tokens["refresh_token"]})
    assert again.json()["error"] == "invalid_grant"
    assert mcp_call(client, renewed.json()["access_token"]).status_code == 200


async def test_only_digests_are_stored(client, durable_store):
    from services.risk import SqlControlState

    assert await durable_store.start() is True
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    tokens = exchange(client, client_id, code, verifier).json()
    saved = await SqlControlState(durable_store.engine).get(mcp_oauth.OAUTH_STATE_KEY)
    text = str(saved)
    assert tokens["access_token"] not in text and tokens["refresh_token"] not in text
    assert MCP not in text


async def test_rotating_the_mcp_token_or_revoking_signs_out_after_restart(
    client, configured, durable_store, monkeypatch
):
    assert await durable_store.start() is True
    client_id = register(client)
    verifier, challenge = pkce()
    code = code_from(approve(client, authorize_params(client_id, challenge)))
    access = exchange(client, client_id, code, verifier).json()["access_token"]
    rotated = configured.model_copy(update={"mcp_api_token": "n" * 32})
    for module in (mcp_oauth, mcp_surface):
        monkeypatch.setattr(module, "settings", rotated)
    await _restart()
    assert mcp_call(client, access).status_code == 401
    for module in (mcp_oauth, mcp_surface):
        monkeypatch.setattr(module, "settings", configured)
    await _restart()
    assert mcp_call(client, access).status_code == 200
    client.post("/v1/mcp/oauth/revoke", headers={"X-Proto-Operator-Token": OPERATOR})
    await _restart()
    assert mcp_call(client, access).status_code == 401
