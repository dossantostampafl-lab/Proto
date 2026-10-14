"""Venue credentials and authenticated adapters stay inside the executor boundary."""

from __future__ import annotations

import ast
from pathlib import Path

# Only the isolated executor (Phase 5b) may import authenticated venue code.
ALLOWED_IMPORTERS = ("services/venues/", "apps/executor/")
SCANNED = ("apps", "services")


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _python_files():
    for root in SCANNED:
        for path in Path(root).rglob("*.py"):
            if not path.as_posix().startswith(ALLOWED_IMPORTERS):
                yield path


def test_only_the_executor_imports_venue_adapters_or_credentials() -> None:
    violations = [
        f"{path}:{module}"
        for path in _python_files()
        for module in _imports(path)
        if module == "services.venues" or module.startswith("services.venues.")
    ]
    assert violations == []


def test_credential_variables_are_read_only_by_the_credentials_module() -> None:
    violations = [
        str(path) for path in _python_files() if "PROTO_CRED_" in path.read_text(encoding="utf-8")
    ]
    assert violations == []
    sources = [p for p in Path("services/venues").rglob("*.py") if "PROTO_CRED_" in p.read_text()]
    assert [p.name for p in sources] == ["credentials.py"]


def test_venue_package_never_offers_money_movement() -> None:
    forbidden = ("withdraw", "/capital/", "transfer", "/futures", "/margin/")
    for path in Path("services/venues").rglob("*.py"):
        code = [
            node.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("/")
        ]
        hits = [route for route in code for token in forbidden if token in route.lower()]
        assert hits == [], f"{path}: {hits}"
