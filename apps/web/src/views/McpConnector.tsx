import { usePoll } from "../hooks";
import { Empty, Panel, Stat, useCommand } from "../ui";
import { bool, isRecord, num, orDash, str } from "./deskData";

// ChatGPT connector (read-only MCP server, docs/finrobots/chatgpt-mcp.md): whether it is on, whether
// ChatGPT can sign in with OAuth, and how many connectors hold a token. The operator can sign every
// connector out at once. ChatGPT only reads: no tool behind it can trade, size, permit or change limits.

type Status = { enabled: boolean | null; staticToken: boolean | null; oauth: boolean | null; issuer: string | null; clients: number | null; tokens: number | null };

function parseStatus(raw: unknown): Status | null {
  if (!isRecord(raw) || raw.real_money_execution !== false) return null;
  return {
    enabled: bool(raw.mcp_enabled), staticToken: bool(raw.static_token_configured), oauth: bool(raw.available),
    issuer: str(raw.issuer), clients: num(raw.clients), tokens: num(raw.active_access_tokens),
  };
}

/** Connector state and the operator's sign-out. Reading it never connects anything. */
export function McpConnector() {
  const poll = usePoll<unknown>("/v1/mcp/oauth/status", 30000);
  const { busy, run } = useCommand();
  const status = poll.status === 200 ? parseStatus(poll.data) : null;
  const revoke = async () => { if (await run("Sign out ChatGPT connectors", "/v1/mcp/oauth/revoke")) poll.refresh(); };

  return <Panel title="ChatGPT connector (MCP)" actions={status && (status.clients ?? 0) > 0
    ? <button type="button" className="btn" disabled={busy !== null} onClick={revoke}>Sign out all</button>
    : undefined}>
    <span className="tag research">READ ONLY · NO ORDER AUTHORITY</span>
    {poll.status !== 200 && <p role="status" className="agent-read">{poll.status === null ? "Reading connector…" : `CONNECTOR STATUS UNAVAILABLE · ${poll.status || "network error"}.`}</p>}
    {poll.status === 200 && !status && <Empty>CONNECTOR STATUS UNAVAILABLE · unexpected payload.</Empty>}
    {status && <>
      <div className="grid2">
        <Stat label="MCP server" value={status.enabled === null ? "—" : status.enabled ? "ENABLED" : "DISABLED"} className={status.enabled ? "" : "muted"} />
        <Stat label="OAuth sign-in" value={status.oauth === null ? "—" : status.oauth ? "AVAILABLE" : "UNAVAILABLE"} className={status.oauth ? "" : "warn"} />
        <Stat label="Connected clients" value={orDash(status.clients)} />
        <Stat label="Active access tokens" value={orDash(status.tokens)} />
      </div>
      <p className="agent-note">{status.oauth
        ? `ChatGPT connector URL: ${status.issuer}/mcp · authentication OAuth; approve with MCP_API_TOKEN.`
        : "OAuth needs MCP_ENABLED, MCP_API_TOKEN and an HTTPS PUBLIC_ORIGIN on the API."}</p>
    </>}
    <small className="agent-src">Source: /v1/mcp/oauth/status · ChatGPT runs on the operator's plan; no paid API key. Tokens live in memory: an API restart signs connectors out.</small>
  </Panel>;
}
