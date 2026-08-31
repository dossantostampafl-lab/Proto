import React, { useMemo, useState } from "react";

type PerformanceMetrics = {
  sample_count: number;
  cumulative_return: number;
  mean_return: number;
  volatility: number;
  sharpe: number;
  sortino: number | null;
  max_drawdown: number;
  hit_rate: number;
  profit_factor: number | null;
};

type MonteCarloSummary = {
  simulations: number;
  path_length: number;
  block_size: number;
  seed: number;
  median_terminal_return: number;
  p05_terminal_return: number;
  p95_terminal_return: number;
  median_max_drawdown: number;
  p95_max_drawdown: number;
  probability_of_loss: number;
};

type ValidationReport = {
  fold_count: number;
  performance: PerformanceMetrics;
  positive_fold_fraction: number;
  worst_fold_return: number;
  median_fold_return: number;
  robustness_score: number;
  deflated_sharpe_ratio: number;
  monte_carlo: MonteCarloSummary;
  regime: unknown | null;
  parameter_stability: unknown | null;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

type Props = {
  apiBase: string;
};

function formatNumber(value: number | null, digits = 3) {
  if (value === null) return "∞";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function formatPercent(value: number, digits = 2) {
  return `${formatNumber(value * 100, digits)}%`;
}

function parseReturns(source: string): number[] {
  const values = source
    .split(/[\s,;]+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .map(Number);
  if (values.length < 6 || values.some((value) => !Number.isFinite(value) || value <= -1)) {
    throw new Error("Enter at least six finite returns greater than -1.");
  }
  return values;
}

export function ValidationPanel({ apiBase }: Props) {
  const [rawReturns, setRawReturns] = useState("");
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sampleCount = useMemo(() => {
    if (!rawReturns.trim()) return 0;
    return rawReturns.split(/[\s,;]+/).filter(Boolean).length;
  }, [rawReturns]);

  async function runValidation() {
    setBusy(true);
    try {
      const returns = parseReturns(rawReturns);
      const testSize = Math.max(2, Math.floor(returns.length / 4));
      const trainSize = returns.length - testSize - 1;
      if (trainSize < 3) throw new Error("The return series is too short for walk-forward validation.");

      const response = await fetch(`${apiBase}/research/validation/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          returns,
          train_size: trainSize,
          test_size: testSize,
          purge_size: 1,
          embargo_size: 0,
          trials: 20,
          monte_carlo_simulations: 500,
          monte_carlo_block_size: Math.min(5, returns.length),
          monte_carlo_seed: 7,
        }),
      });
      const body = (await response.json()) as ValidationReport & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Validation request failed.");
      setReport(body);
      setError(null);
    } catch (validationError) {
      setReport(null);
      setError(validationError instanceof Error ? validationError.message : "Validation failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="validationShell" aria-labelledby="validation-title">
      <div className="validationHeader">
        <div>
          <p className="validationEyebrow">VALIDATION LAB / OUT-OF-SAMPLE</p>
          <h2 id="validation-title">Test robustness before trusting edge.</h2>
          <p>
            Paste realized research or replay returns. Proto sends them to the validation engine and
            renders the returned walk-forward, DSR, drawdown and Monte Carlo diagnostics.
          </p>
        </div>
        <span className="validationStatus">{report ? "REPORT READY" : "NOT RUN"}</span>
      </div>

      <div className="validationInputGrid">
        <label>
          <span>Return series</span>
          <textarea
            aria-label="Return series"
            placeholder="0.012, -0.004, 0.009, ..."
            rows={5}
            value={rawReturns}
            onChange={(event) => setRawReturns(event.target.value)}
          />
          <small>{sampleCount} samples · decimal returns, not percentages</small>
        </label>
        <div className="validationRunBox">
          <strong>No synthetic score is shown.</strong>
          <p>The panel remains empty until a real research/replay series is submitted.</p>
          <button disabled={busy || sampleCount < 6} onClick={() => void runValidation()}>
            {busy ? "Validating…" : "Run validation"}
          </button>
        </div>
      </div>

      {error && <p className="validationError">{error}</p>}

      {report && (
        <>
          <div className="validationMetrics">
            <article><span>DSR</span><b>{formatPercent(report.deflated_sharpe_ratio)}</b></article>
            <article><span>Robustness</span><b>{formatPercent(report.robustness_score)}</b></article>
            <article><span>Positive folds</span><b>{formatPercent(report.positive_fold_fraction)}</b></article>
            <article><span>Max drawdown</span><b>{formatPercent(report.performance.max_drawdown)}</b></article>
            <article><span>Hit rate</span><b>{formatPercent(report.performance.hit_rate)}</b></article>
            <article><span>Profit factor</span><b>{formatNumber(report.performance.profit_factor)}</b></article>
          </div>

          <div className="validationDetailGrid">
            <article>
              <p className="validationEyebrow">WALK-FORWARD</p>
              <dl>
                <div><dt>Folds</dt><dd>{report.fold_count}</dd></div>
                <div><dt>Median fold</dt><dd>{formatPercent(report.median_fold_return)}</dd></div>
                <div><dt>Worst fold</dt><dd>{formatPercent(report.worst_fold_return)}</dd></div>
                <div><dt>Cumulative</dt><dd>{formatPercent(report.performance.cumulative_return)}</dd></div>
              </dl>
            </article>
            <article>
              <p className="validationEyebrow">MONTE CARLO / BLOCK BOOTSTRAP</p>
              <dl>
                <div><dt>P05 terminal</dt><dd>{formatPercent(report.monte_carlo.p05_terminal_return)}</dd></div>
                <div><dt>Median terminal</dt><dd>{formatPercent(report.monte_carlo.median_terminal_return)}</dd></div>
                <div><dt>P95 drawdown</dt><dd>{formatPercent(report.monte_carlo.p95_max_drawdown)}</dd></div>
                <div><dt>P(loss)</dt><dd>{formatPercent(report.monte_carlo.probability_of_loss)}</dd></div>
              </dl>
            </article>
          </div>

          <p className="validationBoundary">
            Financial connectivity: {String(report.financial_connectivity)} · Real-money execution: {String(report.real_money_execution)}
          </p>
        </>
      )}
    </section>
  );
}
