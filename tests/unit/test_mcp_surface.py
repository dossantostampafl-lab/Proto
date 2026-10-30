from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.app import finrobot_surface, main, mcp_surface, risk_surface
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings

OPERATOR = "o" * 32
MCP = "m" * 32
AUTH = {"Authorization": f"Bearer {MCP}"}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    settings = Settings(
        operator_api_token=OPERATOR, mcp_enabled=True, mcp_api_token=MCP, finrobots_enabled=True
    )
    for module in (main, mcp_surface, finrobot_surface, risk_surface):
        monkeypatch.setattr(module, "settings", settings)
    finrobot_surface.reset_finrobot_state()
    risk_surface.reset_risk_state()
    mcp_surface.rate_limiter.reset()
    yield settings
    finrobot_surface.reset_finrobot_state()
    risk_surface.reset_risk_state()


@pytest.fixture
def client():
    return TestClient(main.app)


def rpc(client, method, params=None, *, headers=AUTH, request_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def test_closed_when_disabled(client, configured, monkeypatch):
    monkeypatch.setattr(
        mcp_surface, "settings", configured.model_copy(update={"mcp_enabled": False})
    )
    assert rpc(client, "tools/list").status_code == 404


def test_requires_its_own_bearer_token(client):
    assert rpc(client, "tools/list", headers={}).status_code == 401
    assert (
        rpc(client, "tools/list", headers={"Authorization": f"Bearer {OPERATOR}"}).status_code
        == 401
    )
    # The operator header is not a substitute either, and the operator middleware does not
    # stand in front of /mcp (ChatGPT cannot send it).
    response = rpc(client, "tools/list", headers={"X-Proto-Operator-Token": OPERATOR})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_foreign_browser_origin_is_refused(client):
    headers = {**AUTH, "Origin": "https://evil.example"}
    assert rpc(client, "tools/list", headers=headers).status_code == 403
    headers = {**AUTH, "Origin": "https://chatgpt.com"}
    assert rpc(client, "tools/list", headers=headers).status_code == 200


def test_initialize_negotiates_version_and_advertises_tools_only(client):
    result = rpc(client, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    unknown = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert unknown["protocolVersion"] == mcp_surface.LATEST_PROTOCOL_VERSION
    assert rpc(client, "notifications/initialized", request_id=None).status_code == 202
    bad = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={**AUTH, "MCP-Protocol-Version": "2000-01-01"},
    )
    assert bad.status_code == 400


def test_every_tool_is_read_only_and_strict(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == set(mcp_surface.TOOLS)
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["inputSchema"]["additionalProperties"] is False
    # Nothing that sounds like it could act is exposed.
    forbidden = ("order", "kill", "permit", "release", "submit", "evaluate", "trade", "run")
    assert not [n for n in names if any(word in n for word in forbidden)]


def test_tool_call_returns_labeled_data_and_is_journaled(client):
    response = rpc(client, "tools/call", {"name": "finrobots_list", "arguments": {}})
    result = response.json()["result"]
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["financial_connectivity"] is False and data["real_money_execution"] is False
    assert data["authority"] == "ADVISORY_ONLY" and len(data["robots"]) == 8
    assert "never instructions" in result["structuredContent"]["notice"]
    event = operational_journal.journal.recent(1)[0]
    assert event.event_type == "MCP_TOOL_CALLED"
    assert event.payload["tool"] == "finrobots_list" and event.payload["ok"] is True


def test_tool_arguments_are_validated(client):
    unknown = rpc(client, "tools/call", {"name": "finrobot_cycles", "arguments": {"limit": 99}})
    assert unknown.json()["result"]["isError"] is True
    extra = rpc(
        client,
        "tools/call",
        {
            "name": "agent_decisions_recent",
            "arguments": {"instrument_id": "x; DROP", "cmd": "sell"},
        },
    )
    assert extra.json()["result"]["isError"] is True
    missing = rpc(client, "tools/call", {"name": "place_order", "arguments": {}})
    assert missing.json()["error"]["code"] == -32602
    assert rpc(client, "resources/list").json()["error"]["code"] == -32601


def test_status_and_risk_tools_answer(client):
    for name in (
        "proto_status",
        "risk_status",
        "finrobot_council_latest",
        "agent_decisions_recent",
    ):
        result = rpc(client, "tools/call", {"name": name}).json()["result"]
        assert result["isError"] is False, name
    council = rpc(client, "tools/call", {"name": "finrobot_council_latest"}).json()["result"]
    assert council["structuredContent"]["data"]["data_mode"] == "UNAVAILABLE"


def test_malformed_and_batched_requests_are_refused(client):
    assert client.post("/mcp", content=b"{", headers=AUTH).status_code == 400
    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=AUTH)
    assert batch.status_code == 400
    assert client.get("/mcp", headers=AUTH).status_code == 405


def test_finrobot_routes(client):
    view = client.get("/v1/finrobots").json()
    assert view["bound"] == 0 and view["authority"] == "ADVISORY_ONLY"
    assert client.post("/v1/finrobots/cycles").status_code == 401
    ran = client.post("/v1/finrobots/cycles", headers={"X-Proto-Operator-Token": OPERATOR})
    assert ran.status_code == 200
    assert ran.json()["cycle"]["reports"] == []
    assert client.get("/v1/finrobots/cycles").json()["cycles"][0]["reports"] == []
    assert client.get("/v1/finrobots/council/latest").json()["data_mode"] == "PAPER"


def test_cycle_refused_when_disabled(client, configured, monkeypatch):
    monkeypatch.setattr(
        finrobot_surface, "settings", configured.model_copy(update={"finrobots_enabled": False})
    )
    ran = client.post("/v1/finrobots/cycles", headers={"X-Proto-Operator-Token": OPERATOR})
    assert ran.status_code == 409


def test_mcp_token_must_be_its_own():
    with pytest.raises(ValueError):
        Settings(operator_api_token=OPERATOR, mcp_api_token=OPERATOR)
