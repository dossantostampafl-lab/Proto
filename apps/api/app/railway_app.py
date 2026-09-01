from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from .live_routes import router as live_router
from .main import app

# Railway runs the research/simulation API and the public read-only BTC/ETH/SOL
# monitor in the same process. The live router owns the monitor lifespan and uses
# the existing WebSocket hub from ``main`` to publish market-data/orderbook
# frames. No account credentials or financial connectivity are introduced.
app.include_router(live_router)

_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"

if _DASHBOARD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
