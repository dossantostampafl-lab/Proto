from dataclasses import replace
from uuid import uuid4

import pytest

from services.events.bus import InMemoryEventBus
from services.events.journal import EventJournal
from services.events.reconciliation import ReconciliationIssue, reconcile


async def test_event_bus_retry_and_dead_letter_isolate_failed_messages() -> None:
    bus = InMemoryEventBus()
    original_id = await bus.publish("signals", {"event_id": "signal-1", "status": "new"})
    retry_id = await bus.retry("signals", {"event_id": "signal-1", "attempt": "2"})
    dead_id = await bus.dead_letter("signals", {"event_id": "signal-1", "reason": "poison"})

    original = [message async for message in bus.subscribe("signals")]
    retries = [message async for message in bus.subscribe("signals.retry")]
    dead_letters = [message async for message in bus.subscribe("signals.dead_letter")]

    assert [message.message_id for message in original] == [original_id]
    assert [message.message_id for message in retries] == [retry_id]
    assert [message.message_id for message in dead_letters] == [dead_id]
    assert dead_letters[0].payload["reason"] == "poison"


def test_duplicate_idempotency_key_is_rejected_under_replay_pressure() -> None:
    journal = EventJournal()
    correlation_id = uuid4()
    journal.append(
        event_type="fill.simulated",
        source="chaos-test",
        payload={"order_id": "order-1"},
        correlation_id=correlation_id,
        idempotency_key="command-1",
    )

    with pytest.raises(ValueError, match="duplicate idempotency_key"):
        journal.append(
            event_type="fill.simulated",
            source="chaos-test",
            payload={"order_id": "order-1"},
            correlation_id=correlation_id,
            idempotency_key="command-1",
        )


def test_hash_chain_detects_in_memory_payload_corruption() -> None:
    journal = EventJournal()
    event = journal.append(
        event_type="risk.approved",
        source="chaos-test",
        payload={"approved": True},
    )
    assert journal.verify() is True

    journal._events[0] = replace(event, payload={"approved": False})
    assert journal.verify() is False


def test_reconciliation_detects_position_and_journal_divergence() -> None:
    result = reconcile(
        order_ids={"order-1"},
        fill_order_ids={"order-1"},
        expected_positions={"BTC": 1.0},
        actual_positions={"BTC": 0.8},
        journal_event_count=3,
        persisted_event_count=2,
    )

    assert result.consistent is False
    assert ReconciliationIssue.POSITION_MISMATCH in result.issues
    assert ReconciliationIssue.JOURNAL_MISMATCH in result.issues
