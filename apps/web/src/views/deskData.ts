// Data layer of the floor desks: runtime guards, exact decimal formatting, the polling feed and the
// narrowed shape of every payload a desk reads. A field that is missing or odd becomes "—" or an
// UNAVAILABLE panel, never a TypeError; a decimal is formatted from its digits, never through a float.
import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "../api";

// ---------- runtime guards ----------
export const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const rec = (v: unknown): Record<string, unknown> => (isRecord(v) ? v : {});
export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
export const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const strs = (v: unknown): string[] | null => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null);
/** A finite number as fixed text; "—" for anything else. */
export const fin = (v: unknown, digits = 2): string => { const n = num(v); return n === null ? "—" : n.toFixed(digits); };
export const orDash = (v: string | number | null | undefined): string => (v === null || v === undefined || v === "" ? "—" : String(v));

const DECIMAL = /^-?\d+(\.\d+)?$/;
const ZERO_SCI = /^-?0+([eE][-+]?\d+)?$/;
/** A plain decimal string, or null: rejects "-Infinity", "NaN", "sNaN", "0E-18", null, "" and non-strings. */
export const finiteDecimal = (v: unknown): string | null => (typeof v === "string" && DECIMAL.test(v.trim()) ? v.trim() : null);
/** A decimal, also reading an exact zero in scientific form ("0E-18") as "0"; null otherwise. */
export const decimalOrZero = (v: unknown): string | null => finiteDecimal(v) ?? (typeof v === "string" && ZERO_SCI.test(v.trim()) ? "0" : null);

/** Two decimals, rounded half up on the digits themselves (never through a float); "—" without a finite decimal. */
export function fmt2(v: unknown): string {
  const d = finiteDecimal(v);
  if (d === null) return "—";
  const negative = d.startsWith("-");
  const [whole, fraction = ""] = (negative ? d.slice(1) : d).split(".");
  const scaled = BigInt(whole + (fraction + "00").slice(0, 2)) + (fraction.length > 2 && fraction.charCodeAt(2) >= 53 ? 1n : 0n);
  const digits = scaled.toString().padStart(3, "0");
  const out = `${digits.slice(0, -2)}.${digits.slice(-2)}`;
  return negative && scaled !== 0n ? `-${out}` : out;
}
/** A fraction as a percentage by moving the decimal point ("0.00125" -> "0.125%"): no float rounding. */
export function pctOf(v: unknown): string {
  const d = finiteDecimal(v);
  if (d === null) return "—";
  const negative = d.startsWith("-");
  const [whole, fraction = ""] = (negative ? d.slice(1) : d).split(".");
  const padded = fraction.padEnd(2, "0");
  const head = (whole + padded.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const tail = padded.slice(2).replace(/0+$/, "");
  return `${negative ? "-" : ""}${head}${tail ? `.${tail}` : ""}%`;
}
/** A ledger amount without trailing zeros ("1.000" -> "1", "0E-18" -> "0"); "—" when it is not a number. */
export const amt = (v: unknown): string => {
  if (typeof v !== "string" || v.trim() === "") return "—";
  const t = v.trim();
  if (ZERO_SCI.test(t)) return "0";
  const d = finiteDecimal(t);
  return d === null ? "—" : d.includes(".") ? d.replace(/0+$/, "").replace(/\.$/, "") : d;
};
export const positive = (v: unknown): boolean => { const d = finiteDecimal(v); return d !== null && !d.startsWith("-") && /[1-9]/.test(d); };
/** a - b on decimal strings, exactly; null when either is not a finite decimal. */
export function subDecimal(a: unknown, b: unknown): string | null {
  const x = finiteDecimal(a), y = finiteDecimal(b);
  if (x === null || y === null) return null;
  const places = Math.max((x.split(".")[1] ?? "").length, (y.split(".")[1] ?? "").length);
  const scale = (d: string) => {
    const negative = d.startsWith("-");
    const [whole, fraction = ""] = (negative ? d.slice(1) : d).split(".");
    const value = BigInt(whole + fraction.padEnd(places, "0"));
    return negative ? -value : value;
  };
  const diff = scale(x) - scale(y);
  const negative = diff < 0n;
  const digits = (negative ? -diff : diff).toString().padStart(places + 1, "0");
  const whole = places ? digits.slice(0, -places) : digits;
  const fraction = places ? digits.slice(-places).replace(/0+$/, "") : "";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}
export const short = (id: string | null) => (id ? id.replace(/^[A-Z_0-9]+:/, "") : "—");
export const isB3 = (instrumentId: string | null) => Boolean(instrumentId?.startsWith("XP_B3:"));
export const isUs = (instrumentId: string | null) => Boolean(instrumentId?.startsWith("ALPACA_US:"));
export const stepValue = (v: unknown): string => (v == null ? "—" : Array.isArray(v) ? (v.length ? v.map(x => (typeof x === "string" ? x : JSON.stringify(x))).join(", ") : "none") : typeof v === "object" ? JSON.stringify(v) : String(v));
export function assertNever(value: never): never { throw new Error(`unhandled state ${JSON.stringify(value)}`); }

// ---------- the B3 session, from the clock ----------
export type B3Session = { open: boolean; local: string; weekday: string };
/** Regular B3 hours in America/Sao_Paulo, as the Risk Engine reads them: Mon-Fri, 10:00 <= time < 17:55. No holiday calendar. */
export function b3Session(at: number): B3Session {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Sao_Paulo", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(at));
  const part = (type: string) => parts.find(p => p.type === type)?.value ?? "";
  const minutes = Number(part("hour")) * 60 + Number(part("minute"));
  const weekday = part("weekday");
  return { open: weekday !== "Sat" && weekday !== "Sun" && minutes >= 600 && minutes < 1075, local: `${part("hour")}:${part("minute")}`, weekday };
}

export type UsSession = B3Session;
/** Regular US hours in America/New_York, as the Risk Engine reads them: Mon-Fri, 09:30 <= time < 16:00. No holiday calendar. */
export function usSession(at: number): UsSession {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(at));
  const part = (type: string) => parts.find(p => p.type === type)?.value ?? "";
  const minutes = Number(part("hour")) * 60 + Number(part("minute"));
  const weekday = part("weekday");
  return { open: weekday !== "Sat" && weekday !== "Sun" && minutes >= 570 && minutes < 960, local: `${part("hour")}:${part("minute")}`, weekday };
}

