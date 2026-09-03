from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from services.market_data import (
    AssetClass,
    Instrument,
    InstrumentRegistry,
    MarketEvent,
    MarketEventKind,
    MarketEventProvenance,
    SessionType,
)


def _instrument(
    instrument_id: str = "TEST:ABC",
    *,
    asset_class: AssetClass = AssetClass.EQUITY,
    venue: str = "TEST",
) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        symbol=instrument_id.split(":", 1)[1],
        name="Test instrument",
        asset_class=asset_class,
        venue=venue,
        currency="USD",
        country="US",
        timezone="America/New_York",
        market_calendar="TEST_CALENDAR",
        session_type=SessionType.EXCHANGE_SESSION,
    )


def test_registry_is_provider_neutral_and_conflict_safe() -> None:
    instrument = _instrument()
    registry = InstrumentRegistry((instrument,))
    assert registry.require("TEST:ABC") == instrument
    assert registry.snapshot()["financial_connectivity"] is False
    assert registry.snapshot()["real_money_execution"] is False

    conflicting = instrument.model_copy(update={"name": "Different contract"})
    with pytest.raises(ValueError, match="different contract"):
        registry.register(conflicting)


def test_registry_filters_without_claiming_provider_coverage() -> None:
    registry = InstrumentRegistry(
        (
            _instrument("TEST:AAA"),
            _instrument("CRYPTO:BTC", asset_class=AssetClass.CRYPTO, venue="CRYPTO"),
        )
    )
    equities = registry.list(asset_class=AssetClass.EQUITY)
    assert tuple(item.instrument_id for item in equities) == ("TEST:AAA",)


def test_instrument_rejects_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        Instrument(
            instrument_id="TEST:ABC",
            symbol="ABC",
            name="Test instrument",
            asset_class=AssetClass.EQUITY,
            venue="TEST",
            currency="USD",
            timezone="Not/A_Real_Timezone",
            market_calendar="TEST_CALENDAR",
            session_type=SessionType.EXCHANGE_SESSION,
        )


def test_universal_quote_event_validates_provenance_and_latency() -> None:
    observed = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
    event = MarketEvent(
        instrument_id="TEST:ABC",
        kind=MarketEventKind.QUOTE,
        observed_at=observed,
        received_at=observed + timedelta(milliseconds=25),
        source="TEST_READ_ONLY",
        provenance=MarketEventProvenance.PUBLIC_READ_ONLY,
        bid=99.0,
        ask=101.0,
        bid_size=10.0,
        ask_size=12.0,
    )
    assert event.mid == 100.0
    assert event.spread == 2.0
    assert event.source_latency_seconds == pytest.approx(0.025)


def test_quote_event_requires_real_quote_fields() -> None:
    now = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
    with pytest.raises(ValidationError, match="QUOTE events require bid and ask"):
        MarketEvent(
            instrument_id="TEST:ABC",
            kind=MarketEventKind.QUOTE,
            observed_at=now,
            received_at=now,
            source="TEST_READ_ONLY",
            provenance=MarketEventProvenance.PUBLIC_READ_ONLY,
            last=100.0,
        )
