# Volatility Risk Gate

The Python risk gate (`RiskEngine.validate` in `apps/api/app/simulation.py`) rejects new risk when snapshot volatility exceeds `max_volatility`, in simulation, replay and paper trading. Risk-reducing orders stay allowed.

## Policy

- New or increasing exposure is rejected with `VolatilityTooHigh` when observed volatility exceeds the supplied ceiling.
- Existing rejection reasons are preserved and the volatility rejection is appended.
- A strictly risk-reducing close may proceed through the volatility gate so a volatility spike cannot trap exposure that the caller is attempting to reduce.
- All existing hard safety controls, including kill switch, exposure, concentration, drawdown, and latency limits, continue to apply through the base risk evaluation.
- This control does not add brokerage, exchange, wallet, custody, deposit, withdrawal, wagering, or real-money execution connectivity.
