# Live monitor payload boundary

`LiveCryptoMonitor` owns orchestration, acceptance, persistence coordination, and bounded in-memory history. It does not own wire-payload schema details.

Market-data and order-book payload serialization is delegated to `apps.api.app.live_payloads`, which centralizes the public read-only source marker plus the hard invariants `financial_connectivity=false` and `real_money_execution=false`.

This boundary is intentionally one-way: `live_payloads` depends only on normalized market-data contracts and does not import API runtime state, persistence, WebSocket orchestration, execution engines, account credentials, or trading connectors.
