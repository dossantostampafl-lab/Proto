# LIVE_MONITORING isolation gate

This phase separates the production-facing public crypto monitor from the legacy research and simulation application.

## Production entrypoint

The default API container runs:

`apps.api.app.live_app:app`

The standalone app exposes only public read-only BTC/ETH/SOL monitoring, event-runtime health, bounded persisted history, descriptive analytics, Prometheus metrics and the two public-data WebSocket channels.

The legacy `apps.api.app.main:app` remains in the repository for historical research/simulation compatibility but is not the default container entrypoint.

## Required invariants

- public unauthenticated market data only;
- HTTP surface is read-only;
- WebSocket surface is limited to `market-data` and `orderbook`;
- no probability/edge/trading endpoints are mounted;
- no portfolio, fills, replay, simulation or kill-switch endpoints are mounted;
- `financial_connectivity=false`;
- `real_money_execution=false`;
- no account credentials or private exchange APIs.

## Security boundary

Every HTTP response receives no-store and browser hardening headers. Non-read HTTP methods are rejected at middleware level. The existing WebSocket hub enforces origin, capacity, message-size and send-timeout limits.

## Exit criteria

- standalone app tests prove legacy financial/research endpoints are absent;
- static isolation tests reject imports from legacy prediction/trading modules;
- Docker defaults to the standalone app;
- CI is green;
- Security is green;
- Graphify is green.
