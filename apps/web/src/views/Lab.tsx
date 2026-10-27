import type { Calibration, Greeks, Hawkes, PnL } from "../api";
import { SYMBOLS } from "../api";
import { usePoll, useSeries } from "../hooks";
import type { Shared } from "../main";
import { clamp, Empty, Meter, Panel, Spark, Stat, num, pct, signed, tone, usd } from "../ui";

const horizon = (m: number) => (m < 60 ? `${m}m` : m % 1440 === 0 ? `${m / 1440}d` : `${m / 60}h`);

export function Lab({ markets, marketsStatus, selected, select }: Shared) {
  const lead = markets.find(m => m.symbol === selected);
  const calibration = usePoll<Calibration>("/models/calibration", 5000).data;
  const pnl = usePoll<PnL>("/pnl/attribution", 5000).data;
  const hawkes = usePoll<Hawkes>(lead ? `/hawkes/${selected}` : null, 5000).data;
  const greeks = usePoll<Greeks>(lead ? `/analytics/greeks/${encodeURIComponent(lead.market_id)}` : null, 5000).data;
  const intensity = useSeries(hawkes?.current_intensity);
  const horizons = [...new Set(markets.map(m => m.expiry_horizon_minutes))].sort((a, b) => a - b).slice(0, 8);
  const curve = calibration?.reliability_curve ?? [];

  if (!markets.length) {
    return (
      <div className="lab">
        <Panel title="Research" source="research" className="wide">
          <Empty>{marketsStatus === 503 ? "Synthetic research is disabled in this deployment." : "Research lifecycle unavailable."} Live public telemetry remains independent.</Empty>
        </Panel>
      </div>
    );
  }

  return (
    <div className="lab">
      <Panel title="Expiry grid" source="research" className="wide">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Horizon</th>{SYMBOLS.map(s => <th key={s} colSpan={3}>{s} · market / model / edge</th>)}</tr></thead>
            <tbody>
              {horizons.map(h => (
                <tr key={h}>
                  <td>{horizon(h)}</td>
                  {SYMBOLS.flatMap(s => {
                    const r = markets.find(m => m.symbol === s && m.expiry_horizon_minutes === h);
                    return [
                      <td key={`${s}m`}>{num(r?.market_probability, 3)}</td>,
                      <td key={`${s}f`}>{num(r?.model_probability, 3)}</td>,
                      <td key={`${s}e`} className={tone(r?.net_edge)}>{signed(r?.net_edge)}</td>,
                    ];
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Calibration" source="research">
        <div className="grid4">
          <Stat label="Brier" value={num(calibration?.brier_score, 3)} />
          <Stat label="Log loss" value={num(calibration?.log_loss, 3)} />
          <Stat label="ECE" value={num(calibration?.expected_calibration_error, 3)} />
          <Stat label="Samples" value={calibration?.observation_count ?? "—"} />
        </div>
        {curve.length ? (
          <div className="reliability" aria-label="Reliability curve">
            {curve.slice(0, 12).map((b, i) => (
              <span key={i}><i style={{ height: `${clamp(b.mean_prediction) * 100}%` }} /><em style={{ bottom: `${clamp(b.observed_frequency) * 100}%` }} /></span>
            ))}
          </div>
        ) : <Empty>No persisted labelled calibration evidence yet.</Empty>}
      </Panel>

      <Panel title={`Hawkes · ${selected}`} source="research"
        actions={<div className="segmented small" role="group" aria-label="Symbol">{SYMBOLS.map(s => <button key={s} type="button" aria-pressed={s === selected} onClick={() => select(s)}>{s}</button>)}</div>}>
        {hawkes ? (
          <>
            <div className="grid3">
              <Stat label="Intensity" value={num(hawkes.current_intensity, 4)} />
              <Stat label="Baseline" value={num(hawkes.baseline_intensity, 4)} />
              <Stat label="Branching" value={num(hawkes.branching_ratio, 4)} />
            </div>
            <Spark values={intensity} height={70} />
            <Stat label="Event probability" value={pct(hawkes.event_probability)} />
          </>
        ) : <Empty>Hawkes state unavailable for {selected}.</Empty>}
      </Panel>

      <Panel title="Synthetic greeks" source="research">
        {([["Δ Delta", greeks?.market_probability_delta], ["ν Vega", greeks?.volatility_vega], ["κ Kappa", greeks?.imbalance_kappa], ["θ Theta", greeks?.time_theta]] as const).map(([label, v]) => (
          <div className="greek" key={label}>
            <span>{label}</span><b className={tone(v)}>{num(v, 4)}</b><Meter value={Math.abs(v ?? 0)} label={label} />
          </div>
        ))}
      </Panel>

      <Panel title="P&L attribution" source="paper">
        <div className="grid4">
          <Stat label="Observed" value={usd(pnl?.observed_total_pnl)} className={tone(pnl?.observed_total_pnl)} />
          <Stat label="Fees" value={usd(pnl?.fees)} />
          <Stat label="Slippage" value={usd(pnl?.slippage)} />
          <Stat label="Residual" value={usd(pnl?.residual)} />
        </div>
      </Panel>
    </div>
  );
}
