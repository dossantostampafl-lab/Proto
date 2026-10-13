from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app.event_state import JournalIntegrity, JournalUnavailable, OperationalJournal
from services.events import (
    DuplicateEvent,
    EventJournal,
    JournalConflict,
    JournalCorrupted,
    JournalError,
    SqlJournalStore,
    verify_chain,
)
from services.events.journal import GENESIS_HASH
from services.events.journal_store import journal_table

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def append(journal: EventJournal, n: int = 1, **kwargs):
    return [
        journal.append(
            event_type=kwargs.get("event_type", "TEST"),
            source="unit",
            payload={"n": i},
            timestamp=T0,
        )
        for i in range(n)
    ]


# --- pure core ----------------------------------------------------------------


def test_chain_has_gap_free_sequence_and_links_from_genesis() -> None:
    journal = EventJournal()
    first, second, third = append(journal, 3)
    assert (first.sequence, second.sequence, third.sequence) == (1, 2, 3)
    assert first.previous_hash == GENESIS_HASH
    assert second.previous_hash == first.hash
    assert journal.head_sequence == len(journal) == 3
    assert journal.head_hash == third.hash
    assert verify_chain([first, second, third]) == 3
    assert journal.verify_window() is True


def test_payload_is_canonical_json_and_immutable() -> None:
    journal = EventJournal()
    event = journal.append(event_type="T", source="s", payload={"b": 1, "a": [1, 2]})
    assert event.payload_json == '{"a":[1,2],"b":1}'
    event.payload["a"].append(3)
    assert event.payload == {"a": [1, 2], "b": 1}


@pytest.mark.parametrize("payload", [{"x": float("nan")}, {"x": uuid4()}, {"x": T0}])
def test_non_json_native_or_non_finite_payloads_are_rejected(payload) -> None:
    with pytest.raises(JournalError, match="JSON-native"):
        EventJournal().append(event_type="T", source="s", payload=payload)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(JournalError, match="timezone"):
        EventJournal().append(
            event_type="T", source="s", payload={}, timestamp=datetime(2026, 1, 1)
        )


def test_duplicate_idempotency_key_returns_the_original_event() -> None:
    journal = EventJournal()
    original = journal.append(event_type="T", source="s", payload={}, idempotency_key="k")
    with pytest.raises(DuplicateEvent) as duplicate:
        journal.append(event_type="T", source="s", payload={"other": 1}, idempotency_key="k")
    assert duplicate.value.existing == original
    assert journal.head_sequence == 1


def test_duplicate_event_id_is_rejected() -> None:
    journal = EventJournal()
    event_id = uuid4()
    journal.append(event_type="T", source="s", payload={}, event_id=event_id)
    with pytest.raises(DuplicateEvent, match="event_id"):
        journal.append(event_type="T", source="s", payload={}, event_id=event_id)


def test_commit_rejects_stale_or_tampered_events() -> None:
    journal = EventJournal()
    stale = journal.prepare(event_type="T", source="s", payload={})
    journal.append(event_type="T", source="s", payload={})
    with pytest.raises(JournalConflict):
        journal.commit(stale)
    fresh = journal.prepare(event_type="T", source="s", payload={})
    with pytest.raises(JournalCorrupted):
        journal.commit(replace(fresh, payload_json='{"forged":true}'))


@pytest.mark.parametrize(
    "tamper",
    [
        lambda events: events.__setitem__(1, replace(events[1], payload_json='{"n":99}')),
        lambda events: events.pop(1),
        lambda events: events.reverse(),
        lambda events: events.__setitem__(1, replace(events[1], sequence=5)),
    ],
    ids=["edited", "deleted", "reordered", "renumbered"],
)
def test_verify_chain_detects_any_tampering(tamper) -> None:
    events = append(EventJournal(), 3)
    tamper(events)
    with pytest.raises(JournalCorrupted):
        verify_chain(events)


def test_memory_is_bounded_but_head_and_count_are_total() -> None:
    journal = EventJournal(window=2, key_cache=2)
    append(journal, 5)
    assert len(journal.recent(10)) == 2
    assert journal.head_sequence == 5
    assert journal.verify_window() is True


def test_window_verification_detects_in_memory_corruption() -> None:
    journal = EventJournal()
    append(journal, 2)
    journal._window[0] = replace(journal._window[0], payload_json='{"n":7}')
    assert journal.verify_window() is False


