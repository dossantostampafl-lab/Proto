from apps.api.app.live_monitor import LiveCryptoMonitor, configured_public_market_adapter
from apps.api.app.settings import settings
from services.market_data import BinancePublicMarketDataAdapter, CoinbasePublicMarketDataAdapter


def test_configured_public_market_adapter_selects_binance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "live_market_source", "BINANCE")

    adapter = configured_public_market_adapter()

    assert isinstance(adapter, BinancePublicMarketDataAdapter)
    assert adapter.symbols == ("BTC", "ETH", "SOL")


def test_configured_public_market_adapter_selects_coinbase(monkeypatch) -> None:
    monkeypatch.setattr(settings, "live_market_source", "COINBASE")

    assert isinstance(configured_public_market_adapter(), CoinbasePublicMarketDataAdapter)


def test_live_monitor_reports_actual_adapter_provider() -> None:
    monitor = LiveCryptoMonitor(adapter=BinancePublicMarketDataAdapter())

    assert monitor.status()["provider"] == "BINANCE"
    assert monitor.source_health()["provider"] == "BINANCE"
    assert monitor.status()["financial_connectivity"] is False
    assert monitor.status()["real_money_execution"] is False
