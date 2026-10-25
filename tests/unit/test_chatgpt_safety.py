"""Offline OAuth/HTTP fixtures only; no real account authorization or inference."""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from services.chatgpt.chatgpt_provider import ChatGPTPlanProvider
from services.chatgpt.contracts import (
    InferenceRateLimitError,
    InferenceRequest,
    ProviderUnavailable,
)
from services.chatgpt.storage import read_private, save_private


class FakeCredentials:
    async def access_token(self):
        return "fixture-token"


def test_original_sources_match_recorded_git_blobs():
    root = Path(__file__).resolve().parents[2] / "third_party/the_creation_os"
    for path, expected in json.loads((root / "original-blobs.json").read_text()).items():
        raw = (root / (Path(path).name + ".txt")).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected


def test_private_storage_rejects_git_and_broad_permissions(tmp_path):
    path = tmp_path / "private" / "credentials.json"
    save_private(path, {"access_token": "fixture-token"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert read_private(path)["access_token"] == "fixture-token"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_private(path)
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError):
        save_private(path, {})


def test_private_storage_rejects_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("unchanged")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        save_private(link, {})
    assert target.read_text() == "unchanged"


def provider():
    return ChatGPTPlanProvider(credentials=FakeCredentials(), default_model="fixture-model")


def test_no_tools_or_paid_fallback_or_private_endpoint():
    request = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        metadata={"enable_capability_intents": True},
    )
    payload = provider()._payload(request)
    assert "tools" not in payload
    assert payload["store"] is False
    assert payload["stream"] is True
    with pytest.raises(ValueError):
        ChatGPTPlanProvider(
            credentials=FakeCredentials(),
            default_model="fixture-model",
            base_url="https://example.com",
        )


@pytest.mark.parametrize(
    "terminal,expected",
    [
        ({"type": "response.completed", "response": {"status": "completed"}}, None),
        ({"type": "response.incomplete"}, ProviderUnavailable),
        (
            {
                "type": "response.failed",
                "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
            },
            InferenceRateLimitError,
        ),
        ({"type": "unrelated"}, ProviderUnavailable),
    ],
)
async def test_only_complete_streams_succeed(monkeypatch, terminal, expected):
    events = [{"type": "response.output_text.delta", "delta": "fixture answer"}, terminal]
    body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)

    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        return httpx.Response(200, text=body)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    request = InferenceRequest(messages=[{"role": "user", "content": "hello"}])
    if expected:
        with pytest.raises(expected):
            await provider().generate(request)
    else:
        assert (await provider().generate(request)).content == "fixture answer"


async def test_context_budget_precedes_network():
    request = InferenceRequest(messages=[{"role": "user", "content": "x" * 32001}])
    with pytest.raises(ValueError):
        await provider().generate(request)


async def test_cancelled_lock_wait_does_not_hold_lock(tmp_path):
    from services.chatgpt.chatgpt_credentials import _HostFileLock

    path = tmp_path / "credentials.lock"
    async with _HostFileLock(path):
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                async with _HostFileLock(path):
                    pytest.fail("second holder must wait")
    async with asyncio.timeout(1):
        async with _HostFileLock(path):
            pass


async def test_refresh_rotation_serializes_and_terminal_failure_clears_tokens(
    tmp_path, monkeypatch
):
    from services.chatgpt.chatgpt_credentials import ChatGPTCredentialStore
    from services.chatgpt.contracts import InferenceAuthenticationError

    path = tmp_path / "profile" / "credentials.json"
    record = {
        "application": "proto",
        "issuer": "https://auth.openai.com",
        "client_id": "test",
        "subject": "user",
        "access_token": "old",
        "refresh_token": "refresh",
        "scopes": ["chatgpt.tokens.use.direct"],
        "expires_in": 1,
        "saved_at": "2020-01-01T00:00:00+00:00",
    }
    save_private(path, record)
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(
            200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(response), **kw)
    )
    one, two = ChatGPTCredentialStore(str(path)), ChatGPTCredentialStore(str(path))
    assert await asyncio.gather(one.access_token(), two.access_token()) == ["new", "new"]
    assert len(calls) == 1
    assert read_private(path)["refresh_token"] == "rotated"
    save_private(path, record)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(400, json={"error": "refresh_token_reused"})
            ),
            **kw,
        ),
    )
    with pytest.raises(InferenceAuthenticationError):
        await one.access_token()
    assert "refresh_token" not in read_private(path)
    assert "access_token" not in read_private(path)
