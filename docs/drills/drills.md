# Automated drills (Phase 6b)

The drills produce the `ADVERSARIAL_SUITE`, `RECOVERY_DRILL` and `ROLLBACK_DRILL` evidence the
CANARY gate needs. Each one runs the **real control code** (Risk Engine, permits, OMS, ledger,
promotion registry, governor) against offline fakes and temporary SQLite files. They never touch
production state, a venue or credentials.

## The drills

| Drill | What it proves |
|---|---|
| `ADVERSARIAL_SUITE` | A compliant intent is approved, and 17 hostile intents are each refused for the expected reason: oversized, off-collar, short, unknown instrument, off-grid, uncertified CANARY, LIVE, stale or missing quote, kill switches, lockdown, daily-loss and drawdown breaches, leverage over 1×, a canary over its R$500 sizing. 6 permit attacks, next to 2 genuine permits that must pass: replay, forgery, another intent, tampered quantity, expiry, an HMAC permit sent to an Ed25519 executor. Then a **seeded fuzz of 400 random intents and accounts**: every approved one is re-checked against the constitution, computed independently of the engine |
| `RECOVERY_DRILL` | A lost response, a lost request, a crash before submit, a resting order across a restart and a spent permit after a restart. The new process resolves every order by asking the venue, never resends, releases holds and stays reconciled |
| `ROLLBACK_DRILL` | A scope walked up to CANARY is demoted to PAPER by a divergence, CANARY intents are refused from then on, the demotion survives a restart and automation cannot re-promote. The kill switch and a breached lockdown hold until an operator releases them, and a lockdown cannot be released while still breached |

If a control raises instead of refusing, the drill records a failed case; it never aborts.

## Reproducible evidence

A report holds only deterministic facts: case names, expected and observed outcomes. Its digest
(SHA-256 of the canonical report) is the same for the same drill version and seed:

```bash
python -m services.drills run ADVERSARIAL_SUITE --seed 7 --out adversarial.json
python -m services.drills verify adversarial.json   # re-runs it: "reproduced <digest>"
```

`run` exits 0 only when the drill passes; `verify` exits 0 only when the digest reproduces and the
drill passes (2 for a report from another drill version).

## Recording it on a scope

`POST /v1/promotion/evidence/drill` (operator) with `{scope, kind, seed}` runs the drill inside the
API and records the evidence with its outcome. The reference is
`drill:<kind>:v<version>:seed=<seed>:sha256=<digest prefix>`, which is everything a reviewer needs
to re-run it. A failed drill records failed evidence and keeps the gate shut.
