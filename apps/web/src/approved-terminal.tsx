import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./approved-terminal.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type ViewName = "overview" | "paper" | "research" | "replay" | "risk" | "system";
type Health = { status: string; mode: string; version: string };
type RuntimeState = { mode: string; running: boolean; kill_switch: string; replay_speed: number };
type LiveStatus = { running: boolean; receiving_data: boolean; complete: boolean; all_symbols_fresh: boolean; last_receipt_age_seconds: number | null; fresh_symbols: string[] };
type LiveFrame = { timestamp: string; received_at?: string | null; symbol: SymbolName; bid: number; ask: number; mid: number; spread: number; bid_size: number; ask_size: number; sequence: number; connection_generation: number };
type LiveMarketResponse = { markets: LiveFrame[] };
type LiveHistoryResponse = { history: LiveFrame[] };
type LiveAnalytics = { symbol: SymbolName; sample_count: number; simple_return: number; realized_volatility: number; current_spread_bps: number; current_imbalance: number; current_microprice: number; observation_span_seconds: number };
type LifecycleRow = { market_id: string; symbol: string; market_probability: number; model_probability: number; confidence: number; uncertainty: number; net_edge: number; edge_decision: string; expiry_horizon_minutes: number };
type LifecycleResponse = { source?: string; markets: LifecycleRow[] };
type Hawkes = { current_intensity: number; baseline_intensity: number; excitation: number; branching_ratio: number; event_probability: number; decay?: number };
type Greeks = { market_probability_delta: number; volatility_vega: number; imbalance_kappa: number; time_theta: number };
type Calibration = { status: string; source: string; observation_count: number; brier_score: number | null; log_loss: number | null; expected_calibration_error: number | null; maximum_calibration_error: number | null; reliability_curve: Array<{ count: number; mean_prediction: number; observed_frequency: number; absolute_gap: number }> };
type Position = Record<string, unknown>;
type Portfolio = { gross_exposure: number; net_exposure: number; total_pnl_after_fees: number; total_realized_pnl?: number; total_unrealized_pnl?: number; realized_drawdown: number; max_asset_concentration: number; open_position_count: number; positions?: Position[] };
type PnL = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
type PaperStatus = { mode: string; running: boolean; kill_switch: string; paper_execution_enabled: boolean; autopilot_running: boolean; financial_connectivity: boolean; real_money_execution: boolean };
type AutopilotStatus = { running: boolean; paper_runtime_ready: boolean; live_market_ready: boolean; kill_switch: string; last_reason: string; config: Record<string, unknown> | null; counters: Record<string, number>; financial_connectivity: boolean; real_money_execution: boolean };
type ReplayStatus = { active: boolean; paused: boolean; finished: boolean; cursor: number; total_frames: number; speed: string; fingerprint?: string | null; current_timestamp?: string | null };
type RiskState = Record<string, unknown>;
type ApiResult<T> = { ok: boolean; status: number; data: T | null; error: string | null };
type Envelope = { type: string; data?: unknown };
type ActionEntry = { at: string; label: string; ok: boolean; detail: string };

type ReplayUpload = { frames: unknown[]; name: string };

const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const REST_MS = 3000;
const RESEARCH_MS = 5000;
const MAX_SERIES = 90;
const HISTORY_LIMIT = 1000;

const emptyFrames = (): Record<SymbolName, LiveFrame | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyAnalytics = (): Record<SymbolName, LiveAnalytics | null> => ({ BTC: null, ETH: null, SOL: null });
const emptySeries = (): Record<SymbolName, number[]> => ({ BTC: [], ETH: [], SOL: [] });
const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const append = (xs: number[], v: number) => [...xs, v].slice(-MAX_SERIES);
const n = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const usd = (v: number | null | undefined) => v == null || !Number.isFinite(v) ? "—" : `$${n(v, Math.abs(v) >= 1000 ? 2 : 4)}`;
const pct = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : `${n(v * 100, d)}%`;
const horizon = (m: number) => m < 60 ? `${m}m` : m % 1440 === 0 ? `${m / 1440}d` : `${m / 60}h`;
const pickNumber = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const pickText = (value: unknown): string => typeof value === "string" ? value : "—";

async function api<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), init?.method === "POST" ? 8000 : 3500);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      signal: controller.signal,
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) as unknown : null;
    if (!response.ok) {
      const detail = parsed && typeof parsed === "object" && "detail" in parsed ? JSON.stringify((parsed as { detail: unknown }).detail) : text || `HTTP ${response.status}`;
      return { ok: false, status: response.status, data: null, error: detail };
    }
    return { ok: true, status: response.status, data: parsed as T, error: null };
  } catch (error) {
    return { ok: false, status: 0, data: null, error: error instanceof Error ? error.message : "network error" };
  } finally {
    window.clearTimeout(timeout);
  }
}

