import type { Analytics, Portfolio, Sym } from "../api";
import { SYMBOLS } from "../api";
import { usePoll } from "../hooks";
import type { Shared } from "../main";
import { Button, clamp, Empty, Orbit, Panel, pct, Spark, Stat, num, signed, tone, usd } from "../ui";

function Tile({ symbol, active, onSelect, mid, series }: { symbol: Sym; active: boolean; onSelect: () => void; mid: number | undefined; series: number[] }) {
  const a = usePoll<Analytics>(`/live/analytics/${symbol}`).data;
  return (
    <button type="button" className={`tile ${active ? "active" : ""}`} aria-pressed={active} onClick={onSelect}>
      <span className="tile-head"><b>{symbol}</b><small>/USD</small><em className={tone(a?.simple_return)}>{pct(a?.simple_return)}</em></span>
      <strong>{usd(mid)}</strong>
      <span className="tile-foot">spread {a ? `${num(a.current_spread_bps, 2)} bp` : "—"}</span>
      <Spark values={series.slice(-60)} height={34} />
    </button>
  );
}

export function Command({ market, markets, marketsStatus, runtime, selected, select }: Shared) {
  const frame = market.frames[selected];
  const analytics = usePoll<Analytics>(`/live/analytics/${selected}`).data;
  const portfolio = usePoll<Portfolio>("/v1/portfolio").data;
  const research = markets.filter(m => m.symbol === selected);
  const lead = research[0];
  // Display-only composite (not a canonical risk limit): concentration and drawdown vs exposure.
  const displayRisk = clamp(Math.max(
    portfolio?.max_asset_concentration ?? 0,
    Math.abs(portfolio?.realized_drawdown ?? 0) / Math.max(Math.abs(portfolio?.gross_exposure ?? 1), 1),
  ));
  const armed = runtime?.kill_switch === "ARMED";

  return (
    <div className="command">
      <div className="tiles">
        {SYMBOLS.map(s => (
          <Tile key={s} symbol={s} active={s === selected} onSelect={() => select(s)} mid={market.frames[s]?.mid} series={market.series[s]} />
        ))}
      </div>

      <Panel title={`${selected}/USD`} source="live" className="hero">
        <div className="hero-quote">
          <strong>{usd(frame?.mid)}</strong>
          <span>bid {usd(frame?.bid)}</span><span>ask {usd(frame?.ask)}</span>
          <span className="muted">{market.connected ? "streaming" : "REST reconciliation"}</span>
        </div>
        <Spark values={market.series[selected]} height={200} className="hero-chart" grid />
      </Panel>

      <Panel title="Probability orbit" source="research" className="orbit-panel">
        {research.length ? (
          <>
            <Orbit selected={selected} markets={markets.map(m => ({ id: m.market_id, symbol: m.symbol, market: m.market_probability, model: m.model_probability, horizon: m.expiry_horizon_minutes, edge: m.net_edge }))} />
            <p className="legend"><i className="market" />market P <i className="model" />model P · radius = expiry</p>
            <div className="grid2">
              <Stat label="Market P" value={pct(lead?.market_probability, 1)} />
              <Stat label="Model P" value={pct(lead?.model_probability, 1)} />
              <Stat label="Net edge" value={signed(lead?.net_edge)} className={tone(lead?.net_edge)} />
              <Stat label="Decision" value={lead?.edge_decision ?? "—"} />
            </div>
          </>
        ) : (
          <Empty>{marketsStatus === 503 ? "Synthetic research is disabled in this deployment." : "No research markets for this symbol."} Live telemetry is unaffected.</Empty>
        )}
      </Panel>

      <Panel title="L1 microstructure" source="live" className="micro">
        <div className="imbalance" aria-label="Order-book imbalance">
          <i style={{ left: `${clamp(((analytics?.current_imbalance ?? 0) + 1) / 2) * 100}%` }} />
        </div>
        <div className="grid3">
          <Stat label="Imbalance" value={num(analytics?.current_imbalance, 3)} className={tone(analytics?.current_imbalance)} />
          <Stat label="Microprice" value={usd(analytics?.current_microprice)} />
          <Stat label="Realized vol" value={pct(analytics?.realized_volatility)} />
          <Stat label="Bid size" value={num(frame?.bid_size, 4)} />
          <Stat label="Ask size" value={num(frame?.ask_size, 4)} />
          <Stat label="Samples" value={analytics?.sample_count ?? "—"} />
        </div>
      </Panel>

      <Panel title="Paper book" source="paper" className="book">
        <div className="grid4">
          <Stat label="P&L after fees" value={usd(portfolio?.total_pnl_after_fees)} className={`big ${tone(portfolio?.total_pnl_after_fees)}`} />
          <Stat label="Gross exposure" value={usd(portfolio?.gross_exposure)} />
          <Stat label="Net exposure" value={usd(portfolio?.net_exposure)} />
          <Stat label="Open positions" value={portfolio?.open_position_count ?? "—"} />
          <Stat label="Display risk score" value={pct(displayRisk, 0)} />
          <Stat label="Kill switch" value={runtime?.kill_switch ?? "—"} className={armed ? "up" : "down"} />
        </div>
        <div className="row">
          <Button label="KILL SWITCH" path="/killswitch/trigger" kind="danger" disabled={!armed}>Trigger kill switch</Button>
          <Button label="RESET KILL SWITCH" path="/killswitch/reset" disabled={armed}>Re-arm</Button>
        </div>
      </Panel>
    </div>
  );
}
