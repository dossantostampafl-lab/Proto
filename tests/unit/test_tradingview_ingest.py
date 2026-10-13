from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from services.execution import SignalDirection, SignalSource
from services.signals.tradingview import (
    TRADINGVIEW_WEBHOOK_IPS,
    IngestOutcome,
    SignalRejected,
    TradingViewIngestor,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
TV_IP = sorted(TRADINGVIEW_WEBHOOK_IPS)[0]


def ingestor(**overrides: object) -> TradingViewIngestor:
    values: dict[str, object] = {
        "secret": SECRET,
        "allowed_instruments": frozenset({"BINANCE_SPOT:BTCBRL"}),
        "allowed_ips": TRADINGVIEW_WEBHOOK_IPS,
        "max_age": timedelta(seconds=30),
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return TradingViewIngestor(**values)  # type: ignore[arg-type]


def body(**overrides: object) -> bytes:
    values: dict[str, object] = {
        "secret": SECRET,
        "alert_id": "bar-1728820800000",
        "strategy_id": "tv-ema-cross",
        "instrument_id": "BINANCE_SPOT:BTCBRL",
        "direction": "LONG",
        "strength": 0.8,
        "timeframe": "15m",
        "fired_at": (NOW - timedelta(seconds=2)).isoformat(),
    }
    values.update(overrides)
    return json.dumps({k: v for k, v in values.items() if v is not None}).encode()


def rejected(code: str, status: int, call) -> None:
    with pytest.raises(SignalRejected) as error:
        call()
    assert error.value.code == code
    assert error.value.status_code == status


def test_valid_alert_becomes_a_tradingview_signal_without_order_authority() -> None:
    result = ingestor().ingest(body(), client_ip=TV_IP)
    assert result.outcome is IngestOutcome.ACCEPTED
    signal = result.signal
    assert signal.source is SignalSource.TRADINGVIEW
    assert signal.direction is SignalDirection.LONG
    assert signal.strength == 0.8
    assert signal.observed_at == NOW - timedelta(seconds=2)
    assert signal.received_at == NOW
    assert "secret" not in signal.model_dump_json()


def test_epoch_millisecond_timestamps_from_pine_are_accepted() -> None:
    fired_ms = int((NOW - timedelta(seconds=1)).timestamp() * 1000)
    result = ingestor().ingest(body(fired_at=fired_ms), client_ip=TV_IP)
    assert result.signal.observed_at == NOW - timedelta(seconds=1)


def test_strength_defaults_to_full_conviction() -> None:
    result = ingestor().ingest(body(strength=None), client_ip=TV_IP)
    assert result.signal.strength == 1.0


def test_duplicate_alert_returns_the_same_signal_and_is_flagged() -> None:
    tv = ingestor()
    first = tv.ingest(body(), client_ip=TV_IP)
    second = tv.ingest(body(), client_ip=TV_IP)
    assert second.outcome is IngestOutcome.DUPLICATE
    assert second.signal == first.signal


def test_same_alert_id_from_another_strategy_is_a_different_signal() -> None:
    tv = ingestor()
    first = tv.ingest(body(), client_ip=TV_IP)
    other = tv.ingest(body(strategy_id="tv-rsi"), client_ip=TV_IP)
    assert other.outcome is IngestOutcome.ACCEPTED
    assert other.signal.signal_id != first.signal.signal_id


def test_signal_id_is_deterministic_across_restarts() -> None:
    first = ingestor().ingest(body(), client_ip=TV_IP)
    again = ingestor().ingest(body(), client_ip=TV_IP)
    assert again.outcome is IngestOutcome.ACCEPTED
    assert again.signal.signal_id == first.signal.signal_id


def test_dedupe_memory_is_bounded() -> None:
    tv = ingestor(dedupe_capacity=2)
    tv.ingest(body(alert_id="a"), client_ip=TV_IP)
    tv.ingest(body(alert_id="b"), client_ip=TV_IP)
    tv.ingest(body(alert_id="c"), client_ip=TV_IP)
    assert tv.ingest(body(alert_id="a"), client_ip=TV_IP).outcome is IngestOutcome.ACCEPTED
    assert tv.ingest(body(alert_id="c"), client_ip=TV_IP).outcome is IngestOutcome.DUPLICATE


def test_unlisted_source_ip_is_forbidden_before_reading_the_body() -> None:
    rejected("ip_not_allowed", 403, lambda: ingestor().ingest(b"not json", client_ip="1.2.3.4"))


def test_ip_allowlist_can_be_disabled_for_local_testing() -> None:
    result = ingestor(allowed_ips=None).ingest(body(), client_ip="127.0.0.1")
    assert result.outcome is IngestOutcome.ACCEPTED


def test_wrong_or_missing_secret_is_unauthorized() -> None:
    rejected("unauthorized", 401, lambda: ingestor().ingest(body(secret="x" * 32), TV_IP))
    rejected("unauthorized", 401, lambda: ingestor().ingest(body(secret=None), TV_IP))
    rejected("unauthorized", 401, lambda: ingestor().ingest(body(secret=123), TV_IP))


def test_secret_is_checked_before_schema_details_are_revealed() -> None:
    rejected(
        "unauthorized",
        401,
        lambda: ingestor().ingest(body(secret="x" * 32, direction="UP"), TV_IP),
    )


def test_malformed_and_oversized_bodies_are_rejected() -> None:
    rejected("invalid_json", 400, lambda: ingestor().ingest(b"{oops", TV_IP))
    rejected("invalid_json", 400, lambda: ingestor().ingest(b"[1, 2]", TV_IP))
    rejected("payload_too_large", 413, lambda: ingestor().ingest(b" " * 4097, TV_IP))


def test_schema_violations_are_rejected() -> None:
    rejected("invalid_alert", 422, lambda: ingestor().ingest(body(direction="UP"), TV_IP))
    rejected("invalid_alert", 422, lambda: ingestor().ingest(body(strength=2), TV_IP))
    rejected("invalid_alert", 422, lambda: ingestor().ingest(body(unexpected="x"), TV_IP))
    rejected("invalid_alert", 422, lambda: ingestor().ingest(body(alert_id=""), TV_IP))


def test_naive_timestamps_are_rejected() -> None:
    naive = (NOW - timedelta(seconds=1)).replace(tzinfo=None).isoformat()
    rejected("invalid_alert", 422, lambda: ingestor().ingest(body(fired_at=naive), TV_IP))


def test_stale_and_future_alerts_are_rejected() -> None:
    stale = (NOW - timedelta(seconds=31)).isoformat()
    future = (NOW + timedelta(seconds=6)).isoformat()
    rejected("stale_alert", 422, lambda: ingestor().ingest(body(fired_at=stale), TV_IP))
    rejected("future_alert", 422, lambda: ingestor().ingest(body(fired_at=future), TV_IP))
    small_skew = (NOW + timedelta(seconds=4)).isoformat()
    assert ingestor().ingest(body(fired_at=small_skew), TV_IP).signal.received_at == NOW + (
        timedelta(seconds=4)
    )


def test_instrument_must_be_allowlisted() -> None:
    rejected(
        "instrument_not_allowed",
        422,
        lambda: ingestor().ingest(body(instrument_id="BINANCE_SPOT:DOGEBRL"), TV_IP),
    )


def test_rejected_alerts_are_not_remembered_for_dedupe() -> None:
    tv = ingestor()
    stale = (NOW - timedelta(seconds=31)).isoformat()
    with pytest.raises(SignalRejected):
        tv.ingest(body(fired_at=stale), TV_IP)
    assert tv.ingest(body(), TV_IP).outcome is IngestOutcome.ACCEPTED


def test_ingestor_refuses_weak_secrets() -> None:
    with pytest.raises(ValueError, match="32"):
        ingestor(secret="short")
