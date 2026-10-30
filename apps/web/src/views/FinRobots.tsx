import { usePoll } from "../hooks";
import { Empty, Panel, Stat, useCommand } from "../ui";
import { bool, isRecord, num, orDash, rec, short, str, strs } from "./deskData";

// FinRobots: the eight specialist analysts (docs/finrobots/contract.md), their latest cycle and the
// decision council's verdict per instrument. Advisory only: a council verdict can only argue for less
// risk; the Risk Engine and the OMS still decide every order. An unbound specialist is shown UNBOUND,
// never as working, and a failed read hides the previous data.

type Robot = { id: string; title: string; mandate: string; status: string; veto: boolean | null; impl: string | null; owner: string };
type Evidence = { source: string; provenance: string; summary: string; at: string | null };
type Report = { id: string; robot: string; instrument: string | null; stance: string; confidence: number | null; headline: string; rationale: string[]; evidence: Evidence[] };
type Scope = { scope: string; verdict: string; reasons: string[]; votes: [string, string][]; evidence: Evidence[] };
type Registry = { robots: Robot[]; enabled: boolean | null; running: boolean | null; bound: number | null; cycles: number | null; interval: number | null; lastAt: string | null; errors: [string, string][]; sources: Source[] };
type Source = { name: string; status: string; provenance: string; error: string | null };

const when = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 19) : "—");
const VERDICT_TONE: Record<string, string> = { BLOCK_NEW_RISK: "down", REDUCE_RISK: "warn", CONSISTENT: "up", NO_CONSENSUS: "muted" };
const STANCE_TONE: Record<string, string> = { VETO: "down", CAUTION: "warn", SUPPORT: "up", ABSTAIN: "muted" };

function parseEvidence(raw: unknown): Evidence[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap(e => (isRecord(e) && str(e.source) && str(e.summary)
    ? [{ source: str(e.source)!, provenance: str(e.provenance) ?? "UNAVAILABLE", summary: str(e.summary)!, at: str(e.observed_at) }]
    : []));
}

function parseRegistry(raw: unknown): Registry | null {
  if (!isRecord(raw) || !Array.isArray(raw.robots) || raw.authority !== "ADVISORY_ONLY" || raw.real_money_execution !== false) return null;
  const last = rec(raw.last_cycle);
  return {
    robots: raw.robots.flatMap(r => (isRecord(r) && str(r.robot_id)
      ? [{ id: str(r.robot_id)!, title: str(r.title) ?? str(r.robot_id)!, mandate: str(r.mandate) ?? "—", status: str(r.status) ?? "UNBOUND", veto: bool(r.may_veto), impl: str(r.implementation), owner: str(r.engine_owner) ?? "—" }]
      : [])),
    enabled: bool(raw.enabled), running: bool(raw.running), bound: num(raw.bound), cycles: num(raw.cycles), interval: num(raw.interval_seconds),
    lastAt: str(last.finished_at), errors: Object.entries(rec(last.errors)).flatMap(([k, v]) => (typeof v === "string" ? [[k, v] as [string, string]] : [])),
    sources: Object.entries(rec(last.sources)).flatMap(([name, v]) => (isRecord(v)
      ? [{ name, status: str(v.status) ?? "UNAVAILABLE", provenance: str(v.provenance) ?? "UNAVAILABLE", error: str(v.error) }]
      : [])),
  };
}

function parseCouncil(raw: unknown): { scopes: Scope[]; mode: string } | null {
  if (!isRecord(raw) || raw.real_money_execution !== false) return null;
  const council = rec(raw.council);
  const scopes = Array.isArray(council.scopes) ? council.scopes.flatMap(s => (isRecord(s) && str(s.scope) && str(s.verdict)
    ? [{ scope: str(s.scope)!, verdict: str(s.verdict)!, reasons: strs(s.reasons) ?? [], votes: Object.entries(rec(s.votes)).flatMap(([k, v]) => (typeof v === "string" ? [[k, v] as [string, string]] : [])), evidence: parseEvidence(s.evidence) }]
    : [])) : [];
  return { scopes, mode: str(raw.data_mode) ?? "UNAVAILABLE" };
}

