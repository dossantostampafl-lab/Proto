from __future__ import annotations

from fastapi import APIRouter, WebSocket

from .websockets import hub

router = APIRouter(tags=["live-websocket"])


@router.websocket("/ws/market-data")
async def ws_live_market_data(websocket: WebSocket) -> None:
    await hub.serve("market-data", websocket)


@router.websocket("/ws/orderbook")
async def ws_live_orderbook(websocket: WebSocket) -> None:
    await hub.serve("orderbook", websocket)