/**
 * The XP node on the operator's PC runs on a schedule (infra/xp-node/register-xp-node-task.ps1):
 * started 09:30 and stopped 18:10, Monday to Friday, São Paulo time. Outside that window an
 * unreadable XP executor is the schedule, not a fault.
 */
export function xpNodeScheduled(at: number): boolean {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Sao_Paulo", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(at));
  const part = (type: string) => parts.find(p => p.type === type)?.value ?? "";
  const minutes = Number(part("hour")) * 60 + Number(part("minute"));
  const weekday = part("weekday");
  return weekday !== "Sat" && weekday !== "Sun" && minutes >= 570 && minutes < 1090;
}

// ---------- polling that stops with the drawer and with a hidden tab ----------
export const POLL_MS = 4000;
export type FeedState<T> =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "paused" }
  | { kind: "unavailable"; reason: string }
  | { kind: "live"; data: T; at: number }
  | { kind: "stale"; data: T; at: number; failed: string | null };
export type Feed<T> = { state: FeedState<T>; refresh: () => void };
export const dataOf = <T,>(state: FeedState<T>): T | null => (state.kind === "live" || state.kind === "stale" ? state.data : null);

/** Polls a read endpoint (null = off). A failed or malformed read keeps the last good value as STALE; a slower older answer never overwrites a newer one. */
export function useDeskFeed<T>(path: string | null, parse: (raw: unknown) => T | null, ms = POLL_MS): Feed<T> {
  const [raw, setRaw] = useState<{ data: T | null; at: number | null; failed: string | null; tried: boolean }>({ data: null, at: null, failed: null, tried: false });
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");
  const [now, setNow] = useState(() => Date.now());
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const parseRef = useRef(parse);
  parseRef.current = parse;
  useEffect(() => {
    if (path === null) return;
    let live = true;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      const mine = ++seq.current;
      const result = await get<unknown>(path);
      if (!live || mine !== seq.current) return;
      if (!result.ok) {
        const reason = result.status === 0 ? "network" : result.status === 200 ? result.error : `HTTP ${result.status}`;
        setRaw(old => ({ ...old, tried: true, failed: reason }));
        return;
      }
      const parsed = parseRef.current(result.data);
      setRaw(old => parsed === null ? { ...old, tried: true, failed: "unexpected response" } : { data: parsed, at: Date.now(), failed: null, tried: true });
    };
    void load();
    const poll = window.setInterval(() => void load(), ms);
    const clock = window.setInterval(() => { if (document.visibilityState === "visible") setNow(Date.now()); }, 2000);
    const visibility = () => {
      setHidden(document.visibilityState === "hidden");
      if (document.visibilityState === "visible") { setNow(Date.now()); void load(); }
    };
    document.addEventListener("visibilitychange", visibility);
    return () => { live = false; window.clearInterval(poll); window.clearInterval(clock); document.removeEventListener("visibilitychange", visibility); };
  }, [path, ms, tick]);
  const refresh = useCallback(() => setTick(t => t + 1), []);
  const state: FeedState<T> = path === null ? { kind: "off" }
    : raw.data === null
      ? raw.tried ? { kind: "unavailable", reason: raw.failed ?? "no data" } : hidden ? { kind: "paused" } : { kind: "loading" }
      : raw.failed !== null || (raw.at !== null && now - raw.at > ms * 3)
        ? { kind: "stale", data: raw.data, at: raw.at ?? 0, failed: raw.failed }
        : { kind: "live", data: raw.data, at: raw.at ?? 0 };
  return { state, refresh };
}

