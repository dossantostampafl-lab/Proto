import { useState } from "react";
import { post } from "../api";

type Entry = { role: string; text: string; origin?: string; evidence?: unknown };
type History = { id: string; agent_id: string; version: number; messages: Entry[] };
type Connection = { connected: boolean; state: string; configured_model: string | null; mode?: string; authorization_command?: string };

// Browser speech (no service, no key): Chrome/Android's SpeechRecognition and speechSynthesis.
type Recognition = { lang: string; interimResults: boolean; onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null; onend: (() => void) | null; onerror: (() => void) | null; start: () => void };
function recognition(): Recognition | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}
function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text.slice(0, 1200));
  u.lang = "pt-BR";
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}

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
  const [voice, setVoice] = useState(true);
  const [listening, setListening] = useState(false);
  const publicMode = connection?.mode === "PUBLIC_OPERATOR";
  const reachable = privateHost || publicMode;

  async function verify() {
    setBusy(true); setModels([]); setModel(""); setConnection(null);
    const result = await post<Connection>("/conversation/status");
    if (!result.ok) { setStatus(`Unavailable: ${result.error}`); setBusy(false); return; }
    setConnection(result.data);
    if (!result.data.connected) {
      setSetup(true); setStatus(result.data.mode === "PUBLIC_OPERATOR"
        ? "Login do ChatGPT necessário: veja os passos abaixo."
        : "Authorization required. Complete the local OpenAI sign-in and import the PROTO session into the VM.");
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
  async function send(spoken?: string) {
    const question = (spoken ?? text).trim();
    if (!history || !question || !model) return;
    setBusy(true); setStatus("Processing · no agent execution requested");
    const result = await post<History>(`/conversation/${history.id}/messages`, { text: question, version: history.version, model });
    if (result.ok) {
      setHistory(result.data); setText(""); setStatus("ChatGPT explanation saved");
      const last = result.data.messages[result.data.messages.length - 1];
      if (voice && last?.origin === "CHATGPT_EXPLANATION") speak(last.text);
    }
    else setStatus(`Failed: ${result.error}. Reload history before retrying after a network timeout.`);
    setBusy(false);
  }
  function listen() {
    const rec = recognition();
    if (!rec) { setStatus("Este navegador não tem reconhecimento de voz. Use o Chrome."); return; }
    rec.lang = "pt-BR"; rec.interimResults = false;
    rec.onresult = e => { const heard = e.results[0]?.[0]?.transcript ?? ""; if (heard) { setText(heard); void send(heard); } };
    rec.onend = () => setListening(false);
    rec.onerror = () => { setListening(false); setStatus("Não entendi. Toque no microfone e fale de novo."); };
    setListening(true); rec.start();
  }
  return <section className="agent-conversation" aria-label="ChatGPT explanation">
    <h3>ChatGPT connection</h3>
    <ul className="agent-roles" aria-label="What this conversation can and cannot do">
      <li><b>Explanation</b> · what this panel does: ChatGPT explains recorded evidence. It writes nothing but the saved conversation.</li>
      <li><b>Proposal</b> · not here: only the agents produce typed intents, from signals, never from this chat.</li>
      <li><b>Execution</b> · not here: only the OMS, with a Risk Engine permit, on paper or testnet. No real money.</li>
    </ul>
    <p>Use your eligible ChatGPT account. OpenAI grants consent in its own sign-in page; no OpenAI API key is required.</p>
    {!privateHost && <p role="note">Pelo site: entre com o token de operador na aba System e toque em "Verify OpenAI connection".</p>}
    <button type="button" className="btn" aria-expanded={setup} onClick={() => setSetup(!setup)}>Connect ChatGPT · setup</button>
    {setup && publicMode && <ol>
      <li>No Cloud Shell, rode: <code>{connection?.authorization_command ?? "bash ~/Proto/infra/oci/deploy/bastion-run.sh chatgpt-login api"}</code></li>
      <li>Abra o link que ele mostrar e entre com a sua conta do ChatGPT. Aceite o pedido de uso do plano.</li>
      <li>O navegador termina numa página que não abre (<code>http://127.0.0.1:1455/…</code>). Copie o endereço completo dessa página e cole no Cloud Shell.</li>
      <li>Volte aqui e toque em "Verify OpenAI connection". A sessão fica só no servidor da API; nenhum token aparece.</li>
    </ol>}
    {setup && !publicMode && <ol>
      <li>On the VM, prepare its identity: <code>python -m services.chatgpt.vm prepare</code>.</li>
      <li>On your own computer, run PROTO’s <code>python -m services.chatgpt.chatgpt_connect --profile oracle --enable-plan</code>. Complete consent on the official OpenAI page opened by the command.</li>
      <li>Transfer only this PROTO session over SSH, then import it with <code>python -m services.chatgpt.vm import --source /private/path/credentials.json</code>. The VM keeps its own host identity. Remove transfer copies and let only the VM refresh.</li>
      <li>Open the private dashboard, authenticate with the existing operator token in System, and verify access below. <a href="https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms" target="_blank" rel="noreferrer">Official OpenAI VM procedure</a>.</li>
    </ol>}
    <button type="button" className="btn primary" disabled={busy} onClick={() => void verify()}>Verify OpenAI connection</button>
    <button type="button" className="btn" disabled={busy || !connection?.connected} onClick={() => void disconnect()}>Disconnect ChatGPT</button>
    {models.length > 0 && <label className="field"><span>ChatGPT model</span><select value={model} disabled={busy} onChange={e => setModel(e.target.value)}>{models.map(value => <option key={value}>{value}</option>)}</select></label>}
    <p>Ask ChatGPT to explain recorded evidence. This does not execute the trader or FinRobot. Never enter passwords or tokens.</p>
    <button type="button" className="btn" disabled={busy || !model} onClick={() => void start()}>New explanation conversation</button>
    <label className="field"><span>Resume conversation ID</span><input value={resumeId} autoComplete="off" spellCheck={false} placeholder="00000000-0000-0000-0000-000000000000" onChange={e => setResumeId(e.target.value.trim())} /></label>
    <button type="button" className="btn" disabled={busy || !resumeId || !reachable} onClick={() => void resume()}>Load history</button>
    <p role="status" aria-live="polite" className="agent-note">{status}</p>
    {history && <>
      <p>Conversation ID (save to resume): <code>{history.id}</code></p>
      {history.messages.map((m, i) => <article key={i}><b>{m.origin === "CHATGPT_EXPLANATION" ? "ChatGPT explanation" : "You"}</b><p style={{ whiteSpace: "pre-wrap" }}>{m.text}</p>{m.evidence != null && <details><summary>Evidence snapshot</summary><pre>{JSON.stringify(m.evidence, null, 2)}</pre></details>}</article>)}
      <label className="field"><span>Question</span><textarea maxLength={4000} value={text} onChange={e => setText(e.target.value)} disabled={busy} /></label>
      <button type="button" className="btn primary" disabled={busy || !text.trim() || !model} onClick={() => void send()}>Ask about evidence</button>
      <button type="button" className="btn" disabled={busy || listening || !model} aria-pressed={listening} onClick={listen}>{listening ? "Ouvindo…" : "🎤 Falar"}</button>
      <label className="field"><span>Responder em voz</span><input type="checkbox" checked={voice} onChange={e => { setVoice(e.target.checked); if (!e.target.checked && "speechSynthesis" in window) window.speechSynthesis.cancel(); }} /></label>
    </>}
  </section>;
}
