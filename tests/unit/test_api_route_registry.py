from pathlib import Path


def test_live_router_has_single_application_owner() -> None:
    main = Path("apps/api/app/main.py").read_text(encoding="utf-8")
    research = Path("apps/api/app/research.py").read_text(encoding="utf-8")

    assert "from .live_routes import router as live_router" in main
    assert main.count("    live_router,\n") == 1
    assert "from .live_routes import router as live_router" not in research
    assert "router.include_router(live_router)" not in research
