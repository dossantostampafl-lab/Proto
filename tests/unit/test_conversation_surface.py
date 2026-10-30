"""Real HTTP + SQLite integration with SIMULATED account, roster and inference."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import conversation_surface as surface
from services.chatgpt.conversations import ConversationStore, metadata


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/chat.db")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    monkeypatch.setattr(
        surface,
        "settings",
        SimpleNamespace(
            chatgpt_local_enabled=True,
            chatgpt_profile="default",
            chatgpt_model="fixture-model",
            operator_api_token="operator",
            chatgpt_public_access=False,
            public_origin=None,
        ),
    )
    monkeypatch.setattr(surface, "store", lambda: ConversationStore(engine))
    monkeypatch.setattr(
        surface.ChatGPTCredentialStore,
        "_read",
        lambda _: SimpleNamespace(subject="user-1", client_id="client-1"),
    )
    agent = SimpleNamespace(
        slot=SimpleNamespace(instrument_id="BTCUSDT"),
        to_dict=lambda: {"agent_id": "AG-1", "status": "ACTIVE"},
    )

    async def ready():
        return SimpleNamespace(get=lambda identifier: agent if identifier == "AG-1" else None)

    def evidence(**kwargs):
        return {"decisions": [], "instrument_id": kwargs["instrument_id"]}

    async def journal(**kwargs):
        assert "text" not in kwargs["payload"]

    async def generate(_, request):
        assert "no tools" in request.messages[0]["content"].lower()
        return SimpleNamespace(content="No recorded decision is available.", model="fixture-model")

    monkeypatch.setattr(surface, "ready", ready)
    monkeypatch.setattr(surface, "recent_decisions", evidence)
    monkeypatch.setattr(surface, "record_operational_event", journal)
    monkeypatch.setattr(surface.ChatGPTPlanProvider, "generate", generate)
    app = FastAPI()
    app.include_router(surface.router)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost",
        headers={"X-Proto-Operator-Token": "operator"},
    ) as c:
        yield c
    await engine.dispose()


async def test_explanation_and_history_are_persisted_and_owner_scoped(client, monkeypatch):
    created = await client.post("/conversation/new", json={"agent_id": "AG-1"})
    assert created.status_code == 200
    identifier = created.json()["id"]
    path = f"/conversation/{identifier}"
    sent = await client.post(path + "/messages", json={"text": "Explain", "version": 0})
    assert sent.status_code == 200, sent.text
    assert sent.json()["messages"][1]["origin"] == "CHATGPT_EXPLANATION"
    history = await client.post(path + "/history")
    assert history.json()["version"] == 1
    assert len(history.json()["messages"]) == 2
    conflict = await client.post(path + "/messages", json={"text": "Again", "version": 0})
    assert conflict.status_code == 409
    monkeypatch.setattr(
        surface.ChatGPTCredentialStore,
        "_read",
        lambda _: SimpleNamespace(subject="user-2", client_id="client-1"),
    )
    assert (await client.post(path + "/history")).status_code == 404


async def test_auth_origin_disabled_and_secret_fail_closed(client):
    assert (
        await client.post("/conversation/status", headers={"X-Proto-Operator-Token": "wrong"})
    ).status_code == 401
    assert (
        await client.post("/conversation/status", headers={"Origin": "https://attacker.example"})
    ).status_code == 403
    assert (
        await client.post("/conversation/status", headers={"Host": "attacker.example"})
    ).status_code == 403
    created = await client.post("/conversation/new", json={"agent_id": "AG-1"})
    path = f"/conversation/{created.json()['id']}"
    assert (
        await client.post(path + "/messages", json={"text": "password=private", "version": 0})
    ).status_code == 422
    assert (await client.post(path + "/history")).json()["messages"] == []
    surface.settings.chatgpt_local_enabled = False
    assert (await client.post("/conversation/status")).status_code == 503


async def test_failed_inference_does_not_persist_fabricated_reply(client, monkeypatch):
    async def fail(*args):
        raise TimeoutError()

    monkeypatch.setattr(surface.ChatGPTPlanProvider, "generate", fail)
    created = await client.post("/conversation/new", json={"agent_id": "AG-1"})
    path = f"/conversation/{created.json()['id']}"
    assert (
        await client.post(path + "/messages", json={"text": "Explain", "version": 0})
    ).status_code == 502
    assert (await client.post(path + "/history")).json()["messages"] == []


async def test_non_loopback_denied(client):
    app = FastAPI()
    app.include_router(surface.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1234)),
        base_url="http://localhost",
    ) as remote:
        assert (await remote.post("/conversation/status")).status_code == 403


async def test_setup_status_is_not_claimed_as_real_authorization(client, monkeypatch):
    from services.chatgpt.contracts import InferenceAuthenticationError

    response = await client.post("/conversation/status")
    assert response.json()["state"] == "CREDENTIALS_PRESENT_UNVERIFIED"
    assert "access_token" not in response.text

    def missing(_):
        raise InferenceAuthenticationError("chatgpt", "missing")

    monkeypatch.setattr(surface.ChatGPTCredentialStore, "_read", missing)
    response = await client.post("/conversation/status")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["state"] == "AUTHORIZATION_REQUIRED"
    assert (await client.post("/conversation/models")).status_code == 401


async def test_model_catalog_and_disconnect_are_explicit(client, monkeypatch):
    async def models(profile):
        assert profile == "default"
        return ["fixture-model"]

    monkeypatch.setattr(surface, "models", models)
    assert (await client.post("/conversation/models")).json() == {
        "models": ["fixture-model"],
        "state": "ACCOUNT_ACCESS_VERIFIED",
    }
    monkeypatch.setattr(surface, "sign_out", lambda profile: False)
    assert (await client.post("/conversation/disconnect")).json() == {
        "connected": False,
        "remote_revocation_confirmed": False,
    }
    await surface._gate.acquire()
    try:
        assert (await client.post("/conversation/disconnect")).status_code == 409
    finally:
        surface._gate.release()


async def test_proxy_headers_and_cross_scheme_are_denied(client):
    for headers in ({"X-Forwarded-For": "127.0.0.1"}, {"Origin": "https://localhost"}):
        for path in ("status", "models", "disconnect", "new"):
            assert (
                await client.post(
                    f"/conversation/{path}", headers=headers, json={"agent_id": "AG-1"}
                )
            ).status_code == 403


async def test_selected_model_is_used_without_changing_server_configuration(client, monkeypatch):
    seen = []
    original = surface.ChatGPTPlanProvider.__init__

    def capture(self, **kwargs):
        seen.append(kwargs["default_model"])
        original(self, **kwargs)

    monkeypatch.setattr(surface.ChatGPTPlanProvider, "__init__", capture)
    created = await client.post("/conversation/new", json={"agent_id": "AG-1"})
    response = await client.post(
        f"/conversation/{created.json()['id']}/messages",
        json={
            "text": "Explain",
            "version": 0,
            "model": "fixture-other",
        },
    )
    assert response.status_code == 200
    assert response.json()["agent_id"] == "AG-1"
    assert seen == ["fixture-other"]
    assert surface.settings.chatgpt_model == "fixture-model"


def test_public_access_needs_the_operator_token_and_the_public_origin(monkeypatch) -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    from apps.api.app import conversation_surface as surface

    def request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/conversation/status",
                "headers": raw,
                "client": ("203.0.113.9", 4000),
                "server": ("155-181-40-136.sslip.io", 443),
                "scheme": "https",
                "query_string": b"",
            }
        )

    configured = SimpleNamespace(
        chatgpt_local_enabled=True,
        chatgpt_public_access=True,
        public_origin="https://155-181-40-136.sslip.io",
        operator_api_token="operator",
    )
    monkeypatch.setattr(surface, "settings", configured)
    good = {"origin": "https://155-181-40-136.sslip.io", "x-forwarded-for": "203.0.113.9"}
    surface.authorize_operator(request({**good, "X-Proto-Operator-Token": "operator"}))
    for headers, code in (
        (good, 401),
        ({**good, "X-Proto-Operator-Token": "wrong"}, 401),
        ({"origin": "https://evil.example", "X-Proto-Operator-Token": "operator"}, 403),
    ):
        with pytest.raises(HTTPException) as refused:
            surface.authorize_operator(request(headers))
        assert refused.value.status_code == code
    configured.public_origin = None
    with pytest.raises(HTTPException) as unset:
        surface.authorize_operator(request({**good, "X-Proto-Operator-Token": "operator"}))
    assert unset.value.status_code == 503
    # Off (the default): a public client is refused even with the token.
    configured.chatgpt_public_access = False
    configured.public_origin = "https://155-181-40-136.sslip.io"
    with pytest.raises(HTTPException) as loopback:
        surface.authorize_operator(request({**good, "X-Proto-Operator-Token": "operator"}))
    assert loopback.value.status_code == 403
