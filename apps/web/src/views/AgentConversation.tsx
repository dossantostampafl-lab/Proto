import { useState } from "react";
import { post } from "../api";

type Entry = { role: string; text: string; origin?: string; evidence?: unknown };
type History = { id: string; version: number; messages: Entry[] };

export function AgentConversation({ agentId }: { agentId: string }) {
  const [history, setHistory] = useState<History | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [text, setText] = useState("");
  const [status, setStatus] = useState("Local opt-in only. Connect a PROTO profile on your host first.");
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    const result = await post<{ id: string }>("/conversation/new", { agent_id: agentId });
    if (result.ok) { setHistory({ id: result.data.id, version: 0, messages: [] }); setStatus("Ready · ChatGPT explanation only"); }
    else setStatus(`Unavailable: ${result.error}`);
    setBusy(false);
  }
  async function resume() {
    if (!/^[0-9a-f-]{36}$/i.test(resumeId)) { setStatus("Enter a valid conversation ID."); return; }
    setBusy(true);
    const result = await post<History>(`/conversation/${resumeId}/history`);
    if (result.ok) { setHistory(result.data); setStatus("History restored"); }
    else setStatus(`Unavailable: ${result.error}`);
    setBusy(false);
  }
  async function send() {
    if (!history || !text.trim()) return;
    setBusy(true); setStatus("Processing · no agent execution requested");
    const result = await post<History>(`/conversation/${history.id}/messages`, { text, version: history.version });
    if (result.ok) { setHistory(result.data); setText(""); setStatus("ChatGPT explanation saved"); }
    else setStatus(`Failed: ${result.error}. Reload history before retrying after a network timeout.`);
    setBusy(false);
  }
  return <section aria-label="ChatGPT explanation">
    <p>Ask ChatGPT to explain recorded evidence. This does not execute the trader or FinRobot. Never enter passwords or tokens.</p>
    <button type="button" disabled={busy} onClick={() => void start()}>New explanation conversation</button>
    <label>Resume conversation ID<input value={resumeId} onChange={e => setResumeId(e.target.value)} /></label>
    <button type="button" disabled={busy || !resumeId} onClick={() => void resume()}>Load history</button>
    <p role="status" aria-live="polite">{status}</p>
    {history && <>
      <p>Conversation ID (save to resume): <code>{history.id}</code></p>
      {history.messages.map((m, i) => <article key={i}><b>{m.origin === "CHATGPT_EXPLANATION" ? "ChatGPT explanation" : "You"}</b><p style={{ whiteSpace: "pre-wrap" }}>{m.text}</p>{m.evidence != null && <details><summary>Evidence snapshot</summary><pre>{JSON.stringify(m.evidence, null, 2)}</pre></details>}</article>)}
      <label>Question<textarea maxLength={4000} value={text} onChange={e => setText(e.target.value)} disabled={busy} /></label>
      <button type="button" disabled={busy || !text.trim()} onClick={() => void send()}>Ask about evidence</button>
    </>}
  </section>;
}
