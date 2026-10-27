import { useState } from "react";
import type { Analytics, Paper, Portfolio, Position } from "../api";
import { SYMBOLS } from "../api";
import { usePoll } from "../hooks";
import type { Shared } from "../main";
import { Button, Empty, Panel, Stat, num, tone, usd } from "../ui";

const field = (p: Position, ...keys: string[]) => {
  for (const k of keys) { const v = p[k]; if (typeof v === "number" && Number.isFinite(v)) return v; }
  return null;
};
const text = (p: Position, key: string) => (typeof p[key] === "string" ? (p[key] as string) : "—");

function Positions({ positions }: { positions: Position[] }) {
  if (!positions.length) return <Empty>No paper positions open.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Asset</th><th>Market</th><th>Side</th><th>Qty</th><th>Avg</th><th>Mark</th><th>Unrealized</th><th>Realized</th></tr></thead>
        <tbody>
          {positions.slice(0, 20).map((p, i) => {
            const u = field(p, "unrealized_pnl"), r = field(p, "realized_pnl");
            return (
              <tr key={String(p.market_id ?? i)}>
                <td>{text(p, "asset")}</td><td>{text(p, "market_id")}</td>
                <td className={text(p, "side").toLowerCase().includes("short") ? "down" : "up"}>{text(p, "side")}</td>
                <td>{num(field(p, "quantity", "qty"), 4)}</td><td>{num(field(p, "average_price", "avg_price"), 4)}</td>
                <td>{num(field(p, "mark_price", "mark"), 4)}</td>
                <td className={tone(u)}>{usd(u)}</td><td className={tone(r)}>{usd(r)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type StrategyStatus = { running: boolean; strategy_id: string; market_source: string; signals: number };
type DrillReport = { verdict: string; finished_at?: string; steps?: { step: string; ok: boolean }[] };

function NumberField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return <label className="field"><span>{label}</span><input value={value} inputMode="decimal" onChange={e => onChange(e.target.value)} /></label>;
}

export function Trade({ market, markets, selected, select }: Shared) {
  const paperPoll = usePoll<Paper>("/paper/status");
  // A failed read is not a stopped engine: unknown reads "—" and orders stay disabled.
  const paper = paperPoll.status === 200 ? paperPoll.data : null;
  // Which venue the autonomous chain really uses, from the floor state (paper venue or the executor's testnet).
  const floor = usePoll<{ data_mode?: string }>("/v1/floor/state", 15000);
  const dataMode = floor.status === 200 ? floor.data?.data_mode : undefined;
  const strategyPoll = usePoll<StrategyStatus>("/v1/strategy/status");
  const strategy = strategyPoll.status === 200 ? strategyPoll.data : null;
  const drillPoll = usePoll<DrillReport>("/v1/drills/e2e/latest");
  const drill = drillPoll.status === 200 ? drillPoll.data : null;
  const portfolioPoll = usePoll<Portfolio>("/v1/portfolio");
  const portfolio = portfolioPoll.status === 200 ? portfolioPoll.data : null;
  const analytics = usePoll<Analytics>(`/live/analytics/${selected}`).data;
  const [qty, setQty] = useState("0.001");

  const frame = market.frames[selected];
  const research = markets.find(m => m.symbol === selected);
  const quantity = Number(qty);
  const engineOn = Boolean(paper?.paper_execution_enabled);
  const canOrder = engineOn && frame && analytics && research && quantity > 0;

  const order = (side: "BUY" | "SELL") => frame && analytics && research ? {
    order: { market_id: research.market_id, asset: selected, side, quantity, limit_price: side === "BUY" ? frame.ask : frame.bid },
    snapshot: {
      symbol: selected, market_id: research.market_id, bid: frame.bid, ask: frame.ask, bid_size: frame.bid_size, ask_size: frame.ask_size,
      volatility: analytics.realized_volatility, imbalance: analytics.current_imbalance,
      market_probability: research.market_probability, observed_at: frame.timestamp,
    },
  } : undefined;

  return (
    <div className="trade">
      <Panel title="Paper engine" source="paper">
        <div className="grid4">
          <Stat label="Mode" value={paper?.mode ?? "—"} />
          <Stat label="Engine" value={!paper ? "—" : engineOn ? "RUNNING" : "STOPPED"} className={!paper ? "" : engineOn ? "up" : "warn"} />
          <Stat label="Autopilot" value={!paper ? "—" : paper.autopilot_running ? "RUNNING" : "STOPPED"} />
          <Stat label="Kill switch" value={paper?.kill_switch ?? "—"} />
        </div>
        <div className="row">
          <Button label="START PAPER" path="/paper/start" kind="primary" disabled={!paper || engineOn}>Start engine</Button>
          <Button label="STOP PAPER" path="/paper/stop" disabled={paper != null && !engineOn}>Stop engine</Button>
          <Button label="RESET SIMULATION" path="/simulation/reset">Reset session</Button>
        </div>
        {paperPoll.status !== null && !paper && <p className="hint warn">PAPER ENGINE STATUS UNAVAILABLE · {paperPoll.status || "network error"}</p>}
      </Panel>

      <Panel title="Manual order" source="paper">
        <div className="segmented" role="group" aria-label="Symbol">
          {SYMBOLS.map(s => <button key={s} type="button" aria-pressed={s === selected} onClick={() => select(s)}>{s}</button>)}
        </div>
        <div className="ticket">
          <strong>{usd(frame?.mid)}</strong>
          <span>bid {usd(frame?.bid)} · ask {usd(frame?.ask)}</span>
        </div>
        <NumberField label="Quantity" value={qty} onChange={setQty} />
        <div className="row split">
          <Button label={`BUY ${selected}`} path="/v1/simulate" body={order("BUY")} kind="buy" disabled={!canOrder}>Buy · paper</Button>
          <Button label={`SELL ${selected}`} path="/v1/simulate" body={order("SELL")} kind="sell" disabled={!canOrder}>Sell · paper</Button>
        </div>
        {!canOrder && <p className="hint">{!paper ? "Paper engine status unknown: orders stay disabled." : !engineOn ? "Start the paper engine to place orders." : !(quantity > 0) ? "Enter a quantity above zero." : "Needs a live quote and a research market for this symbol."}</p>}
      </Panel>

      <Panel title="Autonomy" source={dataMode === "TESTNET" ? "testnet" : dataMode ? "paper" : "unavailable"}>
        <ul className="floor-controls">
          <li><span>Own strategy</span><b className={strategy?.running ? "up" : "muted"}>{strategy == null ? "—" : strategy.running ? `on · ${strategy.strategy_id}` : "off"}</b></li>
          <li><span>Market source</span><b>{strategy?.market_source ?? "—"}</b></li>
          <li><span>Strategy signals</span><b>{strategy?.signals ?? "—"}</b></li>
          <li><span>Closed test</span><b className={drill?.verdict === "PASSED" ? "up" : drill?.verdict === "FAILED" ? "down" : "muted"}>{drill?.verdict ?? "—"}</b></li>
          {drill?.finished_at && <li><span>Last run</span><b>{drill.finished_at.slice(0, 19).replace("T", " ")}</b></li>}
        </ul>
        {drill?.steps && (
          <ol className="fl-steps">
            {drill.steps.map(s => <li key={s.step} className={s.ok ? "up" : "down"}>{s.ok ? "✓" : "✗"} {s.step}</li>)}
          </ol>
        )}
        <p className="hint">Signals from TradingView and the own strategy go through the agents, a Risk Engine permit and {dataMode === "TESTNET" ? "the executor on the Binance testnet" : dataMode ? "the paper venue" : "the configured venue (mode unavailable)"}. No real money.</p>
      </Panel>

      <Panel title="Positions" source="paper" className="wide">
        {portfolio ? <Positions positions={Array.isArray(portfolio.positions) ? portfolio.positions : []} /> : <Empty>{portfolioPoll.status === null ? "Reading the paper book…" : `PAPER BOOK UNAVAILABLE · ${portfolioPoll.status || "network error"}`}</Empty>}
      </Panel>
    </div>
  );
}
