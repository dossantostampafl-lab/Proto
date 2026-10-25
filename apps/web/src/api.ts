// Typed client for the Proto API. Same-origin by default; VITE_API_BASE_URL overrides for local dev.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
export const WS_BASE = API_BASE.replace(/^http/, "ws");

export const SYMBOLS = ["BTC", "ETH", "SOL"] as const;
export type Sym = (typeof SYMBOLS)[number];

export type Health = { status: string; mode: string; version: string };
export type Runtime = { mode: string; running: boolean; kill_switch: string; replay_speed: number };
export type LiveStatus = { running: boolean; receiving_data: boolean; all_symbols_fresh: boolean; last_receipt_age_seconds: number | null };
export type Frame = { timestamp: string; symbol: Sym; bid: number; ask: number; mid: number; bid_size: number; ask_size: number; sequence: number; connection_generation: number };
export type Analytics = { sample_count: number; simple_return: number; realized_volatility: number; current_spread_bps: number; current_imbalance: number; current_microprice: number };
export type Market = { market_id: string; symbol: string; market_probability: number; model_probability: number; confidence: number; uncertainty: number; net_edge: number; edge_decision: string; expiry_horizon_minutes: number };
export type Hawkes = { current_intensity: number; baseline_intensity: number; excitation: number; branching_ratio: number; event_probability: number };
export type Greeks = { market_probability_delta: number; volatility_vega: number; imbalance_kappa: number; time_theta: number };
export type Calibration = { observation_count: number; brier_score: number | null; log_loss: number | null; expected_calibration_error: number | null; reliability_curve: { mean_prediction: number; observed_frequency: number }[] };
export type Position = Record<string, unknown>;
export type Portfolio = { gross_exposure: number; net_exposure: number; total_pnl_after_fees: number; total_realized_pnl?: number; total_unrealized_pnl?: number; realized_drawdown: number; max_asset_concentration: number; open_position_count: number; positions?: Position[] };
export type PnL = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
export type Paper = { mode: string; paper_execution_enabled: boolean; autopilot_running: boolean; kill_switch: string };
export type Autopilot = { running: boolean; last_reason: string; counters: Record<string, number> };
export type Replay = { active: boolean; paused: boolean; finished: boolean; cursor: number; total_frames: number; speed: string };
export type Instrument = { instrument_id: string; asset_class: string; venue: string; currency: string; coverage: { market_data_provider?: string | null; currently_fresh?: boolean } };
export type Universe = { instruments: Instrument[] };
export type Orchestration = { durable_runtime?: { configured?: boolean }; readiness?: { ready_safe_scope?: boolean; durable_safe_scope?: boolean } };
export type Memory = { configured: boolean; records: number; resolved: number; unresolved: number };
export type Shadow = { running?: boolean };
export type Creation = { configured: boolean; orchestration_available: boolean; transport: string; allowed_jobs: string[] };
export type Deployment = { release: string; ui_release: string; git_commit_sha: string | null; environment: string | null };

export type Result<T> = { ok: true; status: number; data: T } | { ok: false; status: number; error: string };

const TOKEN_KEY = "proto.operatorToken";

export function operatorToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}

export function setOperatorToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage blocked: token simply is not remembered */ }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<Result<T>> {
  const post = init.method === "POST";
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), path.startsWith("/conversation/") ? 65000 : post ? 8000 : 3500);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = operatorToken();
  if (post && token) headers["X-Proto-Operator-Token"] = token;
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, headers, signal: controller.signal });
    const text = await response.text();
    if (!response.ok) {
      // An error body is not always JSON (a proxy's 502/504 page): the status is what matters.
      let detail: unknown = text.slice(0, 200);
      try {
        const body: unknown = text ? JSON.parse(text) : null;
        if (body && typeof body === "object" && "detail" in body) detail = (body as { detail: unknown }).detail;
      } catch { /* keep the text */ }
      return { ok: false, status: response.status, error: typeof detail === "string" ? detail || `HTTP ${response.status}` : JSON.stringify(detail) };
    }
    let body: unknown = null;
    if (text) {
      try { body = JSON.parse(text); } catch { return { ok: false, status: response.status, error: "invalid JSON" }; }
    }
    return { ok: true, status: response.status, data: body as T };
  } catch (error) {
    return { ok: false, status: 0, error: error instanceof Error ? error.message : "network error" };
  } finally {
    window.clearTimeout(timer);
  }
}

export const get = <T,>(path: string) => call<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
