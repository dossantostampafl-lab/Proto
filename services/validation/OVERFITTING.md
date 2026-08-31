# Overfitting diagnostics

Proto exposes research diagnostics for model/strategy selection.

## Deflated Sharpe Ratio

`deflated_sharpe_ratio` adjusts an observed non-annualized Sharpe statistic for multiple trials and for observed skew/kurtosis. The expected maximum Sharpe term assumes approximately independent trials under a null process, so callers must not interpret the result as a guarantee of future performance.

The `trials` argument should represent the effective independent search burden, not merely the number of variants that survived researcher filtering.

## Effective independent trials

`effective_number_of_trials` estimates how many independent trials are implied by a family of correlated return series before the DSR penalty is applied. For `M` declared trials and average pairwise correlation `rho`, Proto computes the implied count as:

`rho + (1 - rho) * M`

The implied count is clipped to `[1, M]`, then rounded upward for the integer DSR trial count. Perfectly correlated variants therefore collapse to one effective trial; weakly correlated variants retain more of the declared search burden. Negative average correlation is never allowed to create more independent trials than were actually run.

Every trial series must have equal length, finite observations and non-zero variance. Invalid correlation evidence is rejected rather than silently replaced with a favorable assumption. This estimator is deliberately transparent and dependency-light; it is an effective-search approximation, not proof that the underlying strategies are statistically independent.

## Probability of Backtest Overfitting

`probability_of_backtest_overfitting` uses combinatorially symmetric cross-validation (CSCV): equal contiguous segments are split into in-sample and out-of-sample halves; the best in-sample strategy is ranked out of sample; PBO is the fraction of splits where that selected strategy lands in the lower half of the out-of-sample ranking.

The function requires equal-length strategy return series and segment divisibility. It is intended to compare strategy/parameter candidates generated from the same experiment family.

These diagnostics are research gates only. They do not promote strategies automatically and do not establish financial profitability.