// ---------- wire shapes, narrowed (only what the desks read) ----------
export type Signal = { key: string; at: unknown; instrument: string | null; direction: string | null; strength: number | null; source: string | null; strategy: string | null; timeframe: string | null };
export const parseSignals = (raw: unknown): Signal[] | null => isRecord(raw) && Array.isArray(raw.signals)
  ? raw.signals.map((x, i) => { const r = rec(x); return { key: `${str(r.signal_id) ?? "signal"}:${i}`, at: r.observed_at, instrument: str(r.instrument_id), direction: str(r.direction), strength: num(r.strength), source: str(r.source), strategy: str(r.strategy_id), timeframe: str(r.timeframe) }; })
  : null;

export type AgentStatus = { enabled: boolean | null; reviewer: string | null; model: string | null; modelKnown: boolean; stage: string | null; decisions: number | null; outcomes: [string, number][]; buffered: number | null; journalFailures: number | null; instruments: string[] | null };
export const parseAgentStatus = (raw: unknown): AgentStatus | null => isRecord(raw)
  ? { enabled: bool(raw.enabled), reviewer: str(raw.reviewer), model: str(raw.model), modelKnown: "model" in raw, stage: str(raw.stage), decisions: num(raw.decisions), outcomes: Object.entries(rec(raw.outcomes)).flatMap(([k, v]) => (num(v) === null ? [] : [[k, v as number] as [string, number]])), buffered: num(raw.buffered_signals), journalFailures: num(raw.journal_failures), instruments: strs(raw.instruments) }
  : null;