const requestJson = <T,>(path: string) => api<T>(path);
const postJson = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function Badge({ kind, children }: { kind: "live" | "research" | "paper" | "ok" | "danger"; children?: React.ReactNode }) {
  const label = children ?? (kind === "live" ? "LIVE PUBLIC" : kind === "research" ? "SYNTHETIC RESEARCH" : kind === "paper" ? "PAPER / SIM" : kind === "danger" ? "STOP" : "HEALTHY");
  return <span className={`protoBadge ${kind}`}>{label}</span>;
}

function Line({ values, tone = "green", height = 42 }: { values: number[]; tone?: "green" | "red" | "blue" | "purple" | "orange"; height?: number }) {
  if (values.length < 2) return <div className="lineEmpty">collecting</div>;
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  const points = values.map((v, i) => `${(i / (values.length - 1)) * 100},${height - 4 - ((v - lo) / span) * (height - 8)}`).join(" ");
  return <svg className={`miniLine ${tone}`} viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" aria-hidden="true"><polyline points={points} fill="none" vectorEffect="non-scaling-stroke" /></svg>;
}

function AssetCard({ symbol, frame, analytics, research, series }: { symbol: SymbolName; frame: LiveFrame | null; analytics: LiveAnalytics | null; research: LifecycleRow | null; series: number[] }) {
  const change = analytics?.simple_return ?? 0;
  return <article className="assetCard">
    <div className="assetHeader"><div className={`coin ${symbol.toLowerCase()}`}>{symbol === "BTC" ? "₿" : symbol === "ETH" ? "◆" : "≋"}</div><div><b>{symbol}</b><small>{symbol}/USD</small></div><div className="assetPrice"><span>LAST PRICE</span><strong>{usd(frame?.mid)}</strong></div><div className="assetSpread"><span>SPREAD</span><b>{analytics ? `${n(analytics.current_spread_bps, 2)} bp` : "—"}</b></div></div>
    <div className="assetResearch"><div><span>MARKET PROB</span><strong>{research ? n(research.market_probability, 3) : "—"}</strong><Badge kind="research" /></div><div><span>FAIR PROB</span><strong>{research ? n(research.model_probability, 3) : "—"}</strong><Badge kind="research" /></div><div><span>EDGE</span><strong className={(research?.net_edge ?? 0) >= 0 ? "good" : "bad"}>{research ? `${research.net_edge >= 0 ? "+" : ""}${n(research.net_edge, 3)}` : "—"}</strong><Badge kind="research" /></div><div className="assetSpark"><Line values={series} tone={change >= 0 ? "green" : "red"} /><Badge kind="live" /></div></div>
    <div className="assetMeta"><span>CONFIDENCE<b>{research ? n(research.confidence, 2) : "—"}</b></span><span>UNCERTAINTY<b>{research ? n(research.uncertainty, 2) : "—"}</b></span><span>VOL (REALIZED)<b>{analytics ? pct(analytics.realized_volatility, 1) : "—"}</b></span><span>REGIME<b className={change >= 0 ? "good" : "bad"}>{Math.abs(change) > .01 ? "VOLATILE" : change >= 0 ? "UP BIAS" : "DOWN BIAS"}</b></span></div>
  </article>;
}

function LifecycleGrid({ rows }: { rows: LifecycleRow[] }) {
  const horizons = useMemo(() => Array.from(new Set(rows.map(r => r.expiry_horizon_minutes))).sort((a, b) => a - b).slice(0, 8), [rows]);
  return <div className="tableWrap"><table className="quantTable lifecycleTable"><thead><tr><th>HORIZON</th>{SYMBOLS.flatMap(sym => [<th key={`${sym}-m`}>{sym} MKT</th>, <th key={`${sym}-f`}>FAIR</th>, <th key={`${sym}-e`}>EDGE</th>])}</tr></thead><tbody>{horizons.length ? horizons.map(h => <tr key={h}><td>{horizon(h)}</td>{SYMBOLS.flatMap(sym => { const r = rows.find(x => x.symbol === sym && x.expiry_horizon_minutes === h); return [<td key={`${sym}-${h}-m`}>{r ? n(r.market_probability, 3) : "—"}</td>, <td key={`${sym}-${h}-f`}>{r ? n(r.model_probability, 3) : "—"}</td>, <td key={`${sym}-${h}-e`} className={(r?.net_edge ?? 0) >= 0 ? "good" : "bad"}>{r ? `${r.net_edge >= 0 ? "+" : ""}${n(r.net_edge, 3)}` : "—"}</td>]; })}</tr>) : <tr><td colSpan={10}>Research lifecycle unavailable</td></tr>}</tbody></table></div>;
}

function Reliability({ calibration }: { calibration: Calibration | null }) {
  const bars = calibration?.reliability_curve ?? [];
  if (!bars.length) return <div className="panelEmpty">No persisted labeled calibration evidence.</div>;
  return <div className="reliability">{bars.slice(0, 12).map((b, i) => <div key={i} className="relBin"><i style={{ height: `${clamp(b.mean_prediction) * 100}%` }} /><em style={{ bottom: `${clamp(b.observed_frequency) * 100}%` }} /></div>)}</div>;
}

