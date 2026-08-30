from dataclasses import FrozenInstanceError

import pytest

import services.market_data as market_data
import services.market_data.live as live_module
from services.market_data.live_contracts import PublicFeedHealth, PublicMarketDataAdapter


def test_public_feed_health_has_one_canonical_contract_definition() -> None:
    assert live_module.PublicFeedHealth is PublicFeedHealth
    assert market_data.PublicFeedHealth is PublicFeedHealth


def test_public_market_adapter_protocol_has_one_canonical_contract_definition() -> None:
    assert live_module.PublicMarketDataAdapter is PublicMarketDataAdapter
    assert market_data.PublicMarketDataAdapter is PublicMarketDataAdapter


def test_public_feed_health_remains_immutable() -> None:
    health = PublicFeedHealth(
        connected=False,
        connection_generation=0,
        connection_attempts=0,
        reconnect_count=0,
        frames_received=0,
        ticks_emitted=0,
        parse_error_count=0,
        connected_since=None,
        last_message_at=None,
        last_tick_at=None,
        last_error=None,
    )

    with pytest.raises(FrozenInstanceError):
        health.connected = True
