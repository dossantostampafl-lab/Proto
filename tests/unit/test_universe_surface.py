from apps.api.app import universe_surface
from apps.api.app.settings import Settings


def test_core_universe_is_explicit_and_non_financial(monkeypatch) -> None:
    monkeypatch.setattr(universe_surface, "settings", Settings())
    payload = universe_surface.universe()
    ids = {item["instrument_id"] for item in payload["instruments"]}
    assert ids == {"CRYPTO:BTC", "CRYPTO:ETH", "CRYPTO:SOL"}
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


def test_configured_equities_expand_catalog_without_claiming_live_coverage(monkeypatch) -> None:
    configured = Settings(
        alpaca_equity_symbols="AAPL, NVDA,AAPL",
        brapi_equity_symbols="PETR4,VALE3",
    )
    monkeypatch.setattr(universe_surface, "settings", configured)
    payload = universe_surface.universe()
    by_id = {item["instrument_id"]: item for item in payload["instruments"]}
    assert {"US:AAPL", "US:NVDA", "B3:PETR4", "B3:VALE3"}.issubset(by_id)
    assert by_id["US:AAPL"]["coverage"]["read_only_market_data"] is False
    assert by_id["US:AAPL"]["coverage"]["execution_connected"] is False
    assert by_id["B3:PETR4"]["coverage"]["execution_connected"] is False
