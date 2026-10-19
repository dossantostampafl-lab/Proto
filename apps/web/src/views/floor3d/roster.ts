// The trader-agent roster as GET /v1/agents/roster reports it. Parsed defensively: an odd field
// drops that agent, an odd payload drops the roster, and nothing is ever filled in.
import { finiteDecimal, fmt2, isRecord, str } from "../deskData";

export type RosterAgent = { id: string; slot: string; slotLabel: string; symbol: string | null; status: "ACTIVE" | "ELIMINATED"; equity: string };
export type Roster = { agents: RosterAgent[]; capital: string };

/** "R$198,40" from the decimal string, "R$ —" when the API could not value the book. */
export const brl = (v: unknown): string => {
  const d = finiteDecimal(v);
  return d === null ? "R$ —" : `R$${fmt2(d).replace(".", ",")}`;
};

export function parseRoster(raw: unknown): Roster | null {
  if (!isRecord(raw) || !Array.isArray(raw.agents) || raw.capital !== "VIRTUAL") return null;
  const mode = str(raw.data_mode);
  const capital = `VIRTUAL ${mode === "TESTNET" ? "TESTNET" : mode === "PAPER" ? "PAPER" : "SIM"} CAPITAL · NO REAL MONEY`;
  const agents: RosterAgent[] = [];
  for (const a of raw.agents) {
    if (!isRecord(a) || !isRecord(a.slot)) continue;
    const id = str(a.agent_id), key = str(a.slot.key), status = str(a.status);
    if (!id || !key || (status !== "ACTIVE" && status !== "ELIMINATED")) continue;
    const instrument = str(a.slot.instrument_id) ?? "";
    const symbol = instrument.split(":")[1] ?? null;
    const kind = str(a.slot.kind) === "tradingview" ? "TradingView" : "own strategy";
    agents.push({ id, slot: key, slotLabel: `${symbol ?? "?"} · ${kind}`, symbol, status, equity: brl(a.equity_brl) });
  }
  return { agents, capital };
}
