from __future__ import annotations

import pytest

from apps.api.app.orchestration_surface import orchestration_status
from services.orchestration import CATALOG_VERSION, JOB_CATALOG, JobCapability


def test_versioned_catalog_covers_required_safe_domains() -> None:
    capabilities = {contract.spec.capability for contract in JOB_CATALOG.values()}
    assert {
        JobCapability.MARKET_DATA,
        JobCapability.QUANT_RESEARCH,
        JobCapability.CALIBRATION,
        JobCapability.RISK,
        JobCapability.REPLAY,
        JobCapability.PORTFOLIO,
        JobCapability.OBSERVABILITY,
        JobCapability.PAPER_EXECUTION,
    }.issubset(capabilities)
    assert CATALOG_VERSION


def test_paper_contract_requires_independent_risk_gate() -> None:
    contract = JOB_CATALOG["paper-decision"]
    assert contract.spec.requires_risk_gate is True
    assert contract.spec.allowed_modes == frozenset({"PAPER_TRADING"})
    assert contract.spec.financial_side_effects is False


@pytest.mark.asyncio
async def test_control_plane_never_reports_live_execution_ready() -> None:
    snapshot = await orchestration_status()
    readiness = snapshot["readiness"]
    safety = snapshot["safety"]
    control_plane = snapshot["control_plane"]

    assert readiness["live_ready"] is False
    assert safety["financial_connectivity"] is False
    assert safety["real_money_execution"] is False
    assert safety["live_canary_max_notional"] == 0
    assert control_plane["read_only_surface"] is True
    assert control_plane["arbitrary_job_execution_endpoint"] is False
