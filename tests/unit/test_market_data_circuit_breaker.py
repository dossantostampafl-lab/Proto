from services.market_data.circuit_breaker import CircuitState, MarketDataCircuitBreaker
from services.market_data.core import DataQualityIssue, DataQualityReport


def test_circuit_opens_on_fatal_data_quality_issue() -> None:
    breaker = MarketDataCircuitBreaker(recovery_successes=2)

    decision = breaker.evaluate(
        DataQualityReport(valid=False, issues=[DataQualityIssue.STALE_FEED])
    )

    assert decision.state == CircuitState.OPEN
    assert decision.shadow_decisions_allowed is False


def test_circuit_requires_consecutive_healthy_reports_before_closing() -> None:
    breaker = MarketDataCircuitBreaker(recovery_successes=2)
    breaker.evaluate(DataQualityReport(valid=False, issues=[DataQualityIssue.INVALID_SPREAD]))

    first = breaker.evaluate(DataQualityReport(valid=True, issues=[]))
    second = breaker.evaluate(DataQualityReport(valid=True, issues=[]))

    assert first.state == CircuitState.HALF_OPEN
    assert first.shadow_decisions_allowed is False
    assert second.state == CircuitState.CLOSED
    assert second.shadow_decisions_allowed is True


def test_nonfatal_issue_does_not_open_circuit() -> None:
    breaker = MarketDataCircuitBreaker()

    decision = breaker.evaluate(
        DataQualityReport(valid=False, issues=[DataQualityIssue.PRICE_JUMP])
    )

    assert decision.state == CircuitState.CLOSED
    assert decision.shadow_decisions_allowed is False