export type Decision = { key: string; at: unknown; instrument: string | null; outcome: string | null; abstain: string | null; direction: string | null; score: number | null; reason: string | null; hasFusion: boolean; candidate: { side: string | null; quantity: string | null; limit: string | null } | null; review: { verdict: string | null; sizeFactor: number | null; rationale: string | null } | null };
export const parseDecisions = (raw: unknown): Decision[] | null => isRecord(raw) && Array.isArray(raw.decisions)
  ? raw.decisions.map((x, i) => {
    const r = rec(x), f = rec(r.fusion), c = isRecord(r.candidate) ? r.candidate : null, v = isRecord(r.review) ? r.review : null;
    return {
      key: `${str(r.decision_id) ?? "decision"}:${i}`, at: r.decided_at, instrument: str(r.instrument_id), outcome: str(r.outcome), abstain: str(r.abstain_reason),
      direction: str(f.direction), score: num(f.score), reason: str(f.reason), hasFusion: isRecord(r.fusion),
      candidate: c ? { side: str(c.side), quantity: str(c.quantity), limit: str(c.limit_price) } : null,
      review: v ? { verdict: str(v.verdict), sizeFactor: num(v.size_factor), rationale: str(v.rationale) } : null,
    };
  })
  : null;

export type Strategy = { id: string | null; enabled: boolean | null; running: boolean | null; source: string | null; ticks: number | null; signals: number | null; books: { id: string; bars: number | null; needs: number | null; last: unknown }[] };
export const parseStrategy = (raw: unknown): Strategy | null => isRecord(raw)
  ? { id: str(raw.strategy_id), enabled: bool(raw.enabled), running: bool(raw.running), source: str(raw.market_source), ticks: num(raw.ticks), signals: num(raw.signals), books: Object.entries(rec(raw.books)).map(([id, b]) => ({ id, bars: num(rec(b).bars), needs: num(rec(b).needs_bars), last: rec(b).last_signal_at })) }
  : null;

const decimalLike = (v: unknown): string | null => (typeof v === "number" && Number.isFinite(v) ? String(v) : finiteDecimal(v));
export type RiskStatus = {
  controls: { globalKill: boolean | null; runtime: string | null; venues: string[] | null; strategies: string[] | null; lockdown: { known: boolean; value: string | null } };
  limits: { capital: string | null; perPosition: string | null; dailyLoss: string | null; drawdown: string | null; leverage: string | null; quoteAge: string | null; permitTtl: string | null };
};
export const parseRisk = (raw: unknown): RiskStatus | null => {
  if (!isRecord(raw)) return null;
  const c = rec(raw.controls), k = rec(raw.constitution), p = rec(raw.policy);
  const lockdownKnown = "lockdown" in c && (c.lockdown === null || typeof c.lockdown === "string");
  return {
    controls: { globalKill: bool(c.global_kill), runtime: str(c.runtime_kill_switch), venues: strs(c.killed_venues), strategies: strs(c.killed_strategies), lockdown: { known: lockdownKnown, value: str(c.lockdown) } },
    limits: { capital: finiteDecimal(k.reference_capital), perPosition: finiteDecimal(k.max_risk_per_position), dailyLoss: finiteDecimal(k.max_daily_loss), drawdown: finiteDecimal(k.max_drawdown), leverage: finiteDecimal(k.max_gross_leverage), quoteAge: decimalLike(p.max_quote_age_seconds), permitTtl: decimalLike(p.permit_ttl_seconds) },
  };
};

export type RiskDecision = { key: string; instrument: string | null; approved: boolean | null; rejections: string[]; dailyLoss: string | null; drawdown: string | null; breach: string | null };
export const parseRiskDecisions = (raw: unknown): RiskDecision[] | null => isRecord(raw) && Array.isArray(raw.decisions)
  ? raw.decisions.map((x, i) => { const r = rec(x), c = rec(r.checks); return { key: `${str(r.intent_id) ?? "intent"}:${i}`, instrument: str(r.instrument_id), approved: bool(r.approved), rejections: strs(r.rejections) ?? [], dailyLoss: finiteDecimal(c.daily_loss), drawdown: finiteDecimal(c.drawdown), breach: str(r.breach) }; })
  : null;

export type Watchdog = { active: boolean | null; lastBeat: unknown; coverage: number | null; required: number | null; halts: number | null; checks: [string, string][] };
export const parseWatchdog = (raw: unknown): Watchdog | null => isRecord(raw)
  ? { active: bool(raw.active), lastBeat: raw.last_beat, coverage: num(raw.coverage_hours), required: num(raw.required_hours), halts: Array.isArray(raw.recent_trips) ? raw.recent_trips.length : null, checks: Object.entries(rec(rec(raw.last_report).checks)).flatMap(([k, v]) => (typeof v === "string" ? [[k, v] as [string, string]] : [])) }
  : null;

