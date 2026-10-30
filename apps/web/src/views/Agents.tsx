import { useState } from "react";
import { usePoll } from "../hooks";
import { Empty, Panel, Stat } from "../ui";
import { bool, finiteDecimal, fmt2, isRecord, num, orDash, parseDecisions, rec, short, str, strs } from "./deskData";
import "./agents.css";
import { AgentConversation } from "./AgentConversation";

// Agents dashboard: the trader roster, each agent's virtual book, the agents' latest decisions on
// its instrument and the orchestration contracts. Read only: no prompts, credentials, job submission
// or financial commands. Every payload is parsed defensively; an odd field becomes "—", an odd
// payload an UNAVAILABLE panel, and data from a failed read is never shown as current.

type AgentRow = {
  id: string; name: string; status: string; instrument: string; kind: string; generation: number | null;
  createdAt: string | null; eliminatedAt: string | null; eliminationReason: string | null;
  capital: string | null; equity: string | null; pnl: string | null; pnlQuote: string | null; unrealized: string | null;
  mark: string | null; valuation: string | null; survival: string | null; params: Record<string, unknown>;
  position: string | null; cost: string | null; realized: string | null; fees: string | null;
};
type Totals = { agents: number | null; active: number | null; eliminated: number | null; allocated: string | null; reference: string | null; unallocated: string | null; equity: string | null };
type RosterView = { agents: AgentRow[]; totals: Totals; enabled: boolean | null; synced: boolean | null; mode: string };
type Contract = { name: string; capability: string; owner: string; modes: string[]; riskGate: boolean | null };
function parseAgent(raw: unknown): AgentRow | null {
  if (!isRecord(raw) || !isRecord(raw.slot)) return null;
  const id = str(raw.agent_id), instrument = str(raw.slot.instrument_id), status = str(raw.status);
  if (!id || !instrument || !status) return null;
  const book = rec(raw.book);
  return {
    id, name: str(raw.name) ?? id, status, instrument, kind: str(raw.slot.kind) ?? "—", generation: num(raw.generation),
    createdAt: str(raw.created_at), eliminatedAt: str(raw.eliminated_at), eliminationReason: str(raw.elimination_reason),
    capital: str(raw.capital_brl), equity: str(raw.equity_brl), pnl: str(raw.pnl_brl), pnlQuote: str(raw.pnl_quote),
    unrealized: str(raw.unrealized_pnl_quote), mark: str(raw.mark), valuation: str(raw.valuation), survival: str(raw.survival_multiplier),
    params: rec(raw.params), position: str(book.position_quantity), cost: str(book.position_cost), realized: str(book.realized_pnl), fees: str(book.fees),
  };
}

function parseRoster(raw: unknown): RosterView | null {
  if (!isRecord(raw) || !Array.isArray(raw.agents) || raw.capital !== "VIRTUAL") return null;
  const t = rec(raw.totals);
  return {
    agents: raw.agents.map(parseAgent).filter((a): a is AgentRow => a !== null),
    totals: {
      agents: num(t.agents), active: num(t.active), eliminated: num(t.eliminated), allocated: str(t.allocated_capital_brl),
      reference: str(t.reference_capital_brl), unallocated: str(t.unallocated_capital_brl), equity: str(t.active_equity_brl),
    },
    enabled: bool(raw.enabled), synced: bool(raw.fills_synced), mode: str(raw.data_mode) ?? "UNAVAILABLE",
  };
}

function parseContracts(raw: unknown): { contracts: Contract[]; handlers: string[]; running: boolean | null } | null {
  if (!isRecord(raw) || !Array.isArray(raw.contracts)) return null;
  const supervisor = rec(raw.supervisor);
  const contracts = raw.contracts.flatMap(c => {
    if (!isRecord(c) || !str(c.name)) return [];
    return [{ name: str(c.name)!, capability: str(c.capability) ?? "—", owner: str(c.owner_domain) ?? "—", modes: strs(c.allowed_modes) ?? [], riskGate: bool(c.requires_risk_gate) }];
  });
  return { contracts, handlers: strs(supervisor.registered_jobs) ?? [], running: bool(supervisor.running) };
}

const brl = (v: string | null) => (fmt2(v) === "—" ? "R$ —" : `R$ ${fmt2(v)}`);
const signTone = (v: string | null) => { const d = finiteDecimal(v); return d === null || !/[1-9]/.test(d) ? "" : d.startsWith("-") ? "down" : "up"; };
const when = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 19) : "—");
const kindLabel = (kind: string) => (kind === "tradingview" ? "TradingView" : kind === "—" ? kind : `own strategy (${kind})`);
const modeTag = (mode: string) => (mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : mode === "PAPER" ? "PAPER / SIM" : `${mode} · VIRTUAL CAPITAL`);

function ReadState({ status, what }: { status: number | null; what: string }) {
  if (status === 200) return null;
  return <p role="status" className="agent-read">{status === null ? `Reading ${what}…` : `${what.toUpperCase()} UNAVAILABLE · ${status || "network error"}. Previous data is hidden.`}</p>;
}

