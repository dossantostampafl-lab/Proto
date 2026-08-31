# Synthetic research surface policy

Operational API consumers must not receive deterministic synthetic market telemetry unless synthetic research is explicitly enabled. By default, synthetic market catalog, market data, order-book, data-quality, probability, edge, expected-value, Greeks, Hawkes, lifecycle, resolution-grid and expiry-map routes fail closed with HTTP 503 and direct callers to historical replay or public read-only live market data.

Canonical simulation portfolio, positions and P&L remain available because they represent explicit paper/simulation state rather than fabricated market telemetry. Live monitoring remains public read-only with financial connectivity and real-money execution disabled.
