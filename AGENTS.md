# Proto Agent Operating Guide

## Graphify first

When `graphify-out/graph.json` or `graphify-out/GRAPH_REPORT.md` is available, use the Graphify knowledge graph before broad repository search for architecture, dependencies, impact analysis, and call-flow questions.

Recommended local flow:

```bash
graphify .
graphify query "<architecture question>"
graphify explain "<symbol or concept>"
```

CI publishes `graphify-code-knowledge-graph` with `graph.json` and `GRAPH_REPORT.md` for pull-request revisions.

## Workstreams

Keep implementation separated into architecture, Python, Rust, data, frontend, tests, security, and performance workstreams. Validate cross-workstream changes through CI before merge.

## Live monitoring boundary

Live functionality is limited to public read-only BTC/ETH/SOL market monitoring and descriptive analytics. It must not accept account credentials, API secrets, wallet keys, brokerage/exchange account access, financial connectivity, deposits, withdrawals, custody, or real-money order execution.

Prediction-market functionality must remain isolated from live feeds and must not be connected to gambling or wagering services.

## Git workflow

- Never write directly to `main`.
- Use feature branches and pull requests.
- Do not merge with failing required CI/security gates.
- Prefer deterministic tests and offline fixtures in CI.
- Never commit secrets or generated Graphify output unless explicitly required; CI artifacts are the default distribution mechanism.

## Validation loop

PLAN -> IMPLEMENT -> TEST -> AUDIT -> FIND DEFECTS -> FIX -> RETEST -> RE-AUDIT.
