from pathlib import Path


def test_live_router_has_single_application_owner_in_railway_composition() -> None:
    railway = Path("apps/api/app/railway_app.py").read_text(encoding="utf-8")
    research = Path("apps/api/app/research.py").read_text(encoding="utf-8")

    assert "from .live_routes import router as live_router" in railway
    assert railway.count("app.include_router(live_router)") == 1
    assert "from .live_routes import router as live_router" not in research
    assert "router.include_router(live_router)" not in research
