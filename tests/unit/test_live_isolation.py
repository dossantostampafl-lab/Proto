import ast
from pathlib import Path

LIVE_SCOPE = (
    Path("apps/api/app/live_monitor.py"),
    Path("apps/api/app/live_routes.py"),
    Path("services/market_data/live.py"),
    Path("services/market_data/live_status.py"),
    Path("services/market_data/public_feed_parser.py"),
)
PUBLIC_FEED = Path("services/market_data/live.py")

_FORBIDDEN_LIVE_IMPORT_PREFIXES = (
    "apps.api.app.portfolio",
    "apps.api.app.risk_state",
    "apps.api.app.simulation",
    "services.execution",
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
    "create_order",
    "cancel_order",
    "/accounts",
    "/orders",
    "/withdraw",
    "/deposit",
    "authorization: bearer",
)
_FORBIDDEN_SECRET_ACCESS_TOKENS = (
    "os.getenv(",
    "os.environ",
    "environ[",
)


def _resolve_relative_import(path: Path, node: ast.ImportFrom) -> set[str]:
    package_parts = list(path.with_suffix("").parts[:-1])
    if node.level > 0:
        parent_levels = node.level - 1
        if parent_levels > len(package_parts):
            return set()
        base_parts = package_parts[: len(package_parts) - parent_levels]
    else:
        base_parts = []

    if node.module:
        module_parts = node.module.split(".")
        if node.level > 0:
            return {".".join([*base_parts, *module_parts])}
        return {node.module}

    if node.level > 0:
        return {
            ".".join([*base_parts, alias.name])
            for alias in node.names
            if alias.name != "*"
        }
    return set()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.update(_resolve_relative_import(path, node))
    return modules


def test_relative_import_resolution_is_fail_closed() -> None:
    node = ast.parse("from .simulation import PaperSimulator").body[0]
    assert isinstance(node, ast.ImportFrom)
    resolved = _resolve_relative_import(Path("apps/api/app/live_routes.py"), node)
    assert resolved == {"apps.api.app.simulation"}


def test_live_scope_does_not_import_financial_or_prediction_engines() -> None:
    violations: list[str] = []
    for path in LIVE_SCOPE:
        for module in _imports(path):
            if module.startswith(_FORBIDDEN_LIVE_IMPORT_PREFIXES):
                violations.append(f"{path}:{module}")

    assert sorted(violations) == []


def test_live_scope_contains_no_account_or_order_connectivity_contracts() -> None:
    violations: list[str] = []
    for path in LIVE_SCOPE:
        source = path.read_text(encoding="utf-8").lower()
        violations.extend(
            f"{path}:{token}"
            for token in _FORBIDDEN_CONNECTIVITY_TOKENS
            if token in source
        )

    assert violations == []


def test_live_scope_does_not_read_credentials_from_environment() -> None:
    violations: list[str] = []
    for path in LIVE_SCOPE:
        source = path.read_text(encoding="utf-8").lower()
        violations.extend(
            f"{path}:{token}"
            for token in _FORBIDDEN_SECRET_ACCESS_TOKENS
            if token in source
        )

    assert violations == []


def test_public_feed_is_pinned_to_canonical_tls_websocket() -> None:
    source = PUBLIC_FEED.read_text(encoding="utf-8")

    assert '"wss://advanced-trade-ws.coinbase.com"' in source
    assert '"ws://advanced-trade-ws.coinbase.com"' not in source
