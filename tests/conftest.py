"""Offline defaults shared by every test."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.api.app import oms_surface
from services.execution.constitution import REFERENCE_CAPITAL

# The production lookup (public USDTBRL ask), kept before any test replaces it.
_REAL_REFERENCE_CAPITAL = oms_surface._reference_capital


@pytest.fixture
def real_reference_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the production lookup; the test stubs the rate itself."""

    monkeypatch.setattr(oms_surface, "_reference_capital", _REAL_REFERENCE_CAPITAL)


@pytest.fixture(autouse=True)
def _offline_reference_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    """No network in tests: every account keeps the plain R$ 10,000 number unless a test
    uses the `real_reference_capital` fixture with a stubbed rate."""

    async def plain(_quote_asset: str) -> Decimal:
        return REFERENCE_CAPITAL

    monkeypatch.setattr(oms_surface, "_reference_capital", plain)
