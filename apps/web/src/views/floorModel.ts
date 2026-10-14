// Shared model of the trading floor: wire types from /v1/floor/state and /ws/floor, and helpers
// that read live values without ever estimating a missing one.
import type { Sym } from "../api";
import type { Shared } from "../main";

export type Tone = "ok" | "warn" | "alert" | "working";
export type StationId = "tradingview" | "fusion" | "reviewer" | "risk" | "oms" | "reconciliation";
export type FloorAction = { seq: number; at: string; event_type: string; station: StationId; tone: Tone; headline: string; symbol: string | null; why?: string[] };
export type Account = { equity: string; start_of_day_equity: string; peak_equity: string; gross_exposure: string; positions: Record<string, string> } | null;
export const STAGES = ["RESEARCH", "REPLAY", "SHADOW", "PAPER", "CANARY", "LIVE"] as const;

export type PromotionScope = {
  scope: { account: string; venue: string; strategy_id: string; model_version: string; credential_id: string; key: string };
  stage: string;
  clean_days: number;
  next_stage: string | null;
  blockers: string[];
  violations: number;
};
export type WatchdogSummary = {
  active: boolean;
  last_beat: string | null;
  coverage_hours: number;
  required_hours: number;
  trips: number;
  last_trip: { at: string; reasons: string[] } | null;
};
export type FloorState = {
  promotion?: PromotionScope[];
  watchdog?: WatchdogSummary;
  data_mode: "PAPER" | "TESTNET";
  journal: string;
  stations: Record<StationId, FloorAction | null>;
  actions: FloorAction[];
  controls: { global_kill?: boolean; killed_venues?: string[]; killed_strategies?: string[]; lockdown?: string | null; runtime_kill_switch?: string };
  accounts: Record<string, Account>;
  pipeline: { agents_enabled: boolean; reviewer: string; execution_backend: string; paper_execution_enabled: boolean };
};

/** Any global halt: the Risk Governor's kill switch or the runtime kill switch leaving ARMED. */
export const isHalted = (state: FloorState | null) =>
  Boolean(state?.controls.global_kill) || (state?.controls.runtime_kill_switch ?? "ARMED") !== "ARMED";

export const TONE_COLOR: Record<Tone | "idle", string> = { ok: "#58c39a", warn: "#d8a55b", alert: "#ea666b", working: "#b9c2cb", idle: "#80888f" };

/** Every step of the authority chain has a desk; both scenes place these by id. */
export const STATIONS: { id: StationId; label: string; role: string }[] = [
  { id: "tradingview", label: "TRADINGVIEW", role: "Mercado e dados" },
  { id: "fusion", label: "FUSION", role: "Sinais e estratégias" },
  { id: "reviewer", label: "AI REVIEWER", role: "Análise e validação" },
  { id: "risk", label: "RISK ENGINE", role: "Risco e controle" },
  { id: "oms", label: "OMS / EXECUTOR", role: "Ordens e execução" },
  { id: "reconciliation", label: "RECONCILIATION", role: "Balanço e auditoria" },
];

export function sparkPath(values: number[], x: number, y: number, w: number, h: number) {
  if (values.length < 2) return "";
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  return values.map((v, i) => `${i ? "L" : "M"}${(x + (i / (values.length - 1)) * w).toFixed(1)},${(y + h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
}

/** The latest public mid, or null when there is no usable price. */
export const priceOf = (market: Shared["market"], s: Sym) => {
  const series = market.series[s];
  const mid = market.frames[s]?.mid ?? series[series.length - 1];
  return mid != null && Number.isFinite(mid) && mid > 0 ? mid : null;
};

/** Change since the first point held in memory, or null without two points. */
export const changeOf = (market: Shared["market"], s: Sym) => {
  const series = market.series[s], mid = priceOf(market, s);
  return series.length > 1 && mid ? mid / series[0] - 1 : null;
};
