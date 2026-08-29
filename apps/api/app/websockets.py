from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[channel].add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        connections = self._connections.get(channel)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(channel, None)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in tuple(self._connections.get(channel, ())):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(channel, websocket)

    async def serve(self, channel: str, websocket: WebSocket) -> None:
        await self.connect(channel, websocket)
        try:
            await websocket.send_json({"type": "subscribed", "channel": channel})
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"type": "pong", "channel": channel})
        except WebSocketDisconnect:
            self.disconnect(channel, websocket)

    def connection_count(self, channel: str) -> int:
        return len(self._connections.get(channel, ()))


hub = WebSocketHub()
