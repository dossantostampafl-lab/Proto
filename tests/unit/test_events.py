from datetime import UTC, datetime
from uuid import uuid4

import pytest

from services.events.bus import InMemoryEventBus
from services.events.journal import EventJournal
from services.events.reconciliation import ReconciliationIssue, reconcile


@pytest.mark.asyncio
async def test_in_memory_bus_publish_subscribe_retry_and_dead_letter() -> None:
    bus = InMemoryEventBus()
    message_id = await bus.publish("signals", {"market": "btc", "state": "candidate"})
    messages = [message async for message in bus.subscribe("signals")]

    assert message_id == "1-0"
    assert messages[0].payload["market"] == "btc"
    assert await bus.retry("signals", {"market": "btc"}) == "2-0"
    assert await bus.dead_letter("signals", {"market": "btc"}) == "3-0"


def test_journal_hash_chain_and_idempotency_are_enforced() -> None:
    journal = EventJournal()
    correlation_id = uuid4()
    event_id = uuid4()
    timestamp = datetime(2026, 8, 29, tzinfo=UTC)

    first = journal.append(
        event_type="SIMULATED_FILL",
        source="execution-rust",
        payload={"asset": "BTC", "quantity": "0.01"},
        correlation_id=correlation_id,
        event_id=event_id,
        idempotency_key="fill-1",
        timestamp=timestamp,
    )
    second = journal.append(
        event_type="POSITION_UPDATED",
        source="portfolio",
        payload={"asset": "BTC", "quantity": "0.01"},
        correlation_id=correlation_id,
        idempotency_key="position-1",
        timestamp=timestamp,
    )

    assert first.previous_hash == "GENESIS"
    assert second.previous_hash == first.hash
    assert journal.verify() is True

    with pytest.raises(ValueError, match="duplicate event_id"):
        journal.append(
            event_type="DUPLICATE",
            source="test",
            payload={},
            event_id=event_id,
        )

    with pytest.raises(ValueError, match="duplicate idempotency_key"):
        journal.append(
            event_type="DUPLICATE",
            source="test",
            payload={},
            idempotency_key="fill-1",
        )


def test_reconciliation_detects_cross_state_divergence() -> None:
    result = reconcile(
        order_ids={"order-1"},
        fill_order_ids={"order-1", "missing-order"},
        expected_positions={"BTC": 1.0},
        actual_positions={"BTC": 0.5},
        journal_event_count=4,
        persisted_event_count=3,
    )

    assert result.consistent is False
    assert set(result.issues) == {
        ReconciliationIssue.FILL_WITHOUT_ORDER,
        ReconciliationIssue.POSITION_MISMATCH,
        ReconciliationIssue.JOURNAL_MISMATCH,
    }
