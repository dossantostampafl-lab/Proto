from pathlib import Path


def test_live_monitor_delegates_payload_serialization() -> None:
    source = Path("apps/api/app/live_monitor.py").read_text(encoding="utf-8")

    assert "from .live_payloads import" in source
    assert "market_payload(" in source
    assert "orderbook_payload(" in source
    assert "def _market_payload(" not in source
    assert '"financial_connectivity": False' not in source
    assert '"real_money_execution": False' not in source
