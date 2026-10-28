// Wire data of the building view, parsed defensively. Every number printed comes from
// GET /v1/agents/roster or GET /v1/oms/ledger; a missing or odd value stays null and is drawn as "—".
import { finiteDecimal, fmt2, isRecord, rec, str } from "../deskData";

export type Kind = "own" | "tradingview";
export type FloorId = "binance" | "xp" | "us";

export type Agent = {
  id: string; name: string | null; floor: FloorId; kind: Kind; kindLabel: string;
  instrument: string; symbol: string; status: "ACTIVE" | "ELIMINATED";
  equity: string | null; pnl: string | null; realized: string | null; fees: string | null; brlPerQuote: string | null;
  generation: number | null; survival: string | null; position: string | null; positionCost: string | null;
  created: string | null; eliminated: string | null; reason: string | null; valued: boolean;
};
export type Roster = { agents: Agent[]; mode: "TESTNET" | "PAPER" | "SIM"; capitalLabel: string; enabled: boolean | null };

export function parseRoster(raw: unknown): Roster | null {
  if (!isRecord(raw) || !Array.isArray(raw.agents) || raw.capital !== "VIRTUAL") return null;
  const m = str(raw.data_mode);
  const mode = m === "TESTNET" ? "TESTNET" : m === "PAPER" ? "PAPER" : "SIM";
  const agents: Agent[] = [];
  for (const a of raw.agents) {
    if (!isRecord(a) || !isRecord(a.slot)) continue;
    const id = str(a.agent_id), status = str(a.status), instrument = str(a.slot.instrument_id);
    if (!id || !instrument || (status !== "ACTIVE" && status !== "ELIMINATED")) continue;
    const kind: Kind = str(a.slot.kind) === "tradingview" ? "tradingview" : "own";
    const book = rec(a.book);
    agents.push({
      id, name: str(a.name), floor: instrument.startsWith("XP_B3:") ? "xp" : instrument.startsWith("ALPACA_US:") ? "us" : "binance", kind,
      kindLabel: kind === "tradingview" ? "TradingView" : "estratégia própria",
      instrument, symbol: instrument.split(":")[1] ?? instrument, status,
      equity: finiteDecimal(a.equity_brl), pnl: finiteDecimal(a.pnl_brl),
      realized: finiteDecimal(book.realized_pnl), fees: finiteDecimal(book.fees), brlPerQuote: finiteDecimal(a.brl_per_quote),
      generation: typeof a.generation === "number" && Number.isFinite(a.generation) ? a.generation : null,
      survival: finiteDecimal(a.survival_multiplier), position: finiteDecimal(book.position_quantity), positionCost: finiteDecimal(book.position_cost),
      created: str(a.created_at), eliminated: str(a.eliminated_at), reason: str(a.elimination_reason),
      valued: str(a.valuation) === "MARKED",
    });
  }
  const capitalLabel = mode === "TESTNET" ? "capital virtual (testnet)" : mode === "PAPER" ? "capital virtual (paper)" : "capital virtual (sim)";
  return { agents, mode, capitalLabel, enabled: typeof raw.enabled === "boolean" ? raw.enabled : null };
}

/** One per-currency account of the ledger: the three equities the Risk Engine measures limits against. */
export type Account = { quote: string; equity: string | null; startOfDay: string | null; peak: string | null; reference: string | null; measured: boolean };
export type Ledger = { unavailable: boolean; accounts: Account[] };

export function parseLedger(raw: unknown): Ledger | null {
  if (!isRecord(raw) || !isRecord(raw.accounts)) return null;
  const unavailable = raw.unavailable === true;
  const account = (quote: string, a: unknown, down: boolean): Account => {
    const r = rec(a);
    return { quote, equity: finiteDecimal(r.equity), startOfDay: finiteDecimal(r.start_of_day_equity), peak: finiteDecimal(r.peak_equity), reference: finiteDecimal(r.reference_capital), measured: !down && finiteDecimal(r.gross_exposure) !== null };
  };
  const accounts = Object.entries(raw.accounts).map(([quote, a]) => account(quote, a, unavailable));
  // Alpaca's USD account sits in its own block, measured only when that executor was read.
  const us = isRecord(raw.alpaca_us) ? raw.alpaca_us : null;
  if (us && isRecord(us.accounts) && !accounts.some(a => a.quote === "USD")) {
    accounts.push(account("USD", us.accounts.USD, us.unavailable === true));
  }
  return { unavailable, accounts };
}

// ---------- formatting (decimal strings in, text out; floats only for sums and ratios of real values) ----------
const thousands = (s: string) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
/** "R$ 1.203,41" from a decimal string (exact, via fmt2); "R$ —" without one. */
export function brl(v: string | null, signed = false): string {
  if (v === null) return "R$ —";
  const t = fmt2(v);
  if (t === "—") return "R$ —";
  const neg = t.startsWith("-");
  const [whole, frac] = (neg ? t.slice(1) : t).split(".");
  return `${neg ? "-" : signed && t !== "0.00" ? "+" : ""}R$ ${thousands(whole)},${frac}`;
}
/** A float that is a sum of real values, printed as BRL. */
export const brlOf = (n: number | null, signed = false): string => (n === null || !Number.isFinite(n) ? "R$ —" : brl(n.toFixed(2), signed));
export const sign = (v: string | null): -1 | 0 | 1 => (v === null || /^-?0*(\.0*)?$/.test(v) ? 0 : v.startsWith("-") ? -1 : 1);
export const toNum = (v: string | null): number | null => { if (v === null) return null; const n = parseFloat(v); return Number.isFinite(n) ? n : null; };

/** Sum of the agents' pnl_brl; null when any agent on the list could not be valued. */
export function pnlSum(agents: Agent[]): number | null {
  let total = 0;
  for (const a of agents) { const n = toNum(a.pnl); if (n === null) return null; total += n; }
  return total;
}

/** Realized P&L of the books in BRL (before fees); null when a book with realized P&L has no FX rate. */
export function realizedSum(agents: Agent[]): number | null {
  let total = 0;
  for (const a of agents) {
    const r = toNum(a.realized);
    if (r === null) return null;
    if (r === 0) continue;
    const fx = toNum(a.brlPerQuote);
    if (fx === null) return null;
    total += r * fx;
  }
  return total;
}

export type Limit = { used: number | null; limit: number; text: string };
/**
 * Daily loss and drawdown of an account as fractions, measured as the Risk Engine does: the loss
 * over the start-of-day (or peak) equity, but never over more than the account's reference capital
 * (R$ 10,000 in its own currency, e.g. ~1,980 USDT). Null when not measured.
 */
export function limitsOf(a: Account | null): { daily: Limit; drawdown: Limit; measured: boolean } {
  const eq = toNum(a?.equity ?? null), sod = toNum(a?.startOfDay ?? null), peak = toNum(a?.peak ?? null);
  const ref = toNum(a?.reference ?? null);
  const base = (v: number) => (ref !== null && ref > 0 ? Math.min(v, ref) : v);
  const measured = Boolean(a?.measured) && eq !== null;
  const daily = measured && sod !== null && sod > 0 ? Math.max(0, (sod - eq) / base(sod)) : null;
  const dd = measured && peak !== null && peak > 0 ? Math.max(0, (peak - eq) / base(peak)) : null;
  const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(2)}%`);
  return { measured, daily: { used: daily, limit: 0.01, text: pct(daily) }, drawdown: { used: dd, limit: 0.05, text: pct(dd) } };
}

export const shortTime = (v: string | null): string => {
  const at = v ? Date.parse(v) : Number.NaN;
  return Number.isFinite(at) ? new Date(at).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—";
};
