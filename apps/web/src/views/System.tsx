import { useState } from "react";
import { type Creation, type Deployment, type Health, type Memory, type Orchestration, operatorToken, setOperatorToken, type Shadow, type Universe } from "../api";
import { usePoll } from "../hooks";
import { Button, Dot, Empty, Panel, Stat, useCommand } from "../ui";

const yes = (v: boolean | undefined, on = "READY", off = "NOT READY") => <span className={v ? "up" : "warn"}>{v ? on : off}</span>;

export function System({ health }: { health: Health | null }) {
  const deployment = usePoll<Deployment>("/deployment/info", 30000).data;
  const universe = usePoll<Universe>("/universe", 10000).data;
  const orchestration = usePoll<Orchestration>("/orchestration/status", 5000).data;
  const memory = usePoll<Memory>("/orchestration/decision-memory/status", 10000).data;
  const shadow = usePoll<Shadow>("/shadow/status", 5000).data;
  const creation = usePoll<Creation>("/creation/status", 10000).data;
  const { log } = useCommand();
  const [token, setToken] = useState(operatorToken);
  const [saved, setSaved] = useState(Boolean(operatorToken()));

  const save = (value: string) => { setOperatorToken(value.trim()); setSaved(Boolean(value.trim())); };

  return (
    <div className="system">
      <Panel title="Runtime">
        <div className="grid4">
          <Stat label="API" value={health?.status ?? "—"} className={health?.status === "ok" ? "up" : "warn"} />
          <Stat label="Version" value={health?.version ?? "—"} />
          <Stat label="Release" value={deployment?.ui_release ?? "—"} />
          <Stat label="Commit" value={deployment?.git_commit_sha?.slice(0, 8) ?? "local"} />
        </div>
      </Panel>

      <Panel title="Operator">
        <form className="row" onSubmit={e => { e.preventDefault(); save(token); }}>
          <label className="field grow"><span>Operator token</span>
            <input type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); setSaved(false); }} placeholder="Required for commands in production" />
          </label>
          <button type="submit" className="btn primary">Save</button>
          <button type="button" className="btn" onClick={() => { setToken(""); save(""); }}>Clear</button>
        </form>
        <p className="hint"><Dot on={saved} />{saved ? "Token stored for this browser session only." : "No token stored. Read-only views still work."}</p>
      </Panel>

      <Panel title="Autonomy">
        <div className="grid4">
          <Stat label="Safe scope" value={yes(orchestration?.readiness?.ready_safe_scope)} />
          <Stat label="Durable runtime" value={yes(orchestration?.readiness?.durable_safe_scope)} />
          <Stat label="Shadow engine" value={yes(shadow?.running, "RUNNING", "STOPPED")} />
          <Stat label="ProtoBrain" value={yes(creation?.orchestration_available, "AVAILABLE", "UNAVAILABLE")} />
        </div>
        <div className="row">
          <Button label="SHADOW START" path="/shadow/start" kind="primary" disabled={shadow?.running}>Start shadow</Button>
          <Button label="SHADOW STOP" path="/shadow/stop" disabled={!shadow?.running}>Stop shadow</Button>
        </div>
      </Panel>

      <Panel title="Decision memory">
        <div className="grid4">
          <Stat label="Configured" value={yes(memory?.configured, "YES", "NO")} />
          <Stat label="Records" value={memory?.records ?? 0} />
          <Stat label="Resolved" value={memory?.resolved ?? 0} />
          <Stat label="Unresolved" value={memory?.unresolved ?? 0} />
        </div>
        <p className="hint">Creation bridge: {creation?.configured ? "configured" : "not configured"} · {creation?.transport ?? "—"} · jobs {creation?.allowed_jobs?.join(", ") || "none"}</p>
      </Panel>

      <Panel title="Universe" className="wide">
        {universe?.instruments.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Instrument</th><th>Class</th><th>Venue</th><th>Provider</th><th>Data</th></tr></thead>
              <tbody>
                {universe.instruments.map(i => (
                  <tr key={i.instrument_id}>
                    <td>{i.instrument_id}</td><td>{i.asset_class}</td><td>{i.venue} · {i.currency}</td>
                    <td>{i.coverage.market_data_provider ?? "none"}</td>
                    <td className={i.coverage.currently_fresh ? "up" : "warn"}>{i.coverage.currently_fresh ? "FRESH" : "CATALOG"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No instruments registered.</Empty>}
        <p className="hint">Catalog membership never implies executable connectivity.</p>
      </Panel>

      <Panel title="Operator log" className="wide">
        {log.length ? (
          <ol className="log">
            {log.map((e, i) => <li key={`${e.at}-${i}`} className={e.ok ? "ok" : "fail"}><time>{e.at.slice(11, 19)}</time><b>{e.label}</b><span>{e.detail}</span></li>)}
          </ol>
        ) : <Empty>No commands issued in this browser session.</Empty>}
      </Panel>
    </div>
  );
}
