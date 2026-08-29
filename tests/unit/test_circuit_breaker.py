from services.safety import CircuitBreakerAction, CircuitBreakerReason, evaluate_circuit_breakers


def test_circuit_breaker_continues_when_dependencies_are_healthy() -> None:
    decision = evaluate_circuit_breakers()

    assert decision.action == CircuitBreakerAction.CONTINUE
    assert decision.reasons == ()
    assert decision.halt_required is False


def test_circuit_breaker_degrades_for_optional_infrastructure_failures() -> None:
    decision = evaluate_circuit_breakers(
        database_available=False,
        event_bus_available=False,
    )

    assert decision.action == CircuitBreakerAction.DEGRADED
    assert decision.reasons == (
        CircuitBreakerReason.DATABASE_UNAVAILABLE,
        CircuitBreakerReason.EVENT_BUS_UNAVAILABLE,
    )
    assert decision.halt_required is False


def test_circuit_breaker_halts_for_stale_data_or_position_mismatch() -> None:
    decision = evaluate_circuit_breakers(
        data_fresh=False,
        positions_consistent=False,
    )

    assert decision.action == CircuitBreakerAction.HALT
    assert CircuitBreakerReason.STALE_DATA in decision.reasons
    assert CircuitBreakerReason.POSITION_MISMATCH in decision.reasons
    assert decision.halt_required is True


def test_circuit_breaker_halts_on_unknown_state() -> None:
    decision = evaluate_circuit_breakers(unknown_state=True)

    assert decision.action == CircuitBreakerAction.HALT
    assert decision.reasons == (CircuitBreakerReason.UNKNOWN_STATE,)
