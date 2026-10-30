"""Probe: does the operator's ChatGPT plan (Sign in with ChatGPT) run a function tool?

One read-only tool, ``proto.get_quote`` (the live Binance mid PROTO already streams), offered in
a namespace (the shape the Sign in with ChatGPT preview documents) and, if that shape is refused,
as a plain function tool. The model's call is executed locally, its result is sent back, and the
final answer is returned with which shape worked. Nothing here can trade: the only tool reads a
price. The explanation chat keeps its tool-free provider (ChatGPTPlanProvider).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from services.chatgpt.chatgpt_provider import ChatGPTPlanProvider
from services.chatgpt.contracts import InferenceError, InferenceRequest

QUOTE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "get_quote",
    "description": "Live bid, ask and mid of a crypto pair PROTO streams from Binance (read-only).",
    "parameters": {
        "type": "object",
        "properties": {"symbol": {"type": "string", "description": "e.g. BTCUSDT"}},
        "required": ["symbol"],
        "additionalProperties": False,
    },
}
NAMESPACE = {
    "type": "namespace",
    "name": "proto",
    "description": "Read-only PROTO market data. No trading.",
    "tools": [QUOTE_TOOL],
}
SHAPES: dict[str, list[dict[str, Any]]] = {"namespace": [NAMESPACE], "function": [QUOTE_TOOL]}
QUESTION = (
    "Use the get_quote tool for BTCUSDT, then answer in Portuguese in one sentence: "
    "what is the current BTC mid price?"
)


class _ToolProvider(ChatGPTPlanProvider):
    """The plan provider with tools attached, for this probe only."""

    def __init__(self, *, tools: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tools = tools

    def _payload(self, request: InferenceRequest) -> dict[str, Any]:
        payload = super()._payload(request)
        payload["tools"] = self._tools
        return payload

    async def turn(self, request: InferenceRequest) -> tuple[list[dict[str, Any]], str]:
        """One streamed turn: the output items (function calls among them) and the text."""

        items: list[dict[str, Any]] = []
        text: list[str] = []
        async for event in self._events(request):
            kind = event.get("type")
            if kind == "response.output_item.done" and isinstance(event.get("item"), dict):
                items.append(event["item"])
            elif kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
                text.append(event["delta"])
        return items, "".join(text)


async def probe(
    *, credentials: Any, model: str, quote: Callable[[str], dict[str, Any] | None]
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for shape, tools in SHAPES.items():
        provider = _ToolProvider(tools=tools, credentials=credentials, default_model=model)
        messages: list[dict[str, Any]] = [{"role": "user", "content": QUESTION}]
        try:
            items, text = await provider.turn(InferenceRequest(messages=messages))
            calls = [i for i in items if i.get("type") == "function_call"]
            if not calls:
                attempts.append({"shape": shape, "tool_called": False, "answer": text[:500]})
                continue
            call = calls[0]
            try:
                arguments = json.loads(call.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            symbol = str(arguments.get("symbol", "")).upper()
            result = quote(symbol) or {"error": f"no live quote for {symbol}"}
            # With store=false nothing is kept server-side: replay the call without its item id.
            kept = ("type", "call_id", "name", "arguments", "namespace")
            replay = {k: call[k] for k in kept if k in call}
            followup = (
                messages
                + [replay]
                + [
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": json.dumps(result),
                    }
                ]
            )
            _, answer = await provider.turn(InferenceRequest(messages=followup))
            attempts.append(
                {
                    "shape": shape,
                    "tool_called": True,
                    "tool": call.get("name"),
                    "namespace": call.get("namespace"),
                    "arguments": arguments,
                    "tool_result": result,
                    "answer": answer[:500],
                }
            )
            return {"tools_supported": True, "working_shape": shape, "attempts": attempts}
        except InferenceError as exc:
            attempts.append({"shape": shape, "error": str(exc)[:300]})
    return {"tools_supported": False, "working_shape": None, "attempts": attempts}
