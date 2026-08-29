import pytest

from services.chaos.scenarios import ChaosScenario, FaultInjection, inject_fault


def test_disabled_fault_is_noop_and_preserves_input() -> None:
    payload = {"sequence": 7, "market_id": "btc-replay"}

    result = inject_fault(
        payload,
        FaultInjection(scenario=ChaosScenario.DUPLICATE_EVENT, enabled=False),
    )

    assert result.payload == payload
    assert result.payload is not payload
    assert result.transport_connected is True
    assert result.should_dead_letter is False


def test_stale_market_data_is_marked_without_mutating_original() -> None:
    payload = {"market_id": "btc-replay"}

    result = inject_fault(
        payload,
        FaultInjection(
            scenario=ChaosScenario.STALE_MARKET_DATA,
            stale_by_ms=2_500,
        ),
    )

    assert payload == {"market_id": "btc-replay"}
    assert result.payload["is_stale"] is True
    assert result.payload["stale_by_ms"] == 2_500


def test_sequence_gap_is_deterministic() -> None:
    result = inject_fault(
        {"sequence": 10},
        FaultInjection(scenario=ChaosScenario.SEQUENCE_GAP, sequence_gap=3),
    )

    assert result.payload["sequence"] == 14
    assert result.payload["sequence_gap"] == 3


def test_latency_spike_is_simulated_without_wall_clock_sleep() -> None:
    result = inject_fault(
        {"event_id": "event-1"},
        FaultInjection(scenario=ChaosScenario.LATENCY_SPIKE, latency_ms=750),
    )

    assert result.simulated_latency_ms == 750
    assert result.transport_connected is True


def test_disconnect_and_malformed_payload_have_explicit_failure_semantics() -> None:
    disconnected = inject_fault(
        {"event_id": "event-1"},
        FaultInjection(scenario=ChaosScenario.TRANSPORT_DISCONNECT),
    )
    malformed = inject_fault(
        {"event_id": "event-1"},
        FaultInjection(scenario=ChaosScenario.MALFORMED_PAYLOAD),
    )

    assert disconnected.transport_connected is False
    assert malformed.payload == {"malformed": True}
    assert malformed.should_dead_letter is True


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (
            FaultInjection(scenario=ChaosScenario.LATENCY_SPIKE),
            "LATENCY_SPIKE requires latency_ms > 0",
        ),
        (
            FaultInjection(scenario=ChaosScenario.STALE_MARKET_DATA),
            "STALE_MARKET_DATA requires stale_by_ms > 0",
        ),
        (
            FaultInjection(scenario=ChaosScenario.SEQUENCE_GAP),
            "SEQUENCE_GAP requires sequence_gap > 0",
        ),
    ],
)
def test_scenario_specific_parameters_are_required(
    fault: FaultInjection,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        inject_fault({}, fault)
