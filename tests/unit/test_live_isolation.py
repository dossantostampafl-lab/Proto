import ast
from pathlib import Path

LIVE_MONITOR = Path("apps/api/app/live_monitor.py")
PUBLIC_FEED = Path("services/market_data/live.py")

_FORBIDDEN_LIVE_IMPORT_PREFIXES = (
    "apps.api.app.portfolio",
    "apps.api.app.risk_state",
    "apps.api.app.simulation",
    "services.hedge",
    "services.quant",
)
_FORBIDDEN_CONNECTIVITY_TOKENS = (
    "api_key",
    "api_secret",
    "client_secret",
    "passphrase",
    "private_key",
    "place_order",
    "submit_order",
    "/accounts",
    "/orders",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_live_monitor_does_not_import_financial_or_prediction_engines() -> None:
    modules = _imports(LIVE_MONITOR)

    violations = sorted(
        module
        for module in modules
        if module.startswith(_FORBIDDEN_LIVE_IMPORT_PREFIXES)
    )

    assert violations == []


def test_public_feed_contains_no_account_or_order_connectivity_contracts() -> None:
    source = PUBLIC_FEED.read_text(encoding="utf-8").lower()

    violations = [token for token in _FORBIDDEN_CONNECTIVITY_TOKENS if token in source]

    assert violations == []
