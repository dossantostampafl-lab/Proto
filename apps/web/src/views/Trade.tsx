import { useState } from "react";
import type { Analytics, Autopilot, Paper, Portfolio, Position } from "../api";
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

function NumberField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return <label className="field"><span>{label}</span><input value={value} inputMode="decimal" onChange={e => onChange(e.target.value)} /></label>;
}

export function Trade({ market, markets, selected, select }: Shared) {
  const paper = usePoll<Paper>("/paper/status").data;
  const autopilot = usePoll<Autopilot>("/paper/automation/status").data;
  const portfolio = usePoll<Portfolio>("/v1/portfolio").data;
  const analytics = usePoll<Analytics>(`/live/analytics/${selected}`).data;
  const [qty, setQty] = useState("0.001");
  const [trigger, setTrigger] = useState("0.65");
  const [cooldown, setCooldown] = useState("20");
  const [spread, setSpread] = useState("20");
  const [stopLoss, setStopLoss] = useState("0.02");

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
          <Stat label="Engine" value={engineOn ? "RUNNING" : "STOPPED"} className={engineOn ? "up" : "warn"} />
          <Stat label="Autopilot" value={paper?.autopilot_running ? "RUNNING" : "STOPPED"} />
          <Stat label="Kill switch" value={paper?.kill_switch ?? "—"} />
        </div>
        <div className="row">
          <Button label="START PAPER" path="/paper/start" kind="primary" disabled={engineOn}>Start engine</Button>
          <Button label="STOP PAPER" path="/paper/stop" disabled={!engineOn}>Stop engine</Button>
          <Button label="RESET SIMULATION" path="/simulation/reset">Reset session</Button>
        </div>
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
        {!canOrder && <p className="hint">{engineOn ? "Needs a live quote and a research market for this symbol." : "Start the paper engine to place orders."}</p>}
      </Panel>

      <Panel title="Autopilot" source="paper">
        <div className="grid3">
          <NumberField label="Imbalance trigger" value={trigger} onChange={setTrigger} />
          <NumberField label="Cooldown (s)" value={cooldown} onChange={setCooldown} />
          <NumberField label="Max spread (bp)" value={spread} onChange={setSpread} />
          <NumberField label="Stop-loss fraction" value={stopLoss} onChange={setStopLoss} />
          <NumberField label="Quantity" value={qty} onChange={setQty} />
        </div>
        <div className="row">
          <Button label="START AUTOPILOT" path="/paper/automation/start" kind="primary" disabled={!engineOn}
            body={{ symbol: selected, imbalance_trigger: Number(trigger), cooldown_seconds: Number(cooldown), quantity, max_spread_bps: Number(spread), stop_loss_fraction: Number(stopLoss) }}>
            Start / update
          </Button>
          <Button label="STOP AUTOPILOT" path="/paper/automation/stop" disabled={!autopilot?.running}>Stop</Button>
        </div>
        <p className="hint">
          {autopilot?.last_reason ?? "—"} · cycles {autopilot?.counters?.cycles ?? 0} · signals {autopilot?.counters?.signals ?? 0}
          {" "}· accepted {autopilot?.counters?.accepted ?? 0} · rejected {autopilot?.counters?.rejected ?? 0}
        </p>
      </Panel>

      <Panel title="Positions" source="paper" className="wide">
        <Positions positions={Array.isArray(portfolio?.positions) ? portfolio.positions : []} />
      </Panel>
    </div>
  );
}
