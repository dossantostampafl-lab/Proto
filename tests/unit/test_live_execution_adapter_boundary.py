from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.live_execution import (
    AdapterCapabilities,
    AdapterDescriptor,
    AdapterEnvironment,
    AdapterRegistry,
    ExecutionMode,
    LiveReadinessClassifier,
    LiveReadinessEvidence,
    OrderIntent,
    ShadowExecutionAdapter,
)

NOW = datetime.now(UTC)


class _ExecutionCapableAdapter:
    descriptor = AdapterDescriptor(
        name="test-production-adapter",
        environment=AdapterEnvironment.PRODUCTION,
        official_api=True,
        capabilities=AdapterCapabilities(place_order=True),
    )

    def validate_connection(self) -> bool:
        return True

    def validate_permissions(self) -> bool:
        return True


def _order() -> OrderIntent:
    return OrderIntent(
        order_intent_id="order-2001",
        correlation_id="corr-2001",
        idempotency_key="idempotency-key-2001",
        instrument="BTC-USD",
        side="BUY",
        order_type="LIMIT",
        quantity=0.001,
        limit_price=100_000.0,
        time_in_force="IOC",
        expires_at=NOW + timedelta(minutes=2),
    )


@pytest.mark.parametrize(
    "mode",
    [ExecutionMode.SIMULATION, ExecutionMode.PAPER_TRADING, ExecutionMode.SHADOW],
)
def test_execution_capable_adapter_cannot_register_in_non_live_mode(
    mode: ExecutionMode,
) -> None:
    registry = AdapterRegistry(mode)
    with pytest.raises(ValueError, match="execution-capable adapter"):
        registry.register(_ExecutionCapableAdapter())


def test_shadow_adapter_records_intent_without_execution_capabilities() -> None:
    adapter = ShadowExecutionAdapter()
    registry = AdapterRegistry(ExecutionMode.SHADOW)
    registry.register(adapter)
    adapter.record_intent(_order())

    assert adapter.descriptor.environment == AdapterEnvironment.SHADOW
    assert adapter.descriptor.capabilities.place_order is False
    assert len(adapter.intents()) == 1
    assert registry.descriptors()[0].name == "shadow-no-network"


def test_readiness_defaults_keep_all_financial_execution_blocked() -> None:
    classification = LiveReadinessClassifier.classify(
        LiveReadinessEvidence(safe_scope_ready=True)
    )
    assert classification.proto_safe_scope == "READY"
    assert classification.live_execution_code == "NOT_READY"
    assert classification.sandbox == "NOT_VALIDATED"
    assert classification.shadow == "NOT_VALIDATED"
    assert classification.live_canary == "BLOCKED"
    assert classification.live_execution == "DISABLED"
    assert classification.withdrawals == "DISABLED"
    assert classification.custody == "NOT IMPLEMENTED"


def test_live_code_requires_official_adapter_in_addition_to_internal_controls() -> None:
    evidence = LiveReadinessEvidence(
        safe_scope_ready=True,
        contracts_ready=True,
        risk_engine_ready=True,
        oms_ready=True,
        reconciliation_ready=True,
        security_ready=True,
        observability_ready=True,
        official_adapter_implemented=False,
    )
    classification = LiveReadinessClassifier.classify(evidence)
    assert classification.live_execution_code == "NOT_READY"
    assert classification.live_canary == "BLOCKED"
    assert classification.live_execution == "DISABLED"


def test_live_requires_every_human_and_validation_gate() -> None:
    evidence = LiveReadinessEvidence(
        safe_scope_ready=True,
        contracts_ready=True,
        risk_engine_ready=True,
        oms_ready=True,
        reconciliation_ready=True,
        security_ready=True,
        observability_ready=True,
        official_adapter_implemented=True,
        sandbox_validated=True,
        shadow_validated=True,
        credentials_authorized=True,
        financial_limits_defined=True,
        human_activation_recorded=True,
        canary_authorized=True,
        live_authorized=False,
    )
    classification = LiveReadinessClassifier.classify(evidence)
    assert classification.live_execution_code == "READY"
    assert classification.live_canary == "AUTHORIZED"
    assert classification.live_execution == "DISABLED"

    authorized = LiveReadinessClassifier.classify(
        evidence.model_copy(update={"live_authorized": True})
    )
    assert authorized.live_execution == "AUTHORIZED"
    assert authorized.withdrawals == "DISABLED"
    assert authorized.custody == "NOT IMPLEMENTED"
