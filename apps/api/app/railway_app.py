from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .live_routes import router as live_router
from .main import app

# Railway runs the research/simulation API and the public read-only BTC/ETH/SOL
# monitor in the same process. The live router owns the monitor lifespan and uses
# the existing WebSocket hub from ``main`` to publish market-data/orderbook
# frames. No account credentials or financial connectivity are introduced.
app.include_router(live_router)


@app.middleware("http")
async def dashboard_cache_policy(request: Request, call_next) -> Response:
    """Keep the HTML shell fresh while allowing Vite fingerprinted assets to cache."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    elif path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"

if _DASHBOARD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
