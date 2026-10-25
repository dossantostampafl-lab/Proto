from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from app.inference import chatgpt_connect
from app.inference.chatgpt_connect import (
    ISSUER,
    _default_credentials_path,
    _granted_scopes,
    _inside_git_checkout,
    _load_or_create_host_id,
    _validate_id_token,
)


def test_default_chatgpt_credentials_path_stays_outside_checkout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    path = _default_credentials_path()

    assert path == tmp_path / ".config" / "the-creation-os" / "chatgpt" / "profiles" / "default" / "credentials.json"
    assert path.is_absolute()


def test_chatgpt_host_id_is_persisted_before_credentials_exist(tmp_path: Path) -> None:
    output = tmp_path / "credentials.json"

    first = _load_or_create_host_id(output, {})
    second = _load_or_create_host_id(output, {})

    assert first.startswith("urn:uuid:")
    assert second == first
    assert (tmp_path / "host-id").read_text(encoding="utf-8").strip() == first


def test_saved_remote_profile_does_not_overwrite_the_browser_hosts_identity(tmp_path: Path) -> None:
    output = tmp_path / "credentials.json"
    local_host_id = _load_or_create_host_id(output, {})
    remote_host_id = "urn:uuid:11111111-1111-4111-8111-111111111111"
    credentials = {"ext_agent_host_id": remote_host_id}

    selected_host_id = _load_or_create_host_id(output, credentials)

    assert selected_host_id == remote_host_id
    assert (tmp_path / "host-id").read_text(encoding="utf-8").strip() == local_host_id


def test_plan_permission_comes_only_from_token_response_scopes() -> None:
    assert _granted_scopes({"scope": "openid profile chatgpt.tokens.use.direct"}) == {
        "openid",
        "profile",
        "chatgpt.tokens.use.direct",
    }

    with pytest.raises(RuntimeError, match="did not return granted scopes"):
        _granted_scopes({})


def test_credentials_destination_inside_git_checkout_is_rejected_by_guard(tmp_path: Path) -> None:
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / ".git").mkdir()

    assert _inside_git_checkout(checkout / "credentials.json")
    assert not _inside_git_checkout(tmp_path / "outside" / "credentials.json")


def _signed_id_token(monkeypatch, *, access_token: str) -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    public_jwk = {**jwk.construct(public_pem, "RS256").to_dict(), "kid": "test-key"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [public_jwk]}))
    real_client = httpx.Client
    monkeypatch.setattr(
        chatgpt_connect.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": "oaiapp_test",
            "sub": "user-123",
            "nonce": "nonce-1",
            "iat": now,
            "exp": now + 600,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key"},
        access_token=access_token,
    )


def test_id_token_with_at_hash_is_validated_against_the_access_token(monkeypatch) -> None:
    id_token = _signed_id_token(monkeypatch, access_token="access-1")

    claims = _validate_id_token(
        id_token, client_id="oaiapp_test", nonce="nonce-1", access_token="access-1"
    )

    assert claims["sub"] == "user-123"
    assert "at_hash" in claims


def test_id_token_bound_to_another_access_token_is_rejected(monkeypatch) -> None:
    id_token = _signed_id_token(monkeypatch, access_token="access-1")

    with pytest.raises(jwt.JWTClaimsError):
        _validate_id_token(
            id_token, client_id="oaiapp_test", nonce="nonce-1", access_token="other-access"
        )
