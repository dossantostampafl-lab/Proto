"""SIMULATED credential transfer and real local callback HTTP; no OpenAI account."""

from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

import httpx
import pytest

from services.chatgpt.chatgpt_connect import _CallbackHandler, _profile_credentials_path
from services.chatgpt.storage import read_private, save_private
from services.chatgpt.vm import import_session, prepare


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = tmp_path / "transfer" / "credentials.json"
    save_private(
        path,
        {
            "application": "proto",
            "issuer": "https://auth.openai.com",
            "subject": "fixture-user",
            "client_id": "fixture-client",
            "access_token": "fixture-access",
            "refresh_token": "fixture-refresh",
            "id_token": "fixture-id",
            "scopes": ["chatgpt.tokens.use.direct"],
            "saved_at": "2026-01-01T00:00:00+00:00",
            "expires_in": 3600,
            "ext_agent_host_id": "urn:uuid:laptop",
        },
    )
    return path


def test_vm_import_preserves_vm_identity_and_credentials(source):
    host_id = prepare("default")
    import_session("default", source)
    saved = read_private(_profile_credentials_path())
    assert saved["ext_agent_host_id"] == host_id != "urn:uuid:laptop"
    assert saved["refresh_token"] == "fixture-refresh"
    assert saved["id_token"] == "fixture-id"
    assert prepare("default") == host_id
    with pytest.raises(ValueError, match="Disconnect"):
        import_session("default", source)


def test_import_refuses_other_project_and_identity(source):
    incoming = read_private(source)
    save_private(source, {**incoming, "application": "the-creation-os"})
    with pytest.raises(ValueError, match="PROTO"):
        import_session("default", source)
    save_private(source, incoming)
    save_private(
        _profile_credentials_path(),
        {
            "application": "proto",
            "client_id": "other",
            "subject": "someone-else",
        },
    )
    with pytest.raises(ValueError, match="another identity"):
        import_session("default", source)


def test_import_refuses_unprotected_transfer(source):
    source.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        import_session("default", source)


def test_callback_rejects_invalid_state_duplicates_and_replay():
    handler = type(
        "TestCallback",
        (_CallbackHandler,),
        {
            "event": Event(),
            "result": None,
            "expected_state": "expected",
            "result_lock": Lock(),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/auth/callback"
        with httpx.Client(trust_env=False) as client:
            assert client.get(url, params={"state": "wrong", "code": "bad"}).status_code == 400
            assert client.get(url + "?state=expected&state=expected&code=bad").status_code == 400
            assert not handler.event.is_set()
            assert client.get(url, params={"state": "expected", "code": "good"}).status_code == 200
            assert handler.result == {"state": "expected", "code": "good"}
            assert (
                client.get(url, params={"state": "expected", "code": "replay"}).status_code == 409
            )
            assert handler.result["code"] == "good"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
