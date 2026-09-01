# Frontend data provenance

The deployed quant terminal intentionally separates public live market telemetry from synthetic research surfaces.

## Live public feed

Displayed as live market data:

- BTC/ETH/SOL price
- bid / ask
- spread
- size / L1 microstructure
- live analytics derived from the public read-only feed

These values come from the public live monitor. No financial account connectivity or real-money execution is enabled.

## Synthetic research

Displayed as synthetic research and never as live market telemetry:

- lifecycle / resolution grid
- model probability / fair probability
- net edge
- expiry torus
- synthetic Greeks
- Hawkes research state

The backend marks these lifecycle surfaces as `SYNTHETIC_DEMO`. The frontend must keep that provenance visible so research output cannot be mistaken for a live prediction-market quote.
