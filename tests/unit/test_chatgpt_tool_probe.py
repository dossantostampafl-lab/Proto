"""The ChatGPT tool probe: one read-only price tool, offline fixtures only."""

from __future__ import annotations

import json

from services.chatgpt import tool_probe
from services.chatgpt.chatgpt_provider import ChatGPTPlanProvider
from services.chatgpt.contracts import InferenceRequest, InferenceUpstreamResponseError


class FakeCredentials:
    async def access_token(self):
        return "fixture-token"


CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "get_quote",
    "namespace": "proto",
    "arguments": json.dumps({"symbol": "btcusdt"}),
}


def quote(symbol):
    return {"symbol": symbol, "mid": "100"} if symbol == "BTCUSDT" else None


async def test_the_tool_is_offered_in_a_namespace_and_only_to_the_probe() -> None:
    request = InferenceRequest(messages=[{"role": "user", "content": "hi"}])
    probe_provider = tool_probe._ToolProvider(
        tools=tool_probe.SHAPES["namespace"], credentials=FakeCredentials(), default_model="m"
    )
    payload = probe_provider._payload(request)
    assert payload["tools"][0]["type"] == "namespace"
    assert [t["name"] for t in payload["tools"][0]["tools"]] == ["get_quote"]
    assert payload["store"] is False and payload["stream"] is True
    # The explanation chat's provider stays tool-free.
    plain = ChatGPTPlanProvider(credentials=FakeCredentials(), default_model="m")
    assert "tools" not in plain._payload(request)


async def test_a_called_tool_runs_locally_and_its_result_goes_back(monkeypatch) -> None:
    turns = []

    async def turn(self, request):
        turns.append(request.messages)
        if len(turns) == 1:
            return [CALL], ""
        return [], "O BTC está em 100."

    monkeypatch.setattr(tool_probe._ToolProvider, "turn", turn)
    result = await tool_probe.probe(credentials=FakeCredentials(), model="m", quote=quote)
    assert result["tools_supported"] is True and result["working_shape"] == "namespace"
    attempt = result["attempts"][0]
    assert attempt["tool_result"] == {"symbol": "BTCUSDT", "mid": "100"}
    assert attempt["answer"] == "O BTC está em 100."
    replay, output = turns[1][-2:]
    assert "id" not in replay and replay["call_id"] == "call_1"  # store=false: no item ids
    assert output == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps({"symbol": "BTCUSDT", "mid": "100"}),
    }


async def test_a_refused_namespace_falls_back_to_a_plain_function(monkeypatch) -> None:
    shapes = []

    async def turn(self, request):
        shapes.append(self._tools[0]["type"])
        if self._tools[0]["type"] == "namespace":
            raise InferenceUpstreamResponseError("chatgpt", "unsupported")
        has_output = any(m.get("type") == "function_call_output" for m in request.messages)
        return ([], "ok") if has_output else ([CALL], "")

    monkeypatch.setattr(tool_probe._ToolProvider, "turn", turn)
    result = await tool_probe.probe(credentials=FakeCredentials(), model="m", quote=quote)
    assert result["working_shape"] == "function"
    assert result["attempts"][0]["shape"] == "namespace" and "error" in result["attempts"][0]
    assert shapes == ["namespace", "function", "function"]


async def test_no_tool_call_is_reported_as_unsupported(monkeypatch) -> None:
    async def turn(self, request):
        return [], "Não posso usar ferramentas."

    monkeypatch.setattr(tool_probe._ToolProvider, "turn", turn)
    result = await tool_probe.probe(credentials=FakeCredentials(), model="m", quote=quote)
    assert result == {
        "tools_supported": False,
        "working_shape": None,
        "attempts": [
            {"shape": "namespace", "tool_called": False, "answer": "Não posso usar ferramentas."},
            {"shape": "function", "tool_called": False, "answer": "Não posso usar ferramentas."},
        ],
    }
