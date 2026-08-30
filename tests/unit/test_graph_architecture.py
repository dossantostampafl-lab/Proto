from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apps.api.app import app_state, main, safety_surface, surface

API_APP = Path("apps/api/app")


def _imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "." * node.level + module
            imports.add(module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


def test_analytics_surface_does_not_import_api_main() -> None:
    imports = _imports(API_APP / "surface.py")
    assert ".main" not in imports
    assert "apps.api.app.main" not in imports


def test_analytics_surface_uses_canonical_portfolio_state() -> None:
    assert surface.portfolio is app_state.portfolio


def test_api_main_uses_canonical_state_objects() -> None:
    assert main.runtime is app_state.runtime
    assert main.simulator is app_state.simulator
    assert main.portfolio is app_state.portfolio
    assert main.replay_session is app_state.replay_session
    assert main.persistence_engine is app_state.persistence_engine
    assert main.persistent_journal is app_state.persistent_journal


def test_safety_surface_shares_api_runtime() -> None:
    assert safety_surface.runtime is app_state.runtime
    assert safety_surface.runtime is main.runtime


@pytest.mark.asyncio
async def test_simulation_reset_preserves_canonical_runtime_identity() -> None:
    original_runtime = app_state.runtime
    original_runtime.running = True

    result = await main.simulation_reset()

    assert result is original_runtime
    assert main.runtime is original_runtime
    assert app_state.runtime is original_runtime
    assert safety_surface.runtime is original_runtime
    assert original_runtime.running is False