export type Order = { key: string; id: string | null; instrument: string | null; side: string | null; quantity: string | null; limit: string | null; status: string | null; filled: string | null; average: string | null; fee: string | null; reason: string | null };
export const parseOrders = (raw: unknown): Order[] | null => isRecord(raw) && Array.isArray(raw.orders)
  ? raw.orders.map((x, i) => { const r = rec(x); return { key: `${str(r.client_order_id) ?? "order"}:${i}`, id: str(r.client_order_id), instrument: str(r.instrument_id), side: str(r.side), quantity: str(r.quantity), limit: str(r.limit_price), status: str(r.status), filled: str(r.filled_quantity), average: str(r.average_price), fee: str(r.fee), reason: str(r.reason) }; })
  : null;

export type XpBlock = { accountId: string | null; mode: string | null; unavailable: boolean; halted: boolean | null; balances: { asset: string; total: string; opening: string | null; reserved: string | null }[] };
export type Ledger = {
  accountId: string | null; mode: string | null; venue: string | null; unavailable: boolean;
  accounts: { quote: string; equity: string | null; exposure: string | null }[];
  balances: { asset: string; total: string; reserved: string | null }[];
  xp: XpBlock | null;
  /** The Alpaca US paper account (ledger "alpaca_us"), present only when Alpaca routing is on. */
  us: XpBlock | null;
};
export const parseLedger = (raw: unknown): Ledger | null => {
  if (!isRecord(raw)) return null;
  const xp = isRecord(raw.xp_b3) ? raw.xp_b3 : null, us = isRecord(raw.alpaca_us) ? raw.alpaca_us : null;
  const block = (b: Record<string, unknown>): XpBlock => ({
    accountId: str(b.account_id), mode: str(b.mode), unavailable: b.unavailable === true, halted: bool(b.venue_halted),
    balances: Object.entries(rec(b.balances)).flatMap(([asset, x]) => { const t = decimalOrZero(rec(x).total); return t === null ? [] : [{ asset, total: t, opening: decimalOrZero(rec(x).opening), reserved: decimalOrZero(rec(x).reserved) }]; }),
  });
  return {
    accountId: str(raw.account_id), mode: str(raw.mode), venue: str(raw.venue), unavailable: raw.unavailable === true,
    accounts: Object.entries(rec(raw.accounts)).map(([quote, a]) => ({ quote, equity: finiteDecimal(rec(a).equity), exposure: finiteDecimal(rec(a).gross_exposure) })),
    balances: Object.entries(rec(raw.balances)).flatMap(([asset, b]) => { const t = decimalOrZero(rec(b).total); return t === null ? [] : [{ asset, total: t, reserved: decimalOrZero(rec(b).reserved) }]; }),
    xp: xp ? block(xp) : null,
    us: us ? block(us) : null,
  };
};

export type Drill = { verdict: string | null; env: string | null; instrument: string | null; started: unknown; steps: { key: string; name: string; ok: boolean | null; detail: string }[]; digest: string | null };
export const parseDrill = (raw: unknown): Drill | null => isRecord(raw)
  ? { verdict: str(raw.verdict), env: str(raw.venue_environment), instrument: str(raw.instrument_id), started: raw.started_at, digest: str(raw.digest), steps: arr(raw.steps).map((s, i) => { const r = rec(s); return { key: `${str(r.step) ?? "step"}:${i}`, name: str(r.step) ?? "step", ok: bool(r.ok), detail: Object.entries(r).filter(([k]) => k !== "step" && k !== "ok").map(([k, v]) => `${k} ${stepValue(v)}`).join(" · ") }; }) }
  : null;

// ---------- the XP / B3 lane ----------
export type XpLaneState = "unknown" | "not-configured" | "off-hours" | "unavailable" | "live" | "halted";
export type XpLane = { state: XpLaneState; ledger: Ledger | null; killed: boolean; reason: string };
export const XP_OFF = "XP routing is off: XP_EXECUTOR_URL is not set on the API.";

