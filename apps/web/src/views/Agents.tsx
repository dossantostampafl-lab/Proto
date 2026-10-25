import { useState } from "react";
import { usePoll } from "../hooks";
import { Empty, Panel } from "../ui";
import "./agents.css";
import { AgentConversation } from "./AgentConversation";

type Agent = {
  agent_id: string;
  name: string;
  status: string;
  slot: { instrument_id: string; kind: string };
};
type Roster = { agents: Agent[]; enabled: boolean; data_mode: string; fills_synced: boolean };
type Catalog = {
  contracts: { name: string; capability: string; owner_domain: string }[];
  supervisor: { registered_jobs: string[] };
};
type Decisions = { configured: boolean; decisions: unknown[] };

function ReadState({ status }: { status: number | null }) {
  return <p role="status">{status === null ? "Checking source…" : status === 200
    ? "Source available" : `Unavailable · ${status || "network error"}. Previous data is hidden.`}</p>;
}

/** Evidence browser only. No prompts, credentials, job submission or financial commands. */
export function Agents() {
  const roster = usePoll<Roster>("/v1/agents/roster", 15000);
  const catalog = usePoll<Catalog>("/orchestration/status", 15000);
  const memory = usePoll<Decisions>("/orchestration/decision-memory/recent?limit=20", 15000);
  const [selected, setSelected] = useState("");
  // usePoll retains old data on failure; never present it as a current response.
  const agents = roster.status === 200 ? roster.data?.agents ?? [] : [];
  const agent = agents.find(item => item.agent_id === selected);
  const contracts = catalog.status === 200 ? catalog.data?.contracts ?? [] : [];
  const decisions = memory.status === 200 && memory.data?.configured ? memory.data.decisions : [];

  return <div className="agent-evidence">
    <Panel title="Agents · conversation readiness">
      <span className="tag research">CAPABILITY INVENTORY · READ ONLY</span>
      <p>Choose a registered agent to inspect its identity. Direct agent conversation is unavailable. Private local hosts may enable ChatGPT explanations.</p>
      <p>ChatGPT explanations require local OAuth setup and durable storage. No paid API fallback is used.</p>
      <p><a href="https://developers.openai.com/siwc/token-sharing-open-source">Official ChatGPT plan integration requirements</a></p>
      <dl>
        <dt>Agent response</dt><dd>Requires a result actually produced by the specialist.</dd>
        <dt>ChatGPT explanation</dt><dd>Interpretation of recorded evidence; requires explicit local setup.</dd>
        <dt>Unavailable</dt><dd>No executable conversation interface. Selecting an agent does not run it.</dd>
      </dl>
    </Panel>
    <Panel title="Registered trader agents">
      <span className="tag paper">{roster.status === 200 ? roster.data?.data_mode : "UNAVAILABLE"} · VIRTUAL CAPITAL</span>
      <ReadState status={roster.status} />
      {roster.status === 200 && <p>Roster {roster.data?.enabled ? "enabled" : "disabled"} · fills {roster.data?.fills_synced ? "synchronized" : "unavailable"}</p>}
      <label htmlFor="agent-selection">Agent</label>
      <select id="agent-selection" value={agent ? selected : ""} onChange={event => setSelected(event.target.value)} disabled={!agents.length}>
        <option value="">Select an agent</option>
        {agents.map(item => <option key={item.agent_id} value={item.agent_id}>{item.name} · {item.agent_id}</option>)}
      </select>
      {agent && <dl>
        <dt>Identity</dt><dd>{agent.agent_id}</dd>
        <dt>Instrument / strategy slot</dt><dd>{agent.slot.instrument_id} · {agent.slot.kind}</dd>
        <dt>Recorded state</dt><dd>{agent.status}</dd>
        <dt>Conversation</dt><dd>Unavailable — this trader exposes no chat handler.</dd>
      </dl>}
      {agent && <AgentConversation key={agent.agent_id} agentId={agent.agent_id} />}
      <small>Source: /v1/agents/roster</small>
    </Panel>
    <Panel title="Orchestration contracts">
      <span className="tag research">REGISTERED CONTRACTS · NOT EXECUTION RESULTS</span>
      <ReadState status={catalog.status} />
      <ul>{contracts.map(item => <li key={item.name}>
        <b>{item.name}</b> · {item.owner_domain} · {item.capability}
        <p>{catalog.data?.supervisor.registered_jobs.includes(item.name) ? "Runtime handler registered" : "No registered runtime handler"} · conversation unavailable</p>
      </li>)}</ul>
      <small>Source: /orchestration/status. A contract is not an agent or a successful run.</small>
    </Panel>
    <Panel title="Recorded decision evidence">
      <span className="tag research">RECORDED EVIDENCE · NOT A NEW AGENT RESPONSE</span>
      <ReadState status={memory.status} />
      <p>Latest 20 records from the existing decision store, across agents. These are not attributed to the selected agent.</p>
      {memory.status === 200 && !memory.data?.configured && <Empty>Decision memory is not configured.</Empty>}
      {memory.status === 200 && memory.data?.configured && !decisions.length && <Empty>No recorded decisions.</Empty>}
      {decisions.map((entry, index) => <details key={index}><summary>Recorded decision {index + 1}</summary><pre>{JSON.stringify(entry, null, 2)}</pre></details>)}
      <small>Source: /orchestration/decision-memory/recent?limit=20</small>
    </Panel>
    <Panel title="FinRobot research">
      <span className="tag research">INTEGRATION STATUS · NOT A RESEARCH REPORT</span>
      <p>Research, earnings, DCF, comparables, investment memo and LBO are supported by the original opt-in worker. Dashboard execution is unavailable.</p>
      <p>The worker currently requires a provider credential. ChatGPT-plan compatibility has not been established; no research is launched here.</p>
      <small>Source: apps/research_worker/__main__.py · docs/research/FINROBOT_WORKER.md</small>
    </Panel>
  </div>;
}
