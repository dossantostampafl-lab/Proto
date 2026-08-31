# Validation Engine

This package provides deterministic research-validation primitives for Proto.

Current scope:

- walk-forward fold generation;
- purge gap between the training tail and test window;
- embargo gap before each test window;
- performance metrics from out-of-sample return series;
- fold consistency and drawdown-aware robustness summary.

The engine is research-only. It does not promote a strategy automatically and does not connect to financial venues.

Planned extensions should build on this package rather than reimplement split logic elsewhere: deflated Sharpe ratio, probability of backtest overfitting, block bootstrap/Monte Carlo, regime stability and parameter-surface analysis.
