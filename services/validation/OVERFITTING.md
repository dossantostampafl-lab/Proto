# Overfitting diagnostics

Proto exposes two research diagnostics for model/strategy selection.

## Deflated Sharpe Ratio

`deflated_sharpe_ratio` adjusts an observed non-annualized Sharpe statistic for multiple trials and for observed skew/kurtosis. The expected maximum Sharpe term assumes approximately independent trials under a null process, so callers must not interpret the result as a guarantee of future performance.

## Probability of Backtest Overfitting

`probability_of_backtest_overfitting` uses combinatorially symmetric cross-validation (CSCV): equal contiguous segments are split into in-sample and out-of-sample halves; the best in-sample strategy is ranked out of sample; PBO is the fraction of splits where that selected strategy lands in the lower half of the out-of-sample ranking.

The function requires equal-length strategy return series and segment divisibility. It is intended to compare strategy/parameter candidates generated from the same experiment family.

These diagnostics are research gates only. They do not promote strategies automatically and do not establish financial profitability.