/** Agents dashboard. Evidence only: selecting an agent never runs it. */
export function Agents() {
  const roster = usePoll<unknown>("/v1/agents/roster", 15000);
  const catalog = usePoll<unknown>("/orchestration/status", 30000);
  const [selected, setSelected] = useState("");

  // usePoll keeps the last good value after a failure; a failed read hides it instead.
  const view = roster.status === 200 ? parseRoster(roster.data) : null;
  const agents = view?.agents ?? [];
  const agent = agents.find(a => a.id === selected) ?? null;
  const orchestration = catalog.status === 200 ? parseContracts(catalog.data) : null;

  const instrument = agent?.instrument ?? null;
  const recent = usePoll<unknown>(`/v1/agents/decisions/recent?limit=20${instrument ? `&instrument_id=${encodeURIComponent(instrument)}` : ""}`, 10000);
  const decisions = recent.status === 200 ? parseDecisions(recent.data) : null;
  const t = view?.totals;

  return <div className="agent-dash">
    <Panel title="Trader agents" className="agent-span">
      <div className="agent-tags">
        <span className="tag paper">{view ? modeTag(view.mode) : "ROSTER UNAVAILABLE"}</span>
        <span className="tag research">FINANCIAL CONNECTIVITY OFF</span>
        <span className="tag research">REAL MONEY EXECUTION OFF</span>
      </div>
      <ReadState status={roster.status} what="roster" />
      {roster.status === 200 && !view && <Empty>ROSTER UNAVAILABLE · unexpected payload.</Empty>}
      {view && <div className="grid4 agent-kpis">
        <Stat label="Agents · active / eliminated" value={`${orDash(t?.agents)} · ${orDash(t?.active)} / ${orDash(t?.eliminated)}`} />
        <Stat label="Active equity (virtual)" value={brl(t?.equity ?? null)} />
        <Stat label="Allocated / reference" value={`${brl(t?.allocated ?? null)} / ${brl(t?.reference ?? null)}`} />
        <Stat label="Roster · fills" value={`${view.enabled === null ? "—" : view.enabled ? "ENABLED" : "DISABLED"} · ${view.synced ? "SYNCED" : "NOT SYNCED"}`} className={view.synced ? "" : "warn"} />
      </div>}
    </Panel>

    <Panel title="Roster" className="agent-span">
      {view && !agents.length && <Empty>No registered agents.</Empty>}
      {agents.length > 0 && <div className="table-wrap"><table className="agent-table">
        <thead><tr><th>Agent</th><th>Slot</th><th>Status</th><th>Equity</th><th>P&amp;L</th><th>Position</th><th>Realized</th><th>Valuation</th></tr></thead>
        <tbody>{agents.map(a => <tr key={a.id} className={a.id === selected ? "selected" : ""}>
          <td><button type="button" className="agent-pick" aria-pressed={a.id === selected} onClick={() => setSelected(a.id === selected ? "" : a.id)}>{a.id}</button></td>
          <td>{short(a.instrument)} · {kindLabel(a.kind)}</td>
          <td className={a.status === "ACTIVE" ? "up" : "down"}>{a.status}</td>
          <td>{brl(a.equity)}</td>
          <td className={signTone(a.pnl)}>{brl(a.pnl)}</td>
          <td>{fmt2(a.position)}</td>
          <td className={signTone(a.realized)}>{fmt2(a.realized)}</td>
          <td className={a.valuation === "MARKED" ? "" : "warn"}>{orDash(a.valuation)}</td>
        </tr>)}</tbody>
      </table></div>}
      <small className="agent-src">Source: /v1/agents/roster · virtual capital per agent, no real money allocated. Select an agent to inspect it.</small>
    </Panel>

    <Panel title={agent ? `Agent · ${agent.name}` : "Agent detail"}>
      {!agent && <Empty>Select an agent in the roster.</Empty>}
      {agent && <>
        <div className="grid2">
          <Stat label="Identity" value={agent.id} />
          <Stat label="Generation" value={orDash(agent.generation)} />
          <Stat label="Instrument" value={agent.instrument} />
          <Stat label="Strategy slot" value={kindLabel(agent.kind)} />
          <Stat label="Starting capital" value={brl(agent.capital)} />
          <Stat label="Survival multiplier" value={orDash(agent.survival)} />
          <Stat label="Mark" value={fmt2(agent.mark)} />
          <Stat label="Unrealized (quote)" value={fmt2(agent.unrealized)} className={signTone(agent.unrealized)} />
          <Stat label="Position cost" value={fmt2(agent.cost)} />
          <Stat label="Fees" value={fmt2(agent.fees)} />
        </div>
        <dl className="agent-dl">
          <dt>Created</dt><dd>{when(agent.createdAt)}</dd>
          {agent.eliminatedAt && <><dt>Eliminated</dt><dd className="down">{when(agent.eliminatedAt)} · {orDash(agent.eliminationReason)}</dd></>}
          <dt>Parameters</dt><dd>{Object.keys(agent.params).length ? Object.entries(agent.params).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ") : "none"}</dd>
          <dt>Conversation</dt><dd>Direct trader execution is unavailable. ChatGPT can explain recorded evidence when configured locally.</dd>
        </dl>
        <AgentConversation key={agent.id} agentId={agent.id} />
      </>}
    </Panel>

    <Panel title="Agent decisions">
      <span className="tag research">LATEST DECISIONS · SINCE THE LAST API START</span>
      <ReadState status={recent.status} what="agent decisions" />
      <p className="agent-note">{instrument
        ? `Latest 20 decisions on ${instrument}. Every decision is also kept in the operational journal.`
        : "Latest 20 decisions across all instruments. Select an agent to narrow to its instrument."}</p>
      {recent.status === 200 && !decisions && <Empty>AGENT DECISIONS UNAVAILABLE · unexpected payload.</Empty>}
      {decisions && !decisions.length && <Empty>No decision yet since the last API start.</Empty>}
      {decisions && decisions.length > 0 && <ul className="agent-decisions">{decisions.map(d => <li key={d.key}>
        <details>
          <summary>
            <b>{orDash(d.outcome)}</b><time>{when(typeof d.at === "string" ? d.at : null)}</time>
            <span>{short(d.instrument ?? "—")} · {orDash(d.direction)} · score {d.score === null ? "—" : d.score.toFixed(2)}</span>
            <span className={d.abstain ? "muted" : ""}>{d.abstain ?? (d.candidate ? `${orDash(d.candidate.side)} ${orDash(d.candidate.quantity)} @ ${orDash(d.candidate.limit)}` : "—")}</span>
          </summary>
          <dl className="agent-dl">
            <dt>Fusion</dt><dd>{d.hasFusion ? orDash(d.reason) : "—"}</dd>
            <dt>Order proposed</dt><dd>{d.candidate ? `${orDash(d.candidate.side)} ${orDash(d.candidate.quantity)} @ ${orDash(d.candidate.limit)}` : "none"}</dd>
            <dt>Reviewer</dt><dd>{d.review ? `${orDash(d.review.verdict)} · size ×${d.review.sizeFactor ?? "—"} · ${orDash(d.review.rationale)}` : "none"}</dd>
            <dt>Abstained because</dt><dd>{orDash(d.abstain)}</dd>
          </dl>
        </details>
      </li>)}</ul>}
      <small className="agent-src">Source: /v1/agents/decisions/recent · the Risk Engine and the OMS still decide every order.</small>
    </Panel>

    <Panel title="Orchestration contracts">
      <span className="tag research">REGISTERED CONTRACTS · NOT EXECUTION RESULTS</span>
      <ReadState status={catalog.status} what="orchestration" />
      {orchestration && <p className="agent-note">Supervisor {orchestration.running === null ? "—" : orchestration.running ? "running" : "stopped"} · {orchestration.handlers.length} runtime handler(s) registered.</p>}
      {orchestration && !orchestration.contracts.length && <Empty>No registered contracts.</Empty>}
      {orchestration && orchestration.contracts.length > 0 && <div className="table-wrap"><table>
        <thead><tr><th>Contract</th><th>Domain</th><th>Capability</th><th>Risk gate</th><th>Handler</th></tr></thead>
        <tbody>{orchestration.contracts.map(c => <tr key={c.name}>
          <td>{c.name}</td><td>{c.owner}</td><td>{c.capability}</td>
          <td>{c.riskGate === null ? "—" : c.riskGate ? "required" : "no"}</td>
          <td className={orchestration.handlers.includes(c.name) ? "up" : "muted"}>{orchestration.handlers.includes(c.name) ? "registered" : "none"}</td>
        </tr>)}</tbody>
      </table></div>}
      <small className="agent-src">Source: /orchestration/status. A contract is not an agent or a successful run.</small>
    </Panel>

    <Panel title="Conversation & research readiness">
      <span className="tag research">CAPABILITY INVENTORY · READ ONLY</span>
      <dl className="agent-dl">
        <dt>Agent conversation</dt><dd>Explanation only, opt-in: works on a private loopback host with the operator token (public hosts connect through an SSH tunnel). Selecting an agent does not run it.</dd>
        <dt>ChatGPT sign-in</dt><dd>Opt-in private local OAuth and account-owned history; disabled by default. No paid API fallback is used.</dd>
        <dt>FinRobot research</dt><dd>Original opt-in worker only (research, earnings, DCF, comps, IC memo, LBO). Not launched from the dashboard.</dd>
      </dl>
      <small className="agent-src">Gates: docs/research/CHATGPT_CONVERSATION_AUDIT.md · <a href="https://developers.openai.com/siwc/token-sharing-open-source" target="_blank" rel="noopener noreferrer">ChatGPT plan integration requirements</a></small>
    </Panel>
  </div>;
}
