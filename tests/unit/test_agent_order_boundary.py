"""Agents, strategies and research produce opinions and intents only: no order path.

Fusion, the reviewer, the roster, the signal adapters, the strategy lab, the analytics and the
isolated research worker must never reach a venue, the OMS, the executor or the Risk Engine's
permit signer themselves. Orders exist only after the API's Risk Engine issues a permit and the
OMS consumes it (AGENTS.md, financial execution boundary).
"""

from __future__ import annotations

import ast
from pathlib import Path

SCANNED = (
    "services/agents",
    "services/signals",
    "services/research",
    "services/analytics",
    "apps/research_worker",
)
FORBIDDEN = (
    "services.venues",
    "services.oms",
    "services.risk",
    "apps.executor",
    "apps.api",
)


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_agents_strategies_and_research_never_import_an_order_path() -> None:
    files = [path for root in SCANNED for path in sorted(Path(root).rglob("*.py"))]
    assert files, "the scanned packages moved: update SCANNED"
    violations = [
        f"{path}: {module}"
        for path in files
        for module in sorted(_imports(path))
        if any(module == f or module.startswith(f"{f}.") for f in FORBIDDEN)
    ]
    assert violations == []
