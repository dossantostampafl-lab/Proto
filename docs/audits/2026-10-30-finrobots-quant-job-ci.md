# Temporary FinRobots/ProtoBrain integration test — 2026-10-30

**Validation branch only — do not merge or deploy.**

Virtual merge from temporary draft PR #363:
- Claude PR #360 @ `d1317abba097d8dfa055120afe82428b690f3407`
- Codex PR #359 @ `53d40630dffdba4fbc0dde088d2e22ed2d0ef598`
- Merge commit `2cf28837bf80bf6b9b32d35c627c76765cbd19e0`

Trigger standard CI: Python 3.13 Ruff + full pytest (including the
new durable quant ProtoBrain job), frontend typecheck/build/contract,
Docker Compose, mutation safety, CodeQL and Graphify. The branch is
ephemeral validation only, not a release candidate.
