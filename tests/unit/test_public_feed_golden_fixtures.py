from pathlib import Path
from services.market_data.public_feed_parser import parse_public_ticker_message


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "public_feeds"


def test_coinbase_advanced_ticker_golden_fixture_parses_offline() -> None:
    payload = (FIXTURE_DIR / "coinbase_advanced_ticker.json").read_text(encoding="utf-8")

    ticks = parse_public_ticker_message(payload)

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.venue == "coinbase-public"
    assert tick.symbol == "BTC"
    assert tick.sequence == 0
    assert tick.last == 21932.98
    assert tick.bid == 21931.98
    assert tick.ask == 21933.98
    assert tick.bid_size == 8000.21
    assert tick.ask_size == 8038.07770938
    assert tick.volume == 16038.28770938
