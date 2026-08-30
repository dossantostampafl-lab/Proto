from __future__ import annotations

import ast
from pathlib import Path

from apps.api.app import app_state, surface

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
