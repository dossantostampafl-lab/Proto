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


def test_finrobot_routes(client, monkeypatch):
    # The engines branch binds real specialists; pin the unbound case so this test does not
    # depend on which ones exist.
    monkeypatch.setattr(finrobot_surface, "_ROBOTS_MODULE", "services.finrobots.no_such_robots")
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


def test_oversized_body_is_refused_before_it_is_read(client):
    headers = {**AUTH, "Content-Length": str(mcp_surface._MAX_BODY_BYTES + 1)}
    response = client.post("/mcp", content=b"{}", headers=headers)
    assert response.status_code == 413


async def test_chunked_body_stops_being_read_at_the_limit():
    from starlette.requests import Request

    chunk = b"x" * 16_384
    pulled = 0

    async def receive():
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": chunk, "more_body": pulled < 1_000}

    request = Request({"type": "http", "method": "POST", "headers": []}, receive)
    assert await mcp_surface._bounded_body(request) is None
    # 64 KiB / 16 KiB: it stopped after the fifth chunk instead of draining all 1,000.
    assert pulled == 5


async def test_snapshot_labels_every_source():
    import asyncio

    async def hang():
        await asyncio.sleep(10)

    def boom():
        raise RuntimeError("down")

    readers = {
        "ok": (lambda: {"x": 1}, "PAPER"),
        "async.ok": (lambda: asyncio.sleep(0, result=[1]), "TESTNET"),
        "broken": (boom, "PAPER"),
        "slow": (hang, "PAPER"),
    }
    original = finrobot_surface._readers
    finrobot_surface._readers = lambda: readers
    finrobot_surface._SOURCE_TIMEOUT_SECONDS, saved = 0.05, finrobot_surface._SOURCE_TIMEOUT_SECONDS
    try:
        snapshot = await finrobot_surface.read_snapshot()
    finally:
        finrobot_surface._readers = original
        finrobot_surface._SOURCE_TIMEOUT_SECONDS = saved
    assert snapshot["ok"] == {"x": 1} and snapshot["async.ok"] == [1]
    assert "broken" not in snapshot and "slow" not in snapshot
    sources = snapshot["_sources"]["sources"]
    assert {k: v["status"] for k, v in sources.items()} == {
        "ok": "OK",
        "async.ok": "OK",
        "broken": "UNAVAILABLE",
        "slow": "TIMEOUT",
    }
    assert sources["async.ok"]["provenance"] == "TESTNET"
    assert sources["broken"]["provenance"] == "UNAVAILABLE"
    assert sources["broken"]["error"] == "RuntimeError"


async def test_real_snapshot_reads_proto_sources():
    snapshot = await finrobot_surface.read_snapshot()
    sources = snapshot["_sources"]["sources"]
    assert set(sources) == set(finrobot_surface._readers())
    assert sources["risk.status"]["status"] == "OK"
    assert sources["risk.status"]["provenance"] == "PAPER"
    assert snapshot["_sources"]["version"] == finrobot_surface.SNAPSHOT_VERSION
    # No live monitor in tests: every quote is None, so the source is not labeled LIVE.
    assert all(q is None for q in snapshot["market.quotes"].values())
    assert sources["market.quotes"]["provenance"] == "UNAVAILABLE"


def test_only_in_memory_sources_are_synchronous():
    import inspect
    from time import perf_counter

    for name, (read, _) in finrobot_surface._readers().items():
        started = perf_counter()
        value = read()
        elapsed = perf_counter() - started
        if name in finrobot_surface.IN_MEMORY_SOURCES:
            # Sync readers run on the loop without a timeout: they must be plain memory reads.
            assert not inspect.isawaitable(value), name
            assert elapsed < 0.05, (name, elapsed)
        else:
            # Anything else must be a coroutine, so the 3 s timeout bounds it.
            assert inspect.isawaitable(value), name
            value.close()


