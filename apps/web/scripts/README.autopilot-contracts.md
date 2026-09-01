# Autopilot frontend contracts

`paper-autopilot-contract-check.mjs` covers the primary control placement and server-autopilot semantics.

`autopilot-freshness-contract-check.mjs` adds a regression gate for the UI status surface and the explicit simulation-only/no-financial-connectivity provenance. Server-side freshness enforcement is tested independently in `tests/test_paper_autopilot.py` and `tests/test_autopilot_live_boundary_contract.py`.
