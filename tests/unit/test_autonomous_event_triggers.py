from __future__ import annotations

import pytest

from services.events.bus import InMemoryEventBus
from services.events.runtime import EventRuntime
from services.orchestration import (
    AutonomousEventDispatcher,
    AutonomousEventType,
    EventTriggerRule,
)


class BrainProbe:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    async def enqueue(self, job_name: str, **kwargs):
        self.enqueued.append({"job_name": job_name, **kwargs})
        return object()


@pytest.mark.asyncio
async def test_normalized_market_event_dispatches_allowlisted_job_once() -> None:
    bus = InMemoryEventBus()
    brain = BrainProbe()
    dispatcher = AutonomousEventDispatcher(
        bus=bus,
        brain=brain,  # type: ignore[arg-type]
        stream="proto.market.normalized",
        rules=(
            EventTriggerRule(
                event_type=AutonomousEventType.MARKET_TICK,
                job_name="market-data-health",
                mode="LIVE_MONITORING",
            ),
        ),
        default_event_type=AutonomousEventType.MARKET_TICK,
    )
    await bus.publish(
        "proto.market.normalized",
        {
            "event_id": "abc",
            "source": "COINBASE",
            "symbol": "BTC",
            "occurred_at": "2026-09-03T21:00:00+00:00",
            "received_at": "2026-09-03T21:00:00.010000+00:00",
            "sequence": "42",
            "job_name": "paper-decision",
            "mode": "PAPER_TRADING",
        },
    )

    assert await dispatcher.drain() == 1
    assert await dispatcher.drain() == 0
    assert len(brain.enqueued) == 1
    queued = brain.enqueued[0]
    assert queued["job_name"] == "market-data-health"
    assert queued["mode"] == "LIVE_MONITORING"
    assert queued["payload"]["trigger_event"] == "MARKET_TICK"
    assert queued["payload"]["symbol"] == "BTC"
    assert "paper-decision" not in str(queued)
    assert "PAPER_TRADING" not in str(queued)


@pytest.mark.asyncio
async def test_unknown_typed_event_is_ignored_without_enqueue() -> None:
    bus = InMemoryEventBus()
    brain = BrainProbe()
    dispatcher = AutonomousEventDispatcher(
        bus=bus,
        brain=brain,  # type: ignore[arg-type]
        stream="proto.autonomy",
        rules=(
            EventTriggerRule(
                event_type=AutonomousEventType.MODEL_DEGRADED,
                job_name="market-data-health",
                mode="LIVE_MONITORING",
            ),
        ),
    )
    await bus.publish("proto.autonomy", {"event_type": "OPPORTUNITY_FOUND"})

    assert await dispatcher.drain() == 0
    assert brain.enqueued == []
    assert dispatcher.status()["ignored"] == 1


@pytest.mark.asyncio
async def test_runtime_not_ready_is_fail_closed_and_non_blocking() -> None:
    runtime = EventRuntime(backend="memory")
    brain = BrainProbe()
    dispatcher = AutonomousEventDispatcher(
        runtime=runtime,
        brain=brain,  # type: ignore[arg-type]
        stream="proto.market.normalized",
        rules=(
            EventTriggerRule(
                event_type=AutonomousEventType.MARKET_TICK,
                job_name="market-data-health",
                mode="LIVE_MONITORING",
            ),
        ),
        default_event_type=AutonomousEventType.MARKET_TICK,
    )

    assert await dispatcher.drain() == 0
    assert dispatcher.status()["runtime_ready"] is False
    assert brain.enqueued == []
