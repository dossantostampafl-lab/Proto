from pathlib import Path


SOURCE = Path("apps/api/app/paper_autopilot.py").read_text(encoding="utf-8")


def test_autopilot_requires_current_fresh_public_feed_before_submit() -> None:
    assert 'status.get("source_message_fresh") is True' in SOURCE
    assert 'health.get("receipt_fresh") is True' in SOURCE
    assert 'health.get("current_connection") is True' in SOURCE
    assert 'self._last_reason = "LIVE_DATA_BECAME_STALE"' in SOURCE
    assert SOURCE.count("self._live_market_ready(config.symbol)") >= 2
    assert 'status.get("financial_connectivity") is False' in SOURCE
    assert 'status.get("real_money_execution") is False' in SOURCE
