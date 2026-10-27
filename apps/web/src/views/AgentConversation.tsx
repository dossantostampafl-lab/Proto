import { useState } from "react";
import { post } from "../api";

type Entry = { role: string; text: string; origin?: string; evidence?: unknown };
type History = { id: string; agent_id: string; version: number; messages: Entry[] };
type Connection = { connected: boolean; state: string; configured_model: string | null };

export function AgentConversation({ agentId }: { agentId: string }) {
  const [history, setHistory] = useState<History | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [text, setText] = useState("");
  const [status, setStatus] = useState("Verify your private PROTO connection before starting.");
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [setup, setSetup] = useState(false);
  const privateHost = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);

  async function verify() {
    setBusy(true); setModels([]); setModel(""); setConnection(null);
    const result = await post<Connection>("/conversation/status");
    if (!result.ok) { setStatus(`Unavailable: ${result.error}`); setBusy(false); return; }
    setConnection(result.data);
    if (!result.data.connected) {
      setSetup(true); setStatus("Authorization required. Complete the local OpenAI sign-in and import the PROTO session into the VM.");
    } else {
      setStatus("Credentials present. Checking account access with OpenAI…");
      const catalog = await post<{ models: string[] }>("/conversation/models");
      if (catalog.ok) {
        setModels(catalog.data.models);
        const configured = result.data.configured_model;
        setModel(configured && catalog.data.models.includes(configured) ? configured : catalog.data.models[0] ?? "");
        setStatus(catalog.data.models.length ? "OpenAI account access verified. Choose an available model." : "OpenAI returned no available models. Conversation unavailable.");
      } else setStatus(`Credentials present; account access NOT verified: ${catalog.error}`);
    }
    setBusy(false);
  }
  async function disconnect() {
    setBusy(true);
    const result = await post<{ remote_revocation_confirmed: boolean }>("/conversation/disconnect");
    if (result.ok) {
      setConnection(null); setModels([]); setModel(""); setHistory(null); setText("");
      setStatus(result.data.remote_revocation_confirmed ? "Disconnected. Remote revocation confirmed." : "Local credentials cleared. Remote revocation unconfirmed; also disconnect PROTO in ChatGPT settings.");
    } else setStatus(`Disconnect failed: ${result.error}`);
    setBusy(false);
  }
  async function start() {
    setBusy(true);
    const result = await post<{ id: string }>("/conversation/new", { agent_id: agentId });
    if (result.ok) { setHistory({ id: result.data.id, agent_id: agentId, version: 0, messages: [] }); setStatus("Ready · ChatGPT explanation only"); }
    else setStatus(`Unavailable: ${result.error}`);
    setBusy(false);
  }
  async function resume() {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(resumeId)) { setStatus("Enter a valid conversation ID."); return; }
    setBusy(true);
    const result = await post<History>(`/conversation/${resumeId}/history`);
    if (result.ok && result.data.agent_id === agentId) { setHistory(result.data); setStatus("History restored"); }
    else if (result.ok) { setHistory(null); setStatus(`This conversation belongs to agent ${result.data.agent_id}. Select that agent first.`); }
    else setStatus(`Unavailable: ${result.error}`);
    setBusy(false);
  }
  async function send() {
    if (!history || !text.trim() || !model) return;
    setBusy(true); setStatus("Processing · no agent execution requested");
    const result = await post<History>(`/conversation/${history.id}/messages`, { text, version: history.version, model });
    if (result.ok) { setHistory(result.data); setText(""); setStatus("ChatGPT explanation saved"); }
    else setStatus(`Failed: ${result.error}. Reload history before retrying after a network timeout.`);
    setBusy(false);
  }
  return <section className="agent-conversation" aria-label="ChatGPT explanation">
    <h3>ChatGPT connection</h3>
    <ul className="agent-roles" aria-label="What this conversation can and cannot do">
      <li><b>Explanation</b> · what this panel does: ChatGPT explains recorded evidence. It writes nothing but the saved conversation.</li>
      <li><b>Proposal</b> · not here: only the agents produce typed intents, from signals, never from this chat.</li>
      <li><b>Execution</b> · not here: only the OMS, with a Risk Engine permit, on paper or testnet. No real money.</li>
    </ul>
    <p>Use your eligible ChatGPT account. OpenAI grants consent in its own sign-in page; no OpenAI API key is required.</p>
    {!privateHost && <p role="note">This public dashboard cannot authorize the private connector. Open the same PROTO dashboard through an SSH tunnel at localhost. Follow <code>docs/research/CHATGPT_ORACLE_SETUP.md</code> in the PROTO repository.</p>}
    <button type="button" className="btn" aria-expanded={setup} onClick={() => setSetup(!setup)}>Connect ChatGPT · setup</button>
    {setup && <ol>
      <li>On the VM, prepare its identity: <code>python -m services.chatgpt.vm prepare</code>.</li>
      <li>On your own computer, run PROTO’s <code>python -m services.chatgpt.chatgpt_connect --profile oracle --enable-plan</code>. Complete consent on the official OpenAI page opened by the command.</li>
      <li>Transfer only this PROTO session over SSH, then import it with <code>python -m services.chatgpt.vm import --source /private/path/credentials.json</code>. The VM keeps its own host identity. Remove transfer copies and let only the VM refresh.</li>
      <li>Open the private dashboard, authenticate with the existing operator token in System, and verify access below. <a href="https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms" target="_blank" rel="noreferrer">Official OpenAI VM procedure</a>.</li>
    </ol>}
    <button type="button" className="btn primary" disabled={busy || !privateHost} onClick={() => void verify()}>Verify OpenAI connection</button>
    <button type="button" className="btn" disabled={busy || !connection?.connected} onClick={() => void disconnect()}>Disconnect ChatGPT</button>
    {models.length > 0 && <label className="field"><span>ChatGPT model</span><select value={model} disabled={busy} onChange={e => setModel(e.target.value)}>{models.map(value => <option key={value}>{value}</option>)}</select></label>}
    <p>Ask ChatGPT to explain recorded evidence. This does not execute the trader or FinRobot. Never enter passwords or tokens.</p>
    <button type="button" className="btn" disabled={busy || !model} onClick={() => void start()}>New explanation conversation</button>
    <label className="field"><span>Resume conversation ID</span><input value={resumeId} autoComplete="off" spellCheck={false} placeholder="00000000-0000-0000-0000-000000000000" onChange={e => setResumeId(e.target.value.trim())} /></label>
    <button type="button" className="btn" disabled={busy || !resumeId || !privateHost} onClick={() => void resume()}>Load history</button>
    <p role="status" aria-live="polite" className="agent-note">{status}</p>
    {history && <>
      <p>Conversation ID (save to resume): <code>{history.id}</code></p>
      {history.messages.map((m, i) => <article key={i}><b>{m.origin === "CHATGPT_EXPLANATION" ? "ChatGPT explanation" : "You"}</b><p style={{ whiteSpace: "pre-wrap" }}>{m.text}</p>{m.evidence != null && <details><summary>Evidence snapshot</summary><pre>{JSON.stringify(m.evidence, null, 2)}</pre></details>}</article>)}
      <label className="field"><span>Question</span><textarea maxLength={4000} value={text} onChange={e => setText(e.target.value)} disabled={busy} /></label>
      <button type="button" className="btn primary" disabled={busy || !text.trim() || !model} onClick={() => void send()}>Ask about evidence</button>
    </>}
  </section>;
}