/**
 * XP / B3 as the API reports it. NOT CONFIGURED when the pipeline lists no XP_B3 instrument and the
 * ledger has no xp_b3 block (the API adds both only when XP routing is on); HALTED when the Risk
 * Governor killed the venue or the executor says it is halted; OFF HOURS when the XP ledger cannot be
 * read outside the node's scheduled window (xpNodeScheduled); UNAVAILABLE when it cannot be read inside
 * it; LIVE otherwise. Nothing here is a number, so nothing here can be invented.
 */
export function deriveXpLane(input: { instruments: string[] | null; ledger: Ledger | null; ledgerFailed: boolean; killedVenues: string[] | null; at?: number }): XpLane {
  const { instruments, ledger, ledgerFailed, killedVenues, at = Date.now() } = input;
  const listed = instruments?.some(i => i.startsWith("XP_B3:")) ?? false;
  const block = ledger?.xp ?? null;
  if (instruments === null && block === null) return { state: "unknown", ledger, killed: false, reason: "The agent status could not be read, so the XP routing state is unknown." };
  if (!listed && block === null) return { state: "not-configured", ledger, killed: false, reason: XP_OFF };
  const killed = Boolean(killedVenues?.includes("XP_B3")) || block?.halted === true;
  if (killed) return { state: "halted", ledger, killed, reason: "XP_B3 is halted: new orders are refused until an operator releases it." };
  if ((ledgerFailed || block === null || block.unavailable) && !xpNodeScheduled(at)) return { state: "off-hours", ledger, killed, reason: "Outside the XP node schedule (09:30-18:10 São Paulo, Mon-Fri): the node is off by design and back at 09:30." };
  if (ledgerFailed || block === null || block.unavailable) return { state: "unavailable", ledger, killed, reason: "The XP executor cannot be read: nothing about XP is measured." };
  return { state: "live", ledger, killed, reason: "XP demo account readable (venue environment TESTNET)." };
}

// ---------- the Alpaca US lane ----------
/** The same states as the XP lane minus OFF HOURS: the Alpaca executor runs on the server around the clock. */
export type UsLaneState = Exclude<XpLaneState, "off-hours">;
export type UsLane = { state: UsLaneState; ledger: Ledger | null; killed: boolean; reason: string };
export const US_OFF = "Alpaca routing is off: ALPACA_EXECUTOR_URL is not set on the API.";

/**
 * Alpaca US (paper) as the API reports it, read like the XP lane: NOT CONFIGURED when the pipeline
 * lists no ALPACA_US instrument and the ledger has no alpaca_us block; HALTED when the Risk Governor
 * killed the venue or the executor says it is halted; UNAVAILABLE when the Alpaca ledger cannot be
 * read; LIVE otherwise. A closed US session is not a fault and is shown next to the lane, not as it.
 */
export function deriveUsLane(input: { instruments: string[] | null; ledger: Ledger | null; ledgerFailed: boolean; killedVenues: string[] | null }): UsLane {
  const { instruments, ledger, ledgerFailed, killedVenues } = input;
  const listed = instruments?.some(i => i.startsWith("ALPACA_US:")) ?? false;
  const block = ledger?.us ?? null;
  if (instruments === null && block === null) return { state: "unknown", ledger, killed: false, reason: "The agent status could not be read, so the Alpaca routing state is unknown." };
  if (!listed && block === null) return { state: "not-configured", ledger, killed: false, reason: US_OFF };
  const killed = Boolean(killedVenues?.includes("ALPACA_US")) || block?.halted === true;
  if (killed) return { state: "halted", ledger, killed, reason: "ALPACA_US is halted: new orders are refused until an operator releases it." };
  if (ledgerFailed || block === null || block.unavailable) return { state: "unavailable", ledger, killed, reason: "The Alpaca executor cannot be read: nothing about the US account is measured." };
  return { state: "live", ledger, killed, reason: "Alpaca paper account readable (simulated fills, no money)." };
}