function Positions({ positions }: { positions: Position[] }) {
  return <div className="tableWrap"><table className="quantTable"><thead><tr><th>ASSET</th><th>MARKET</th><th>SIDE</th><th>QTY</th><th>AVG PRICE</th><th>MARK</th><th>UNREALIZED P&L</th><th>REALIZED P&L</th><th>EXPOSURE</th></tr></thead><tbody>{positions.length ? positions.slice(0, 12).map((p, i) => { const side = pickText(p.side); const qty = pickNumber(p.quantity ?? p.qty); const avg = pickNumber(p.average_price ?? p.avg_price); const mark = pickNumber(p.mark_price ?? p.mark); const upnl = pickNumber(p.unrealized_pnl); const rpnl = pickNumber(p.realized_pnl); const exposure = pickNumber(p.notional ?? p.exposure); return <tr key={String(p.market_id ?? i)}><td>{pickText(p.asset)}</td><td>{pickText(p.market_id)}</td><td className={side.toLowerCase().includes("short") ? "bad" : "good"}>{side}</td><td>{qty == null ? "—" : n(qty, 4)}</td><td>{avg == null ? "—" : n(avg, 4)}</td><td>{mark == null ? "—" : n(mark, 4)}</td><td className={(upnl ?? 0) >= 0 ? "good" : "bad"}>{usd(upnl)}</td><td className={(rpnl ?? 0) >= 0 ? "good" : "bad"}>{usd(rpnl)}</td><td>{usd(exposure)}</td></tr>; }) : <tr><td colSpan={9}>No paper positions currently open.</td></tr>}</tbody></table></div>;
}

