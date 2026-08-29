import pytest

from services.events.runtime import EventRuntime


def test_event_runtime_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError):
        EventRuntime(backend="unknown")


@pytest.mark.asyncio
async def test_memory_event_runtime_publishes_and_tracks_health() -> None:
    runtime = EventRuntime(backend="memory")

    await runtime.start()
    message_id = await runtime.publish("proto.system", {"event": "ready"})
    snapshot = runtime.snapshot()

    assert message_id == "1-0"
    assert snapshot.backend == "memory"
    assert snapshot.started is True
    assert snapshot.ready is True
    assert snapshot.publish_count == 1
    assert snapshot.publish_failures == 0
    assert snapshot.last_error is None

    messages = [message async for message in runtime.bus.subscribe("proto.system")]
    assert len(messages) == 1
    assert messages[0].payload == {"event": "ready"}

    await runtime.close()
    closed = runtime.snapshot()
    assert closed.started is False
    assert closed.ready is False


@pytest.mark.asyncio
async def test_safe_publish_fails_closed_before_start() -> None:
    runtime = EventRuntime(backend="memory")

    message_id = await runtime.safe_publish("proto.system", {"event": "ignored"})

    assert message_id is None
    snapshot = runtime.snapshot()
    assert snapshot.publish_count == 0
    assert snapshot.publish_failures == 0
