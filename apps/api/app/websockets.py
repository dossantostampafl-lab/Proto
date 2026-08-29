from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class WebSocketHub:
    def __init__(
        self,
        *,
        max_connections_per_channel: int = 128,
        max_message_chars: int = 1_024,
        send_timeout_seconds: float = 1.0,
        allowed_origins: frozenset[str] | None = None,
    ) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._max_connections_per_channel = max_connections_per_channel
        self._max_message_chars = max_message_chars
        self._send_timeout_seconds = send_timeout_seconds
        self._allowed_origins = allowed_origins or frozenset(
            {"http://localhost:5173", "http://127.0.0.1:5173"}
        )
        self._broadcast_count = 0
        self._send_failures = 0
        self._origin_rejections = 0
        self._capacity_rejections = 0
        self._oversized_messages = 0

    async def connect(self, channel: str, websocket: WebSocket) -> bool:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            self._origin_rejections += 1
            await websocket.close(code=1008, reason="origin not allowed")
            return False
        if self.connection_count(channel) >= self._max_connections_per_channel:
            self._capacity_rejections += 1
            await websocket.close(code=1013, reason="channel capacity reached")
            return False
        await websocket.accept()
        self._connections[channel].add(websocket)
        return True

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        connections = self._connections.get(channel)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(channel, None)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        self._broadcast_count += 1
        connections = tuple(self._connections.get(channel, ()))
        if not connections:
            return
        results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    websocket.send_json(payload),
                    timeout=self._send_timeout_seconds,
                )
                for websocket in connections
            ),
            return_exceptions=True,
        )
        for websocket, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                self._send_failures += 1
                self.disconnect(channel, websocket)

    async def serve(self, channel: str, websocket: WebSocket) -> None:
        if not await self.connect(channel, websocket):
            return
        try:
            await websocket.send_json({"type": "subscribed", "channel": channel})
            while True:
                message = await websocket.receive_text()
                if len(message) > self._max_message_chars:
                    self._oversized_messages += 1
                    await websocket.close(code=1009, reason="message too large")
                    return
                if message == "ping":
                    await websocket.send_json({"type": "pong", "channel": channel})
        except WebSocketDisconnect:
            pass
        finally:
            self.disconnect(channel, websocket)

    def connection_count(self, channel: str) -> int:
        return len(self._connections.get(channel, ()))

    def snapshot(self) -> dict[str, object]:
        return {
            "connections": {
                channel: len(connections)
                for channel, connections in sorted(self._connections.items())
            },
            "total_connections": sum(len(items) for items in self._connections.values()),
            "broadcast_count": self._broadcast_count,
            "send_failures": self._send_failures,
            "origin_rejections": self._origin_rejections,
            "capacity_rejections": self._capacity_rejections,
            "oversized_messages": self._oversized_messages,
        }


hub = WebSocketHub()