function App() {
  const [activeView, setActiveView] = useState<ViewName>("overview");
  const [selected, setSelected] = useState<SymbolName>("BTC");
  const [health, setHealth] = useState<Health | null>(null);
  const [runtimeState, setRuntimeState] = useState<RuntimeState | null>(null);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [frames, setFrames] = useState(emptyFrames);
  const [analytics, setAnalytics] = useState(emptyAnalytics);
  const [lifecycle, setLifecycle] = useState<LifecycleRow[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [pnl, setPnl] = useState<PnL | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [hawkes, setHawkes] = useState<Hawkes | null>(null);
  const [greeks, setGreeks] = useState<Greeks | null>(null);
  const [paper, setPaper] = useState<PaperStatus | null>(null);
  const [autopilot, setAutopilot] = useState<AutopilotStatus | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [riskState, setRiskState] = useState<RiskState | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [priceSeries, setPriceSeries] = useState(emptySeries);
  const [edgeSeries, setEdgeSeries] = useState(emptySeries);
  const [pnlSeries, setPnlSeries] = useState<number[]>([]);
  const [hawkesSeries, setHawkesSeries] = useState<number[]>([]);
  const [quantity, setQuantity] = useState("0.001");
  const [autoTrigger, setAutoTrigger] = useState("0.65");
  const [autoCooldown, setAutoCooldown] = useState("20");
  const [autoSpread, setAutoSpread] = useState("20");
  const [autoStopLoss, setAutoStopLoss] = useState("0.02");
  const [replayUpload, setReplayUpload] = useState<ReplayUpload | null>(null);
  const [replaySpeed, setReplaySpeed] = useState("1x");
  const [seekCursor, setSeekCursor] = useState("0");
  const [busy, setBusy] = useState<string | null>(null);
  const [actions, setActions] = useState<ActionEntry[]>([]);
  const socket = useRef<WebSocket | null>(null);

  const logAction = (label: string, ok: boolean, detail: string) => setActions(old => [{ at: new Date().toISOString(), label, ok, detail }, ...old].slice(0, 30));

  const refreshControl = async () => {
    const [runtimeResult, paperResult, autoResult, replayResult, riskResult] = await Promise.all([
      requestJson<RuntimeState>("/system/status"), requestJson<PaperStatus>("/paper/status"), requestJson<AutopilotStatus>("/paper/automation/status"), requestJson<ReplayStatus>("/replay/status"), requestJson<RiskState>("/risk"),
    ]);
    if (runtimeResult.ok) setRuntimeState(runtimeResult.data);
    if (paperResult.ok) setPaper(paperResult.data);
    if (autoResult.ok) setAutopilot(autoResult.data);
    if (replayResult.ok) setReplay(replayResult.data);
    if (riskResult.ok) setRiskState(riskResult.data);
  };

  const command = async <T,>(label: string, path: string, body?: unknown) => {
    setBusy(label);
    const result = await postJson<T>(path, body);
    logAction(label, result.ok, result.ok ? `HTTP ${result.status}` : result.error ?? `HTTP ${result.status}`);
    await refreshControl();
    setBusy(null);
    return result;
  };

  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all(SYMBOLS.map(sym => requestJson<LiveHistoryResponse>(`/live/history/${sym}?limit=${HISTORY_LIMIT}`))).then(results => {
      if (cancelled) return;
      setPriceSeries(old => {
        const next = { ...old };
        results.forEach((result, index) => {
          if (!result.ok || !result.data) return;
          const symbol = SYMBOLS[index];
          const values = result.data.history.filter(frame => frame.symbol === symbol && Number.isFinite(frame.mid) && frame.mid > 0).slice(-MAX_SERIES).map(frame => frame.mid);
          if (values.length) next[symbol] = values;
        });
        return next;
      });
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let reconnect: number | null = null;
    const ingest = (frame: LiveFrame) => {
      if (!SYMBOLS.includes(frame.symbol) || !Number.isFinite(frame.mid) || frame.mid <= 0) return;
      setFrames(old => ({ ...old, [frame.symbol]: frame }));
      setPriceSeries(old => ({ ...old, [frame.symbol]: append(old[frame.symbol], frame.mid) }));
    };
    const refresh = async () => {
      const [h, s, market, life, port, attribution, calibrationResult, ...aa] = await Promise.all([
        requestJson<Health>("/health"), requestJson<LiveStatus>("/live/status"), requestJson<LiveMarketResponse>("/live/market-data"), requestJson<LifecycleResponse>("/market-lifecycle"), requestJson<Portfolio>("/v1/portfolio"), requestJson<PnL>("/pnl/attribution"), requestJson<Calibration>("/models/calibration"), ...SYMBOLS.map(sym => requestJson<LiveAnalytics>(`/live/analytics/${sym}`)),
      ]);
      if (cancelled) return;
      if (h.ok) setHealth(h.data);
      if (s.ok) setStatus(s.data);
      if (market.ok && market.data) market.data.markets.forEach(ingest);
      if (life.ok && life.data) {
        setLifecycle(life.data.markets);
        setEdgeSeries(old => {
          const next = { ...old };
          SYMBOLS.forEach(sym => { const row = life.data!.markets.find(x => x.symbol === sym); if (row) next[sym] = append(next[sym], row.net_edge); });
          return next;
        });
      }
      if (port.ok && port.data) { setPortfolio(port.data); setPnlSeries(old => append(old, port.data!.total_pnl_after_fees)); }
      if (attribution.ok) setPnl(attribution.data);
      if (calibrationResult.ok) setCalibration(calibrationResult.data);
      aa.forEach((result, index) => { if (result.ok && result.data) setAnalytics(old => ({ ...old, [SYMBOLS[index]]: result.data as LiveAnalytics })); });
      await refreshControl();
    };
    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/ws/market-data`);
      socket.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = event => { try { const envelope = JSON.parse(event.data) as Envelope; const candidate = envelope.type === "market-data" ? envelope.data : envelope; if (candidate && typeof candidate === "object") ingest(candidate as LiveFrame); } catch { /* malformed frame ignored */ } };
      ws.onerror = () => setWsConnected(false);
      ws.onclose = () => { setWsConnected(false); if (!cancelled) reconnect = window.setTimeout(connect, 1500); };
    };
    void refresh(); connect();
    const id = window.setInterval(() => void refresh(), REST_MS);
    return () => { cancelled = true; window.clearInterval(id); if (reconnect != null) window.clearTimeout(reconnect); socket.current?.close(); };
  }, []);

  const selectedMarket = lifecycle.find(x => x.symbol === selected) ?? null;
  useEffect(() => {
    if (!selectedMarket) { setHawkes(null); setGreeks(null); return; }
    let cancelled = false;
    const load = async () => {
      const [h, g] = await Promise.all([requestJson<Hawkes>(`/hawkes/${selected}`), requestJson<Greeks>(`/analytics/greeks/${encodeURIComponent(selectedMarket.market_id)}`)]);
      if (cancelled) return;
      if (h.ok && h.data) { setHawkes(h.data); setHawkesSeries(old => append(old, h.data!.current_intensity)); } else setHawkes(null);
      setGreeks(g.ok ? g.data : null);
    };
    void load(); const id = window.setInterval(() => void load(), RESEARCH_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [selected, selectedMarket?.market_id]);

  const liveFresh = Boolean(status?.running && status.receiving_data && status.all_symbols_fresh);
  const researchBySymbol = Object.fromEntries(SYMBOLS.map(sym => [sym, lifecycle.find(x => x.symbol === sym) ?? null])) as Record<SymbolName, LifecycleRow | null>;
  const selectedAnalytics = analytics[selected];
  const selectedFrame = frames[selected];
  const positions = Array.isArray(portfolio?.positions) ? portfolio.positions : [];
  const displayRiskScore = clamp(Math.max(portfolio?.max_asset_concentration ?? 0, Math.min(Math.abs(portfolio?.realized_drawdown ?? 0) / Math.max(Math.abs(portfolio?.gross_exposure ?? 1), 1), 1)));

  const submitPaperOrder = async (side: "BUY" | "SELL") => {
    const qty = Number(quantity);
    if (!selectedFrame || !selectedAnalytics || !selectedMarket || !Number.isFinite(qty) || qty <= 0) { logAction(`${side} ${selected}`, false, "Live frame, research market and a positive quantity are required."); return; }
    const limit = side === "BUY" ? selectedFrame.ask : selectedFrame.bid;
    await command("MANUAL PAPER ORDER", "/v1/simulate", {
      order: { market_id: selectedMarket.market_id, asset: selected, side, quantity: qty, limit_price: limit },
      snapshot: { symbol: selected, market_id: selectedMarket.market_id, bid: selectedFrame.bid, ask: selectedFrame.ask, bid_size: selectedFrame.bid_size, ask_size: selectedFrame.ask_size, volatility: selectedAnalytics.realized_volatility, imbalance: selectedAnalytics.current_imbalance, market_probability: selectedMarket.market_probability, observed_at: selectedFrame.timestamp },
    });
  };

  const startAutopilot = async () => command("START AUTOPILOT", "/paper/automation/start", {
    symbol: selected,
    imbalance_trigger: Number(autoTrigger),
    cooldown_seconds: Number(autoCooldown),
    quantity: Number(quantity),
    max_spread_bps: Number(autoSpread),
    stop_loss_fraction: Number(autoStopLoss),
  });

  const onReplayFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const frames = Array.isArray(parsed) ? parsed : parsed && typeof parsed === "object" && "frames" in parsed && Array.isArray((parsed as { frames: unknown }).frames) ? (parsed as { frames: unknown[] }).frames : null;
      if (!frames?.length) throw new Error("Replay JSON must be an array of frames or an object containing frames[].");
      setReplayUpload({ frames, name: file.name });
      logAction("LOAD REPLAY FILE", true, `${file.name}: ${frames.length} frame(s)`);
    } catch (error) { logAction("LOAD REPLAY FILE", false, error instanceof Error ? error.message : "Invalid JSON"); }
  };

  const startReplay = async () => {
    if (!replayUpload) { logAction("START REPLAY", false, "Load an actual replay JSON file first."); return; }
    await command("START REPLAY", "/replay/start", { frames: replayUpload.frames, speed: replaySpeed });
  };

  const navItems: Array<{ key: ViewName; label: string; icon: string }> = [
    { key: "overview", label: "Overview", icon: "▦" }, { key: "paper", label: "Paper Trading", icon: "↕" }, { key: "research", label: "Research", icon: "◇" }, { key: "replay", label: "Replay", icon: "▶" }, { key: "risk", label: "Risk", icon: "⬡" }, { key: "system", label: "System", icon: "⚙" },
  ];

  return <main className="terminal approvedTerminal" data-feed={liveFresh ? "live" : "stale"}>
    <aside className="sideNav" aria-label="Terminal sections"><div className="sideLogo">P</div>{navItems.map(item => <button type="button" key={item.key} className={activeView === item.key ? "active" : ""} onClick={() => setActiveView(item.key)} title={item.label} aria-label={item.label}><span>{item.icon}</span><small>{item.label}</small></button>)}</aside>

    <header className="commandBar"><div className="brandWord"><b>PROTO</b><span>Prediction Market Quant Engine</span></div><Badge kind="ok">LIVE MONITORING</Badge><div className="commandMetric"><span>FEED STATUS</span><b className={liveFresh ? "good" : "warn"}>● FEED {liveFresh ? "LIVE" : "STALE"}</b></div><div className="commandMetric"><span>BACKEND</span><b className={health?.status === "ok" ? "good" : "warn"}>● {health?.status === "ok" ? "HEALTHY" : "CHECKING"}</b></div><div className="commandMetric"><span>WEBSOCKET</span><b className={wsConnected ? "good" : "warn"}>● {wsConnected ? "CONNECTED" : "RECONCILING"}</b></div><div className="commandMetric"><span>MODE</span><b>{runtimeState?.mode ?? health?.mode ?? "—"}</b></div><div className="commandMetric"><span>UTC TIME</span><b>{new Date(now).toISOString().slice(11, 19)}Z</b></div><div className="headerActions"><button type="button" className="action goodAction" disabled={busy != null} onClick={() => void command("START PAPER", "/paper/start")}>START PAPER</button><button type="button" className="action" disabled={busy != null} onClick={() => setActiveView("paper")}>TRADE DESK</button><button type="button" className="action dangerAction" disabled={busy != null} onClick={() => void command("KILL SWITCH", "/killswitch/trigger")}>KILL SWITCH</button></div></header>

    <section className="assetGrid" data-section="MARKETS">{SYMBOLS.map(sym => <button type="button" className={`marketTile assetButton ${selected === sym ? "active" : ""}`} key={sym} onClick={() => setSelected(sym)}><AssetCard symbol={sym} frame={frames[sym]} analytics={analytics[sym]} research={researchBySymbol[sym]} series={priceSeries[sym]} /></button>)}</section>

    <nav className="workspaceTabs" aria-label="Workspace views">{navItems.map(item => <button type="button" key={item.key} className={activeView === item.key ? "active" : ""} onClick={() => setActiveView(item.key)}>{item.label}</button>)}</nav>

    {activeView === "overview" && <section className="dashboardGrid">
      <article className="panel lifecyclePanel"><header>MARKET LIFECYCLE / RESOLUTION GRID <Badge kind="research" /></header><LifecycleGrid rows={lifecycle} /></article>
      <article className="panel portfolioPanel"><header>PORTFOLIO STATUS <Badge kind="paper" /></header><div className="portfolioBody"><div className="portfolioStats"><span>P&L AFTER FEES<b className={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "good" : "bad"}>{usd(portfolio?.total_pnl_after_fees)}</b></span><span>REALIZED P&L<b>{usd(portfolio?.total_realized_pnl)}</b></span><span>UNREALIZED P&L<b>{usd(portfolio?.total_unrealized_pnl)}</b></span><span>GROSS EXPOSURE<b>{usd(portfolio?.gross_exposure)}</b></span><span>NET EXPOSURE<b>{usd(portfolio?.net_exposure)}</b></span><span>OPEN POSITIONS<b>{portfolio?.open_position_count ?? "—"}</b></span></div><div className="riskDonut" style={{ background: `conic-gradient(#2ecf78 0 ${Math.max(0, 1 - displayRiskScore) * 100}%, #287dff ${Math.max(0, 1 - displayRiskScore) * 100}% 100%)` }}><div><span>DISPLAY RISK SCORE</span><strong>{n(displayRiskScore * 100, 1)}%</strong><small>COMPOSITE VIEW</small></div></div></div></article>
      <article className="panel orderFlowPanel"><header>L1 MICROSTRUCTURE <Badge kind="live" /></header><div className="metricGrid"><span>IMBALANCE<b className={(selectedAnalytics?.current_imbalance ?? 0) >= 0 ? "good" : "bad"}>{n(selectedAnalytics?.current_imbalance, 3)}</b></span><span>BEST BID SIZE<b>{n(selectedFrame?.bid_size, 5)}</b></span><span>BEST ASK SIZE<b>{n(selectedFrame?.ask_size, 5)}</b></span><span>MICROPRICE<b>{usd(selectedAnalytics?.current_microprice)}</b></span><span>REALIZED VOL<b>{pct(selectedAnalytics?.realized_volatility, 2)}</b></span><span>OBSERVATIONS<b>{selectedAnalytics?.sample_count ?? "—"}</b></span></div></article>
      <article className="panel riskPanel"><header>RISK / SAFETY <Badge kind="paper" /></header><div className="metricGrid"><span>KILL SWITCH<b className={runtimeState?.kill_switch === "ARMED" ? "good" : "bad"}>{runtimeState?.kill_switch ?? "—"}</b></span><span>PAPER ENGINE<b className={paper?.paper_execution_enabled ? "good" : "warn"}>{paper?.paper_execution_enabled ? "RUNNING" : "STOPPED"}</b></span><span>AUTOPILOT<b className={autopilot?.running ? "good" : "warn"}>{autopilot?.running ? "RUNNING" : "STOPPED"}</b></span><span>FINANCIAL CONNECTIVITY<b className="good">OFF</b></span><span>REAL MONEY EXECUTION<b className="good">OFF</b></span></div></article>
      <article className="panel edgePanel"><header>EDGE TIMELINE <Badge kind="research" /></header><div className="multiLines">{SYMBOLS.map((sym, i) => <div key={sym}><span>{sym} EDGE</span><Line values={edgeSeries[sym]} tone={i === 0 ? "orange" : i === 1 ? "blue" : "green"} height={82} /></div>)}</div></article>
      <article className="panel pnlPanel"><header>P&L CURVE <Badge kind="paper" /></header><div className="wideChart"><Line values={pnlSeries} tone={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "green" : "red"} height={100} /><div className="chartLegend"><span>CURRENT {usd(portfolio?.total_pnl_after_fees)}</span><span>FEES {usd(pnl?.fees)}</span><span>SLIPPAGE {usd(pnl?.slippage)}</span><span>RESIDUAL {usd(pnl?.residual)}</span></div></div></article>
      <article className="panel positionsPanel"><header>POSITIONS <Badge kind="paper" /></header><Positions positions={positions} /></article>
      <div className="center compatibilityCenter"><article className="panel chart compactLiveChart"><header>{selected}/USD · LIVE PUBLIC MICRO-CHART <Badge kind="live" /></header><div className="compactChartValue"><strong>{usd(selectedFrame?.mid)}</strong><span>{wsConnected ? "STREAMING" : "RECONCILING"}</span></div><Line values={priceSeries[selected]} tone={(selectedAnalytics?.simple_return ?? 0) >= 0 ? "green" : "red"} height={62} /></article></div>
    </section>}

    {activeView === "paper" && <section className="operatorGrid">
      <article className="panel controlPanel"><header>PAPER TRADING CONTROL <Badge kind="paper" /></header><div className="controlBody"><div className="stateStrip"><span>MODE<b>{paper?.mode ?? "—"}</b></span><span>ENGINE<b className={paper?.paper_execution_enabled ? "good" : "warn"}>{paper?.paper_execution_enabled ? "RUNNING" : "STOPPED"}</b></span><span>KILL SWITCH<b>{paper?.kill_switch ?? "—"}</b></span><span>AUTOPILOT<b>{paper?.autopilot_running ? "RUNNING" : "STOPPED"}</b></span></div><div className="buttonRow"><button className="primaryButton" disabled={busy != null} onClick={() => void command("START PAPER", "/paper/start")}>Start Paper Engine</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("STOP PAPER", "/paper/stop")}>Stop Paper Engine</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("RESET SIMULATION", "/simulation/reset")}>Reset Session</button></div></div></article>
      <article className="panel ticketPanel"><header>MANUAL PAPER ORDER <Badge kind="paper" /></header><div className="ticketBody"><div className="ticketQuote"><span>{selected}/USD</span><strong>{usd(selectedFrame?.mid)}</strong><small>BID {usd(selectedFrame?.bid)} · ASK {usd(selectedFrame?.ask)}</small></div><label>Quantity<input value={quantity} onChange={e => setQuantity(e.target.value)} inputMode="decimal" /></label><div className="buttonRow split"><button className="buyButton" disabled={busy != null || !paper?.paper_execution_enabled} onClick={() => void submitPaperOrder("BUY")}>BUY / PAPER</button><button className="sellButton" disabled={busy != null || !paper?.paper_execution_enabled} onClick={() => void submitPaperOrder("SELL")}>SELL / PAPER</button></div><p className="safetyNote">Server risk gates remain authoritative. Orders are simulation/paper only; no broker or exchange order is routed.</p></div></article>
      <article className="panel autopilotPanel"><header>PAPER AUTOPILOT <Badge kind="paper" /></header><div className="formGrid"><label>Symbol<select value={selected} onChange={e => setSelected(e.target.value as SymbolName)}>{SYMBOLS.map(s => <option key={s}>{s}</option>)}</select></label><label>Quantity<input value={quantity} onChange={e => setQuantity(e.target.value)} /></label><label>Imbalance trigger<input value={autoTrigger} onChange={e => setAutoTrigger(e.target.value)} /></label><label>Cooldown seconds<input value={autoCooldown} onChange={e => setAutoCooldown(e.target.value)} /></label><label>Max spread bps<input value={autoSpread} onChange={e => setAutoSpread(e.target.value)} /></label><label>Stop-loss fraction<input value={autoStopLoss} onChange={e => setAutoStopLoss(e.target.value)} /></label></div><div className="buttonRow"><button className="primaryButton" disabled={busy != null || !paper?.paper_execution_enabled} onClick={() => void startAutopilot()}>Start / Update Autopilot</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("STOP AUTOPILOT", "/paper/automation/stop")}>Stop Autopilot</button></div><div className="statusMessage"><b>LAST REASON</b><span>{autopilot?.last_reason ?? "—"}</span><small>Cycles {autopilot?.counters?.cycles ?? 0} · Signals {autopilot?.counters?.signals ?? 0} · Accepted {autopilot?.counters?.accepted ?? 0} · Rejected {autopilot?.counters?.rejected ?? 0}</small></div></article>
      <article className="panel positionsPanel wide"><header>POSITIONS <Badge kind="paper" /></header><Positions positions={positions} /></article>
    </section>}

    {activeView === "research" && <section className="researchGrid"><article className="panel lifecyclePanel"><header>MARKET LIFECYCLE / RESOLUTION GRID <Badge kind="research" /></header><LifecycleGrid rows={lifecycle} /></article><article className="panel calibrationPanel"><header>MODEL CALIBRATION <Badge kind="research">PERSISTED RESEARCH</Badge></header><div className="calMetrics"><span>BRIER SCORE<b>{n(calibration?.brier_score, 3)}</b></span><span>LOG LOSS<b>{n(calibration?.log_loss, 3)}</b></span><span>ECE<b>{n(calibration?.expected_calibration_error, 3)}</b></span><span>SAMPLE COUNT<b>{calibration?.observation_count ?? 0}</b></span></div><Reliability calibration={calibration} /></article><article className="panel hawkesPanel"><header>HAWKES CASCADE <Badge kind="research" /></header><div className="metricGrid"><span>BASELINE<b>{n(hawkes?.baseline_intensity, 4)}</b></span><span>CURRENT<b>{n(hawkes?.current_intensity, 4)}</b></span><span>EXCITATION<b>{n(hawkes?.excitation, 4)}</b></span><span>BRANCHING<b>{n(hawkes?.branching_ratio, 4)}</b></span><span>EVENT PROB<b>{pct(hawkes?.event_probability, 2)}</b></span></div><Line values={hawkesSeries} tone="purple" height={100} /></article><article className="panel greeksPanel"><header>SYNTHETIC GREEKS <Badge kind="research" /></header><div className="greekRows">{[["DELTA", greeks?.market_probability_delta], ["VEGA", greeks?.volatility_vega], ["KAPPA", greeks?.imbalance_kappa], ["THETA", greeks?.time_theta]].map(([label, value]) => <span key={String(label)}>{label}<b className={(Number(value) || 0) >= 0 ? "good" : "bad"}>{n(value as number | undefined, 4)}</b><i><em style={{ width: `${clamp(Math.abs(Number(value) || 0)) * 100}%` }} /></i></span>)}</div></article></section>}

    {activeView === "replay" && <section className="operatorGrid replayWorkspace"><article className="panel replayControl wide"><header>HISTORICAL REPLAY CONTROL <Badge kind="paper">HISTORICAL REPLAY</Badge></header><div className="controlBody"><div className="stateStrip"><span>ACTIVE<b>{replay?.active ? "YES" : "NO"}</b></span><span>STATE<b>{replay?.paused ? "PAUSED" : replay?.finished ? "FINISHED" : replay?.active ? "RUNNING" : "IDLE"}</b></span><span>CURSOR<b>{replay?.cursor ?? 0} / {replay?.total_frames ?? 0}</b></span><span>SPEED<b>{replay?.speed ?? replaySpeed}</b></span></div><div className="replayLoader"><label className="fileButton">Load Replay JSON<input type="file" accept="application/json,.json" onChange={e => void onReplayFile(e.target.files?.[0])} /></label><span>{replayUpload ? `${replayUpload.name} · ${replayUpload.frames.length} frames` : "No replay file loaded"}</span><label>Speed<select value={replaySpeed} onChange={e => setReplaySpeed(e.target.value)}>{["1x","5x","10x","50x","100x","MAX"].map(v => <option key={v}>{v}</option>)}</select></label></div><div className="buttonRow wrap"><button className="primaryButton" disabled={busy != null || !replayUpload} onClick={() => void startReplay()}>Start Replay</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("PAUSE REPLAY", "/replay/pause")}>Pause</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("RESUME REPLAY", "/replay/resume")}>Resume</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("STEP REPLAY", "/replay/step")}>Step</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("RESTART REPLAY", "/replay/restart")}>Restart</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("RESET REPLAY", "/replay/reset")}>Reset</button></div><div className="seekRow"><label>Seek cursor<input value={seekCursor} onChange={e => setSeekCursor(e.target.value)} inputMode="numeric" /></label><button className="secondaryButton" disabled={busy != null || !replay?.active} onClick={() => void command("SEEK REPLAY", "/replay/seek", { cursor: Number(seekCursor) })}>Seek</button><button className="secondaryButton" disabled={busy != null || !replay?.active} onClick={() => void command("SET REPLAY SPEED", "/replay/speed", { speed: replaySpeed })}>Apply Speed</button></div><p className="safetyNote">Replay never invents a dataset. Load an actual JSON replay payload produced by Proto or your research pipeline.</p></div></article></section>}

    {activeView === "risk" && <section className="operatorGrid"><article className="panel controlPanel"><header>RISK / SAFETY <Badge kind="paper" /></header><div className="controlBody"><div className="bigState"><span>KILL SWITCH</span><strong className={runtimeState?.kill_switch === "ARMED" ? "good" : "bad"}>{runtimeState?.kill_switch ?? "—"}</strong></div><div className="buttonRow"><button className="dangerButton" disabled={busy != null} onClick={() => void command("KILL SWITCH", "/killswitch/trigger")}>Trigger Kill Switch</button><button className="secondaryButton" disabled={busy != null} onClick={() => void command("RESET KILL SWITCH", "/killswitch/reset")}>Reset Kill Switch</button></div><p className="safetyNote">FINANCIAL CONNECTIVITY OFF · REAL MONEY EXECUTION OFF. Kill switch halts simulation, paper and replay activity.</p></div></article><article className="panel"><header>AUTHORITATIVE RISK STATE</header><pre className="jsonPanel">{JSON.stringify(riskState, null, 2)}</pre></article></section>}

    {activeView === "system" && <section className="operatorGrid"><article className="panel"><header>SYSTEM STATUS</header><div className="metricGrid systemMetrics"><span>API HEALTH<b>{health?.status ?? "—"}</b></span><span>VERSION<b>{health?.version ?? "—"}</b></span><span>RUNTIME MODE<b>{runtimeState?.mode ?? "—"}</b></span><span>RUNTIME RUNNING<b>{runtimeState?.running ? "YES" : "NO"}</b></span><span>LIVE FEED<b>{liveFresh ? "LIVE" : "STALE"}</b></span><span>WEBSOCKET<b>{wsConnected ? "CONNECTED" : "RECONCILING"}</b></span></div></article><article className="panel actionLog"><header>OPERATOR ACTION LOG</header><div className="logList">{actions.length ? actions.map((entry, i) => <div key={`${entry.at}-${i}`} className={entry.ok ? "ok" : "fail"}><time>{entry.at.slice(11, 19)}Z</time><b>{entry.label}</b><span>{entry.detail}</span></div>) : <div className="panelEmpty">No operator commands issued in this browser session.</div>}</div></article></section>}

    <footer className="systemTicker"><b>SYSTEM LOG</b><span>{new Date(now).toISOString().slice(11, 19)}Z</span><span>LIVE PUBLIC BTC/ETH/SOL</span><span>RESEARCH {lifecycle.length ? "AVAILABLE" : "UNAVAILABLE"}</span><span>PAPER {paper?.paper_execution_enabled ? "RUNNING" : "STOPPED"}</span><span>AUTOPILOT {autopilot?.running ? "RUNNING" : "STOPPED"}</span><span>FINANCIAL CONNECTIVITY OFF</span>{busy && <span className="warn">COMMAND: {busy}</span>}</footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