# --- durable store and API facade ---------------------------------------------


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    journal_store = SqlJournalStore(engine, batch_size=2)
    await journal_store.create_schema()
    yield journal_store
    await engine.dispose()


async def test_events_survive_a_restart_and_the_chain_continues(store) -> None:
    first_process = OperationalJournal(store)
    await first_process.start()
    for i in range(5):
        await first_process.record(event_type="T", source="s", payload={"i": i})

    restarted = OperationalJournal(store)
    assert restarted.integrity is JournalIntegrity.UNVERIFIED
    await restarted.start()
    assert restarted.integrity is JournalIntegrity.VALID
    assert restarted.journal.head_sequence == 5
    assert restarted.journal.head_hash == first_process.journal.head_hash
    result = await restarted.record(event_type="T", source="s", payload={"i": 5})
    assert result.created is True
    assert result.event["sequence"] == 6
    assert (await restarted.verify())["chain_valid"] is True
    assert await store.count() == 6


async def test_idempotency_survives_a_restart(store) -> None:
    first_process = OperationalJournal(store)
    await first_process.start()
    original = await first_process.record(
        event_type="T", source="s", payload={}, idempotency_key="signal-1"
    )

    restarted = OperationalJournal(store, EventJournal(key_cache=1))
    await restarted.start()
    await restarted.record(event_type="T", source="s", payload={}, idempotency_key="other")
    again = await restarted.record(
        event_type="T", source="s", payload={}, idempotency_key="signal-1"
    )
    assert again.created is False
    assert again.event == original.event
    assert await store.count() == 2


async def test_writes_are_refused_before_the_history_is_loaded(store) -> None:
    with pytest.raises(JournalUnavailable, match="UNVERIFIED"):
        await OperationalJournal(store).record(event_type="T", source="s", payload={})


async def test_tampered_history_is_detected_at_startup_and_writes_fail_closed(store) -> None:
    writer = OperationalJournal(store)
    await writer.start()
    for i in range(3):
        await writer.record(event_type="T", source="s", payload={"i": i})
    async with store.engine.begin() as connection:
        await connection.execute(
            update(journal_table)
            .where(journal_table.c.sequence == 2)
            .values(payload_json='{"i":42}')
        )

    restarted = OperationalJournal(store)
    await restarted.start()
    assert restarted.integrity is JournalIntegrity.CORRUPTED
    assert "sequence 2" in str(restarted.corruption)
    with pytest.raises(JournalUnavailable, match="CORRUPTED"):
        await restarted.record(event_type="T", source="s", payload={})
    verification = await writer.verify()
    assert verification["chain_valid"] is False
    assert "sequence 2" in str(verification["error"])


async def test_a_failed_write_never_reaches_memory(store) -> None:
    journal = OperationalJournal(store)
    await journal.start()
    await journal.record(event_type="T", source="s", payload={})
    await store.engine.dispose()

    class BrokenStore(SqlJournalStore):
        async def append(self, event) -> None:
            raise OSError("database is down")

    journal.store = BrokenStore(store.engine)
    with pytest.raises(JournalUnavailable):
        await journal.record(event_type="T", source="s", payload={})
    assert journal.journal.head_sequence == 1
    assert journal.write_failures == 1
    assert journal.integrity is JournalIntegrity.VALID


async def test_a_second_writer_is_detected_instead_of_forking(store) -> None:
    first = OperationalJournal(store)
    second = OperationalJournal(store)
    await first.start()
    await second.start()
    await first.record(event_type="T", source="first", payload={})
    with pytest.raises(JournalUnavailable):
        await second.record(event_type="T", source="second", payload={})
    assert second.integrity is JournalIntegrity.CORRUPTED
    assert await store.count() == 1


async def test_memory_only_journal_works_without_a_store() -> None:
    journal = OperationalJournal(None)
    assert journal.integrity is JournalIntegrity.VALID
    result = await journal.record(event_type="T", source="s", payload={"ok": True})
    assert result.created is True
    verification = await journal.verify()
    assert verification["chain_valid"] is True
    assert verification["scope"] == "memory_window"


# --- migration ----------------------------------------------------------------


def test_migration_creates_the_same_table_the_runtime_uses(monkeypatch) -> None:
    path = Path("migrations/versions/0007_operational_journal.py")
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0006_orchestration_job_runs"

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {
            column["name"] for column in inspect(connection).get_columns(migration.TABLE_NAME)
        }
        assert columns == set(journal_table.columns.keys())
        migration.downgrade()
        assert migration.TABLE_NAME not in inspect(connection).get_table_names()