async def test_snapshot_changes_nothing_and_never_starts_the_paper_oms():
    from apps.api.app import oms_surface
    from apps.api.app.metrics_state import metrics

    oms_surface.reset_oms_state()
    try:
        before = dict(metrics.counters)
        snapshot = await finrobot_surface.read_snapshot()
        sources = snapshot["_sources"]["sources"]
        assert not oms_surface.paper_oms_started()
        assert sources["oms.orders"]["status"] == "UNAVAILABLE"
        assert sources["oms.orders"]["error"] == "SourceNotReady"
        assert sources["oms.balances"]["status"] == "UNAVAILABLE"
        assert sources["oms.balances"]["error"] == "SourceNotReady"
        assert not {"oms.ledger", "reconciliation.status", "promotion.scopes"} & set(sources)
        assert dict(metrics.counters) == before
        # Once something else has started the OMS, its orders are read (and nothing else is).
        await oms_surface.components()
        started = await finrobot_surface.read_snapshot()
        assert started["_sources"]["sources"]["oms.orders"]["status"] == "OK"
        assert started["oms.orders"]["execution_backend"] == "paper"
        balances = started["oms.balances"]
        assert balances["mode"] == "PAPER" and balances["real_money_execution"] is False
        assert "BINANCE_SPOT" in balances["accounts"]
        assert dict(metrics.counters) == before
    finally:
        oms_surface.reset_oms_state()


async def test_pure_balances_read_executor_ledgers_without_account_state(monkeypatch):
    from apps.api.app import oms_surface

    calls = []

    class Client:
        def __init__(self, venue):
            self.venue = venue

        async def ledger(self):
            calls.append(self.venue)
            if self.venue.value == "XP_B3":
                raise oms_surface.ExecutorUnavailable("down")
            return {"account_id": "acc", "balances": {"USDT": {"total": "10"}}}

    async def no_account_state(*_a, **_k):
        raise AssertionError("account_state must not run")

    monkeypatch.setattr(oms_surface, "executor_mode", lambda: True)
    monkeypatch.setattr(oms_surface, "xp_enabled", lambda: True)
    monkeypatch.setattr(oms_surface, "alpaca_enabled", lambda: False)
    monkeypatch.setattr(oms_surface, "executor_client", Client)
    monkeypatch.setattr(oms_surface, "account_state", no_account_state)
    result = await oms_surface.pure_balances()
    assert result["mode"] == "TESTNET"
    assert result["accounts"]["BINANCE_SPOT"]["balances"] == {"USDT": {"total": "10"}}
    assert result["accounts"]["XP_B3"] == {
        "account_id": "executor-xp-b3",
        "mode": "TESTNET",
        "unavailable": True,
    }
    assert len(calls) == 2


def _cycle_path_chatgpt_imports() -> list[str]:
    """Imports of ChatGPT, the conversation surface or the MCP server on the cycle path."""

    import ast
    from pathlib import Path

    forbidden = ("services.chatgpt", "apps.api.app.conversation_surface", "apps.api.app.mcp")
    files = [*Path("services/finrobots").rglob("*.py"), Path("apps/api/app/finrobot_surface.py")]
    found = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = "apps.api.app." if node.level == 1 else ""
                names = [f"{base}{node.module or ''}"] + [f"{base}{a.name}" for a in node.names]
            found += [f"{path}:{n}" for n in names if n.startswith(forbidden)]
    return found


async def test_finrobots_keep_running_with_chatgpt_unavailable(client, configured, monkeypatch):
    """ChatGPT is only a reader: with the connector closed and no ChatGPT code reachable, the
    autonomous cycles still run on PROTO's own schedule and record their verdicts."""

    from datetime import timedelta

    assert _cycle_path_chatgpt_imports() == []

    # Connector off (ChatGPT cannot reach PROTO) and no ChatGPT sign-in configured.
    closed = configured.model_copy(update={"mcp_enabled": False, "chatgpt_local_enabled": False})
    monkeypatch.setattr(mcp_surface, "settings", closed)
    assert rpc(client, "tools/list").status_code == 404

    orch = finrobot_surface.orchestrator()
    orch.start(timedelta(seconds=5))
    try:
        import asyncio

        for _ in range(200):
            if orch.history:
                break
            await asyncio.sleep(0.01)
        assert orch.history, "the scheduled cycle must run without ChatGPT"
        latest = orch.latest()
        assert latest.snapshot_sources and latest.errors == {}
    finally:
        await orch.stop()