function parseReports(raw: unknown): Report[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.cycles)) return null;
  const latest = raw.cycles[0];
  if (!isRecord(latest) || !Array.isArray(latest.reports)) return [];
  return latest.reports.flatMap(r => (isRecord(r) && str(r.report_id) && str(r.robot_id)
    ? [{ id: str(r.report_id)!, robot: str(r.robot_id)!, instrument: str(r.instrument_id), stance: str(r.stance) ?? "ABSTAIN", confidence: num(r.confidence), headline: str(r.headline) ?? "—", rationale: strs(r.rationale) ?? [], evidence: parseEvidence(r.evidence) }]
    : []));
}

function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return <span className="muted">no evidence cited</span>;
  return <ul className="agent-evidence">{items.map((e, i) => <li key={`${e.source}-${i}`}>
    <span className="tag research">{e.provenance}</span> <b>{e.source}</b> · {e.summary} <time>{when(e.at)}</time>
  </li>)}</ul>;
}

/** FinRobot registry, council and evidence. Running a cycle only reads and journals. */
export function FinRobots() {
  const registry = usePoll<unknown>("/v1/finrobots", 15000);
  const council = usePoll<unknown>("/v1/finrobots/council/latest", 15000);
  const cycles = usePoll<unknown>("/v1/finrobots/cycles?limit=1", 15000);
  const { busy, run } = useCommand();

  const view = registry.status === 200 ? parseRegistry(registry.data) : null;
  const verdicts = council.status === 200 ? parseCouncil(council.data) : null;
  const reports = cycles.status === 200 ? parseReports(cycles.data) : null;
  const runCycle = async () => {
    if (await run("Run FinRobot cycle", "/v1/finrobots/cycles")) { registry.refresh(); council.refresh(); cycles.refresh(); }
  };

  return <>
    <Panel title="FinRobots" className="agent-span" actions={view?.enabled
      ? <button type="button" className="btn" disabled={busy !== null} onClick={runCycle}>Run analysis cycle</button>
      : undefined}>
      <div className="agent-tags">
        <span className="tag research">ADVISORY ONLY · NO ORDER AUTHORITY</span>
        <span className="tag research">REAL MONEY EXECUTION OFF</span>
        {verdicts && <span className="tag paper">{verdicts.mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : verdicts.mode === "PAPER" ? "PAPER / SIM" : verdicts.mode}</span>}
      </div>
      {registry.status !== 200 && <p role="status" className="agent-read">{registry.status === null ? "Reading FinRobots…" : `FINROBOTS UNAVAILABLE · ${registry.status || "network error"}. Previous data is hidden.`}</p>}
      {registry.status === 200 && !view && <Empty>FINROBOTS UNAVAILABLE · unexpected payload.</Empty>}
      {view && <>
        <div className="grid4 agent-kpis">
          <Stat label="Specialists bound" value={`${orDash(view.bound)} / ${view.robots.length}`} className={view.bound ? "" : "warn"} />
          <Stat label="Scheduler" value={view.enabled === null ? "—" : !view.enabled ? "DISABLED" : view.running ? `RUNNING · ${orDash(view.interval)} s` : "STOPPED"} />
          <Stat label="Cycles since API start" value={orDash(view.cycles)} />
          <Stat label="Last cycle" value={when(view.lastAt)} />
        </div>
        <div className="table-wrap"><table className="agent-table">
          <thead><tr><th>Specialist</th><th>Mandate</th><th>Veto</th><th>Engine</th></tr></thead>
          <tbody>{view.robots.map(r => <tr key={r.id}>
            <td>{r.title}</td><td>{r.mandate}</td><td>{r.veto === null ? "—" : r.veto ? "may veto" : "no"}</td>
            <td className={r.status === "BOUND" ? "up" : "muted"}>{r.status === "BOUND" ? orDash(r.impl) : `UNBOUND · ${r.owner}`}</td>
          </tr>)}</tbody>
        </table></div>
        {view.errors.length > 0 && <p className="agent-note warn">Last cycle errors: {view.errors.map(([k, v]) => `${k} (${v})`).join(" · ")}</p>}
        {view.sources.length > 0 && <details className="agent-sources">
          <summary>Evidence sources of the last cycle · {view.sources.filter(s => s.status === "OK" && s.provenance !== "UNAVAILABLE").length}/{view.sources.length} with data</summary>
          <ul className="agent-evidence">{view.sources.map(s => <li key={s.name}>
            <span className="tag research">{s.provenance}</span> <b>{s.name}</b> · <span className={s.status !== "OK" ? "warn" : s.provenance === "UNAVAILABLE" ? "muted" : "up"}>{s.status === "OK" && s.provenance === "UNAVAILABLE" ? "OK · no data" : s.status}</span>{s.error ? ` · ${s.error}` : ""}
          </li>)}</ul>
        </details>}
      </>}
      <small className="agent-src">Source: /v1/finrobots · an UNBOUND specialist has no engine yet and reports nothing.</small>
    </Panel>

    <Panel title="Decision council">
      <span className="tag research">ADVISORY · CAN ONLY ARGUE FOR LESS RISK</span>
      {council.status !== 200 && <p role="status" className="agent-read">{council.status === null ? "Reading council…" : `COUNCIL UNAVAILABLE · ${council.status || "network error"}.`}</p>}
      {verdicts && !verdicts.scopes.length && <Empty>No council verdict yet: no cycle has produced reports.</Empty>}
      {verdicts && verdicts.scopes.length > 0 && <ul className="agent-decisions">{verdicts.scopes.map(s => <li key={s.scope}>
        <details>
          <summary><b className={VERDICT_TONE[s.verdict] ?? ""}>{s.verdict}</b><span>{s.scope === "SYSTEM" ? "SYSTEM" : short(s.scope)}</span>
            <span>{s.votes.map(([robot, stance]) => `${robot}: ${stance}`).join(" · ") || "no votes"}</span></summary>
          <dl className="agent-dl">
            <dt>Reasons</dt><dd>{s.reasons.join(" · ") || "—"}</dd>
            <dt>Shared evidence</dt><dd><EvidenceList items={s.evidence} /></dd>
          </dl>
        </details>
      </li>)}</ul>}
      <small className="agent-src">Source: /v1/finrobots/council/latest · the Risk Engine permit and the OMS still decide every order.</small>
    </Panel>

    <Panel title="Specialist reports">
      <span className="tag research">LATEST CYCLE · EVIDENCE</span>
      {cycles.status !== 200 && <p role="status" className="agent-read">{cycles.status === null ? "Reading reports…" : `REPORTS UNAVAILABLE · ${cycles.status || "network error"}.`}</p>}
      {reports && !reports.length && <Empty>No report in the latest cycle.</Empty>}
      {reports && reports.length > 0 && <ul className="agent-decisions">{reports.map(r => <li key={r.id}>
        <details>
          <summary><b className={STANCE_TONE[r.stance] ?? ""}>{r.stance}</b><span>{r.robot}</span><span>{r.instrument ? short(r.instrument) : "SYSTEM"} · conf {r.confidence === null ? "—" : r.confidence.toFixed(2)}</span><span>{r.headline}</span></summary>
          <dl className="agent-dl">
            <dt>Rationale</dt><dd>{r.rationale.join(" · ") || "—"}</dd>
            <dt>Evidence</dt><dd><EvidenceList items={r.evidence} /></dd>
          </dl>
        </details>
      </li>)}</ul>}
      <small className="agent-src">Source: /v1/finrobots/cycles · ChatGPT reads the same data through the read-only MCP connector.</small>
    </Panel>
  </>;
}
