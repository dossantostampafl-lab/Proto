from __future__ import annotations

from pydantic import BaseModel


class LiveReadinessEvidence(BaseModel):
    safe_scope_ready: bool = False
    contracts_ready: bool = False
    risk_engine_ready: bool = False
    oms_ready: bool = False
    reconciliation_ready: bool = False
    security_ready: bool = False
    observability_ready: bool = False
    official_adapter_implemented: bool = False
    sandbox_validated: bool = False
    shadow_validated: bool = False
    credentials_authorized: bool = False
    financial_limits_defined: bool = False
    human_activation_recorded: bool = False
    canary_authorized: bool = False
    live_authorized: bool = False


class LiveReadinessClassification(BaseModel):
    proto_safe_scope: str
    live_execution_code: str
    sandbox: str
    shadow: str
    live_canary: str
    live_execution: str
    withdrawals: str
    custody: str


class LiveReadinessClassifier:
    @staticmethod
    def classify(evidence: LiveReadinessEvidence) -> LiveReadinessClassification:
        code_ready = all(
            (
                evidence.contracts_ready,
                evidence.risk_engine_ready,
                evidence.oms_ready,
                evidence.reconciliation_ready,
                evidence.security_ready,
                evidence.observability_ready,
                evidence.official_adapter_implemented,
            )
        )
        canary_authorized = all(
            (
                code_ready,
                evidence.sandbox_validated,
                evidence.shadow_validated,
                evidence.credentials_authorized,
                evidence.financial_limits_defined,
                evidence.human_activation_recorded,
                evidence.canary_authorized,
            )
        )
        live_authorized = all((canary_authorized, evidence.live_authorized))
        return LiveReadinessClassification(
            proto_safe_scope="READY" if evidence.safe_scope_ready else "NOT_READY",
            live_execution_code="READY" if code_ready else "NOT_READY",
            sandbox="VALIDATED" if evidence.sandbox_validated else "NOT_VALIDATED",
            shadow="VALIDATED" if evidence.shadow_validated else "NOT_VALIDATED",
            live_canary="AUTHORIZED" if canary_authorized else "BLOCKED",
            live_execution="AUTHORIZED" if live_authorized else "DISABLED",
            withdrawals="DISABLED",
            custody="NOT IMPLEMENTED",
        )
