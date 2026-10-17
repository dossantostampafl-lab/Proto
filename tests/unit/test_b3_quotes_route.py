"""GET /v1/b3/quotes: a read-only view of the B3 quotes the API holds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_xp_routing import XP_OFF, XP_ON, configure

from apps.api.app import b3_quotes, main
from services.execution.venue import Quote


@pytest.fixture(autouse=True)
def _clean():
    b3_quotes.install(None)
    yield
    b3_quotes.install(None)


def feed_with(**quotes: Quote) -> b3_quotes.B3QuoteFeed:
    feed = b3_quotes.B3QuoteFeed(fetch=None, symbols=list(quotes))
    feed._quotes.update(quotes)
    b3_quotes.install(feed)
    return feed


def test_without_xp_routing_the_route_says_so_and_invents_nothing(monkeypatch) -> None:
    configure(monkeypatch, **XP_OFF)
    body = TestClient(main.app).get("/v1/b3/quotes").json()
    assert body["enabled"] is False and "symbols" not in body
    assert body["real_money_execution"] is False and body["financial_connectivity"] is False


def test_it_reports_each_quote_with_its_own_timestamp_and_age(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    at = datetime.now(UTC) - timedelta(seconds=90)
    feed_with(PETR4F=Quote(bid=Decimal("38.10"), ask=Decimal("38.12"), observed_at=at))
    body = TestClient(main.app).get("/v1/b3/quotes").json()
    assert body["enabled"] is True and set(body["symbols"]) == {"PETR4F", "VALE3F", "BOVA11F"}
    petr = body["symbols"]["PETR4F"]
    assert petr["bid"] == "38.10" and petr["ask"] == "38.12"
    assert petr["observed_at"] == at.isoformat()
    assert 89 <= petr["age_seconds"] <= 120  # the age of the terminal's quote, not of the poll
    assert body["symbols"]["VALE3F"] is None and body["symbols"]["BOVA11F"] is None


def test_without_a_feed_every_symbol_is_null_and_the_feed_is_not_running(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    body = TestClient(main.app).get("/v1/b3/quotes").json()
    assert body["enabled"] is True and body["feed_running"] is False
    assert all(value is None for value in body["symbols"].values())


def test_a_future_timestamp_never_gives_a_negative_age(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    at = datetime.now(UTC) + timedelta(seconds=30)
    feed_with(PETR4F=Quote(bid=Decimal("38.10"), ask=Decimal("38.12"), observed_at=at))
    body = TestClient(main.app).get("/v1/b3/quotes").json()
    assert body["symbols"]["PETR4F"]["age_seconds"] == 0.0


def test_the_route_is_read_only(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    client = TestClient(main.app)
    assert client.post("/v1/b3/quotes").status_code in (401, 405)
    assert client.put("/v1/b3/quotes").status_code in (401, 405)
