# Paper autopilot live-freshness invariant

The server paper autopilot may evaluate and submit a simulated order only while the selected public market symbol is:

- observed on the currently connected public feed generation;
- source-fresh and receipt-fresh according to the canonical live monitor;
- backed by a connected source with fresh source messages;
- inside the explicit no-financial-connectivity / no-real-money boundary.

The guard is evaluated once before reading the trading snapshot and again immediately before `/v1/simulate` is invoked. A health transition between those two points fails closed with `LIVE_DATA_BECAME_STALE` and increments no submission counter.

This invariant applies only to paper/simulation execution. It does not add exchange credentials, account connectivity or real-money order execution.
