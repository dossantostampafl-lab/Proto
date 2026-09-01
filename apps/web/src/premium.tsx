import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ValidationPanel } from "./ValidationPanel";
import "./premium.css";
import "./validation.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Health = { status: string; mode: string; version: string };
type LiveStatus = {
  running: boolean;
  receiving_data: boolean;
  complete: boolean;
  all_symbols_fresh: boolean;
  last_receipt_age_seconds: number | null;
  fresh_symbols: string[];
  missing_symbols: string[];
  stale_symbols: string[];
};
type LiveFrame = {
  timestamp: string;
  received_at?: string | null;
  source_to_server_delta_ms?: number | null;
  symbol: SymbolName;
  bid: number;
  ask: number;
  mid: number;
  last?: number | null;
  spread: number;
  volume_24h?: number | null;
  bid_size: number;
  ask_size: number;
  sequence: number;
  connection_generation: number;
};
type LiveMarketResponse = { count: number; markets: LiveFrame[] };
type LiveAnalytics = {
  symbol: SymbolName;
  sample_count: number;
  first_mid: number;
  last_mid: number;
  simple_return: number;
  log_return: number;
  realized_volatility: number;
  average_spread_bps: number;
  current_spread_bps: number;
  current_imbalance: number;
  current_microprice: number;
  observation_span_seconds: number;
};
type Portfolio = {
  gross_exposure: number;
  net_exposure: number;
  total_pnl_after_fees: number;
  realized_drawdown: number;
  max_asset_concentration: number;
  open_position_count: number;
};
type PnLAttribution = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
type LifecycleRow = {
  market_id: string;
  symbol: string;
  market_probability: number;
  model_probability: number;
  confidence: number;
  uncertainty: number;
  net_edge: number;
  edge_decision: string;
  expiry_horizon_minutes: number;
};
type LifecycleResponse = { source?: string; markets: LifecycleRow[] };
type HawkesState = {
  current_intensity: number;
  baseline_intensity: number;
  excitation: number;
  branching_ratio: number;
  event_probability: number;
};
type SyntheticGreeks = {
  market_probability_delta: number;
  volatility_vega: number;
  imbalance_kappa: number;
  time_theta: number;
};
type StreamEnvelope<T> = { type: string; data?: T };
type ResearchState = "checking" | "available" | "disabled" | "error";
type ApiResult<T> = { ok: boolean; status: number; data: T | null };
type NumericSeries = Record<SymbolName, number[]>;
type Cursor = { generation: number; sequence: number };

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const MAX_POINTS = 180;
const REST_INTERVAL_MS = 3000;
const RECONNECT_MS = 1500;
const RESEARCH_INTERVAL_MS = 4000;
const REQUEST_TIMEOUT_MS = 2500;
const STATUS_TTL_MS = REST_INTERVAL_MS * 2.5;

const emptySeries = (): NumericSeries => ({ BTC: [], ETH: [], SOL: [] });
const emptyAnalytics = (): Record<SymbolName, LiveAnalytics | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyFrames = (): Record<SymbolName, LiveFrame | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyCursor = (): Record<SymbolName, Cursor> => ({
  BTC: { generation: -1, sequence: -1 },
  ETH: { generation: -1, sequence: -1 },
  SOL: { generation: -1, sequence: -1 },
});

function n(v: number | null | undefined, d = 2) {
  return v == null || !Number.isFinite(v)
    ? "—"
    : new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }).format(v);
}
function usd(v: number | null | undefined) { return v == null ? "—" : `$${n(v, Math.abs(v) >= 1000 ? 2 : 4)}`; }
function pct(v: number | null | undefined, d = 2) { return v == null || !Number.isFinite(v) ? "—" : `${n(v * 100, d)}%`; }
function clamp(v: number, a = 0, b = 1) { return Math.max(a, Math.min(b, v)); }
function utcClock() { return new Date().toISOString().slice(11, 19); }
function appendSeries(series: NumericSeries, symbol: SymbolName, value: number) {
  return { ...series, [symbol]: [...series[symbol], value].slice(-MAX_POINTS) };
}

async function requestJson<T>(path: string): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}${path}`, { signal: controller.signal, cache: "no-store" });
    const data = response.ok ? await response.json() as T : null;
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function Sparkline({ values, tone = "positive" }: { values: number[]; tone?: "positive" | "negative" | "info" | "research" }) {
  if (values.length < 2) return <div className="sparkEmpty">collecting</div>;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = Math.max(hi - lo, 1e-9);
  const points = values.map((v, i) => `${(i / Math.max(values.length - 1, 1)) * 100},${28 - ((v - lo) / span) * 24}`).join(" ");
  return <svg className={`sparkline ${tone}`} viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>;
}

function MicroCandles({ values }: { values: number[] }) {
  if (values.length < 8) return <div className="emptyState">Collecting unique live ticks…</div>;
  const groups: number[][] = [];
  for (let i = 0; i < values.length; i += 4) groups.push(values.slice(i, i + 4));
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = Math.max(hi - lo, 1e-9);
  const y = (v: number) => 222 - ((v - lo) / span) * 198;
  return <svg className="priceChart" viewBox="0 0 760 238" preserveAspectRatio="none" role="img" aria-label="Tick-grouped live price micro-candles">
    {[0, 1, 2, 3, 4].map((i) => <line key={i} x1="0" x2="760" y1={22 + i * 46} y2={22 + i * 46} className="chartGrid" />)}
    {groups.map((g, i) => {
      const open = g[0]; const close = g[g.length - 1]; const high = Math.max(...g); const low = Math.min(...g);
      const x = 14 + i * (730 / Math.max(groups.length - 1, 1));
      return <g key={i} className={close >= open ? "candleUp" : "candleDown"}><line x1={x} x2={x} y1={y(high)} y2={y(low)} /><rect x={x - 4} y={Math.min(y(open), y(close))} width="8" height={Math.max(2, Math.abs(y(open) - y(close)))} rx="1" /></g>;
    })}
  </svg>;
}

function FieldTorus({ rows, edge }: { rows: LifecycleRow[]; edge: number }) {
  const strength = clamp(Math.abs(edge) * 18, 0.12, 1);
  return <div className="fieldStage" style={{ "--field-strength": strength } as React.CSSProperties}>
    <svg viewBox="0 0 820 340" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Synthetic research expiry risk field">
      <defs>
        <radialGradient id="fieldGlow"><stop offset="0" stopColor="rgba(142,108,255,.22)" /><stop offset=".5" stopColor="rgba(113,167,255,.12)" /><stop offset="1" stopColor="rgba(5,8,17,0)" /></radialGradient>
        <linearGradient id="fieldStroke"><stop offset="0" stopColor="#8E6CFF" /><stop offset=".52" stopColor="#71A7FF" /><stop offset="1" stopColor="#5CE39B" /></linearGradient>
      </defs>
      <ellipse cx="410" cy="172" rx="300" ry="126" fill="url(#fieldGlow)" />
      {[0,1,2,3,4,5,6].map((i) => <ellipse key={i} cx="410" cy="172" rx={292 - i * 30} ry={122 - i * 10} fill="none" stroke="rgba(113,167,255,.12)" />)}
      <ellipse cx="410" cy="172" rx="292" ry="122" fill="none" stroke="url(#fieldStroke)" strokeWidth="2.2" opacity={0.62 + strength * .25} />
      <ellipse cx="560" cy="176" rx={92 + strength * 24} ry={98 + strength * 18} fill="rgba(232,193,90,.055)" stroke="rgba(232,193,90,.58)" strokeWidth="2" />
      {rows.slice(0, 10).map((m) => {
        const angle = clamp(m.model_probability) * Math.PI * 1.72 - Math.PI * .86;
        const radius = 178 + clamp(m.expiry_horizon_minutes / 240) * 86;
        const x = 410 + Math.cos(angle) * radius;
        const y = 172 + Math.sin(angle) * radius * .47;
        return <g key={m.market_id}><line x1={x} y1={y} x2="560" y2="176" stroke={m.net_edge >= 0 ? "rgba(92,227,155,.38)" : "rgba(240,95,101,.34)"} /><circle cx={x} cy={y} r={3 + clamp(Math.abs(m.net_edge) * 36, 0, 6)} fill={m.net_edge >= 0 ? "#5CE39B" : "#F05F65"} /></g>;
      })}
    </svg>
    <div className="fieldCore"><span>SYNTHETIC EDGE</span><strong>{rows.length ? pct(edge, 2) : "—"}</strong><small>{rows.length} mapped research markets</small></div>
  </div>;
}

function SourceBadge({ type }: { type: "live" | "research" | "paper" }) {
  const label = type === "live" ? "LIVE PUBLIC FEED" : type === "research" ? "SYNTHETIC RESEARCH" : "PAPER / SIMULATION";
  return <span className={`sourceBadge ${type}`}>{label}</span>;
}

function ResearchUnavailable({ state }: { state: ResearchState }) {
  const title = state === "checking" ? "Checking research surface" : state === "disabled" ? "Research disabled in this deployment" : "Research unavailable";
  return <div className="emptyState researchEmpty"><strong>{title}</strong><span>Live public BTC/ETH/SOL telemetry continues independently.</span></div>;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [liveStatus, setLiveStatus] = useState<LiveStatus | null>(null);
  const [statusReceivedAt, setStatusReceivedAt] = useState(0);
  const [clock, setClock] = useState(utcClock());
  const [selected, setSelected] = useState<SymbolName>("BTC");
  const [frames, setFrames] = useState<Record<SymbolName, LiveFrame | null>>(emptyFrames);
  const [analytics, setAnalytics] = useState<Record<SymbolName, LiveAnalytics | null>>(emptyAnalytics);
  const [priceHistory, setPriceHistory] = useState<NumericSeries>(emptySeries);
  const [edgeHistory, setEdgeHistory] = useState<NumericSeries>(emptySeries);
  const [hawkesHistory, setHawkesHistory] = useState<NumericSeries>(emptySeries);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [pnl, setPnl] = useState<PnLAttribution | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleResponse | null>(null);
  const [hawkes, setHawkes] = useState<HawkesState | null>(null);
  const [greeks, setGreeks] = useState<SyntheticGreeks | null>(null);
  const [researchState, setResearchState] = useState<ResearchState>("checking");
  const [wsConnected, setWsConnected] = useState(false);
  const [validationOpen, setValidationOpen] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const lastCursor = useRef<Record<SymbolName, Cursor>>(emptyCursor());

  function ingest(f: LiveFrame) {
    if (!SYMBOLS.includes(f.symbol) || !Number.isFinite(f.mid) || f.mid <= 0 || !Number.isFinite(f.sequence) || !Number.isFinite(f.connection_generation)) return;
    const previous = lastCursor.current[f.symbol];
    if (f.connection_generation < previous.generation) return;
    if (f.connection_generation === previous.generation && f.sequence <= previous.sequence) return;
    const generationChanged = previous.generation >= 0 && f.connection_generation !== previous.generation;
    lastCursor.current[f.symbol] = { generation: f.connection_generation, sequence: f.sequence };
    setFrames((p) => ({ ...p, [f.symbol]: f }));
    setPriceHistory((p) => generationChanged ? { ...p, [f.symbol]: [f.mid] } : appendSeries(p, f.symbol, f.mid));
  }

  function ingestLifecycle(body: LifecycleResponse) {
    setLifecycle(body);
    setResearchState("available");
    setEdgeHistory((previous) => {
      let next = previous;
      for (const symbol of SYMBOLS) {
        const row = body.markets.find((m) => m.symbol === symbol);
        if (row && Number.isFinite(row.net_edge)) next = appendSeries(next, symbol, row.net_edge);
      }
      return next;
    });
  }

  useEffect(() => {
    const clockTimer = window.setInterval(() => setClock(utcClock()), 1000);
    return () => window.clearInterval(clockTimer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: number | null = null;

    async function refresh() {
      const [hr, sr, lr, pr, pnr, mr, ...ar] = await Promise.all([
        requestJson<Health>("/health"),
        requestJson<LiveStatus>("/live/status"),
        requestJson<LiveMarketResponse>("/live/market-data"),
        requestJson<Portfolio>("/v1/portfolio"),
        requestJson<PnLAttribution>("/pnl/attribution"),
        requestJson<LifecycleResponse>("/market-lifecycle"),
        ...SYMBOLS.map((symbol) => requestJson<LiveAnalytics>(`/live/analytics/${symbol}`)),
      ]);
      if (cancelled) return;
      if (hr.ok && hr.data) setHealth(hr.data);
      if (sr.ok && sr.data) { setLiveStatus(sr.data); setStatusReceivedAt(Date.now()); }
      if (lr.ok && lr.data) lr.data.markets.forEach(ingest);
      if (pr.ok && pr.data) setPortfolio(pr.data);
      if (pnr.ok && pnr.data) setPnl(pnr.data);
      if (mr.ok && mr.data) ingestLifecycle(mr.data);
      else if (mr.status === 503) { setResearchState("disabled"); setLifecycle(null); }
      else if (mr.status !== 0) setResearchState("error");
      setAnalytics((previous) => {
        const next = { ...previous };
        ar.forEach((result, i) => { if (result.ok && result.data) next[SYMBOLS[i]] = result.data; });
        return next;
      });
    }

    function connect() {
      if (cancelled) return;
      const current = socket.current;
      if (current && (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)) return;
      const ws = new WebSocket(`${WS_BASE}/ws/market-data`);
      socket.current = ws;
      ws.onopen = () => { if (!cancelled && socket.current === ws) setWsConnected(true); };
      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string) as StreamEnvelope<unknown>;
          if (message.type === "market-data" && message.data) ingest(message.data as LiveFrame);
        } catch { /* REST reconciliation remains authoritative. */ }
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        if (socket.current === ws) socket.current = null;
        if (!cancelled) {
          setWsConnected(false);
          reconnectTimer = window.setTimeout(connect, RECONNECT_MS);
        }
      };
    }

    void refresh();
    connect();
    const timer = window.setInterval(() => void refresh(), REST_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket.current?.close();
      socket.current = null;
    };
  }, []);

  const markets = lifecycle?.markets ?? [];
  const selectedMarkets = markets.filter((m) => m.symbol === selected);
  const selectedMarket = selectedMarkets[0] ?? null;
  const researchAvailable = researchState === "available";

  useEffect(() => {
    if (!researchAvailable || !selectedMarket) { setHawkes(null); setGreeks(null); return; }
    let cancelled = false;
    async function load() {
      const [h, g] = await Promise.all([
        requestJson<HawkesState>(`/hawkes/${selected}`),
        requestJson<SyntheticGreeks>(`/analytics/greeks/${encodeURIComponent(selectedMarket!.market_id)}`),
      ]);
      if (cancelled) return;
      const hawkesBody = h.ok ? h.data : null;
      setHawkes(hawkesBody);
      setGreeks(g.ok ? g.data : null);
      if (hawkesBody && Number.isFinite(hawkesBody.current_intensity)) setHawkesHistory((p) => appendSeries(p, selected, hawkesBody.current_intensity));
    }
    void load();
    const timer = window.setInterval(() => void load(), RESEARCH_INTERVAL_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [researchAvailable, selected, selectedMarket?.market_id]);

  useEffect(() => {
    if (!validationOpen) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setValidationOpen(false); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [validationOpen]);

  const active = frames[selected];
  const a = analytics[selected];
  const values = priceHistory[selected];
  const edge = selectedMarket?.net_edge ?? 0;
  const sessionReturn = useMemo(() => values.length > 1 ? values[values.length - 1] / values[0] - 1 : 0, [values]);
  const statusFreshLocally = statusReceivedAt > 0 && Date.now() - statusReceivedAt < STATUS_TTL_MS;
  const feedFresh = Boolean(statusFreshLocally && liveStatus?.running && liveStatus.receiving_data && liveStatus.all_symbols_fresh);
  const feedState = feedFresh ? "STREAMING" : liveStatus?.running ? "RECONCILING" : "RECONNECTING";
  const transport = wsConnected ? "WS + REST" : "REST FALLBACK";
  const automationState = researchAvailable && feedFresh ? "READY FOR PAPER PIPELINE" : researchAvailable ? "WAITING FOR FRESH FEED" : "RESEARCH GATE CLOSED";
  const riskScore = clamp((portfolio?.max_asset_concentration ?? 0) * .55 + Math.min(Math.abs(portfolio?.realized_drawdown ?? 0) / Math.max(Math.abs(portfolio?.gross_exposure ?? 1), 1), 1) * .45);

  return <main className="protoShell" data-feed={feedFresh ? "live" : "stale"}>
    <header className="topBar">
      <div className="brand"><div className="brandMark">P</div><div><strong>PROTO</strong><span>QUANT TERMINAL</span></div></div>
      <nav className="primaryNav" aria-label="Primary"><button className="active" type="button">COMMAND</button><button type="button">MARKETS</button><button type="button">RESEARCH</button><button type="button">AUTOMATION</button><button type="button">PORTFOLIO</button><button type="button">RISK</button><button type="button">SYSTEM</button></nav>
      <div className="topStatus"><span className="modeChip">{health?.mode ?? "SIMULATION"}</span><span className={`statusDot ${feedFresh ? "ok" : "warn"}`}>● {feedState}</span><button className="killSwitch" type="button" disabled title="Read-only deployment: kill-switch control is not connected from this frontend">◇ KILL SWITCH<br/><b>ARMED</b></button><span className="clockReadout">{clock}<small>UTC</small></span></div>
    </header>

    <section className="marketStrip" aria-label="Live markets">
      <div className="stripLabel"><span className="livePulse" /> LIVE MARKETS</div>
      {SYMBOLS.map((symbol) => {
        const frame = frames[symbol]; const metric = analytics[symbol]; const history = priceHistory[symbol];
        return <button key={symbol} className={`marketCard ${selected === symbol ? "active" : ""}`} type="button" onClick={() => setSelected(symbol)} aria-pressed={selected === symbol}>
          <span><b>{symbol}/USD</b><em className={(metric?.simple_return ?? 0) >= 0 ? "positive" : "negative"}>{pct(metric?.simple_return, 2)}</em></span>
          <strong>{frame ? usd(frame.mid) : "—"}</strong>
          <small>SPREAD {metric ? `${n(metric.current_spread_bps, 2)} bp` : "—"}</small>
          <Sparkline values={history.slice(-36)} tone={(metric?.simple_return ?? 0) >= 0 ? "positive" : "negative"} />
        </button>;
      })}
      <div className="feedCard"><span>MARKET STATUS</span><b>{transport}</b><small>AGE {liveStatus?.last_receipt_age_seconds != null ? `${n(liveStatus.last_receipt_age_seconds, 2)}s` : "—"}</small><small>GEN {active?.connection_generation ?? "—"} · SEQ {active?.sequence ?? "—"}</small></div>
    </section>

    <div className="commandGrid">
      <aside className="leftRail">
        <section className="panel orderPanel"><div className="panelHead"><span>ORDER BOOK · {selected}</span><SourceBadge type="live" /></div>{active ? <><div className="book ask"><span>ASK</span><strong>{usd(active.ask)}</strong><i>{n(active.ask_size, 5)}</i></div><div className="midPrice">{usd(active.mid)}<small>{a ? `${n(a.current_spread_bps, 3)} BP` : "—"}</small></div><div className="book bid"><span>BID</span><strong>{usd(active.bid)}</strong><i>{n(active.bid_size, 5)}</i></div></> : <div className="emptyState">Waiting for live quote…</div>}</section>
        <section className="panel flowPanel"><div className="panelHead"><span>ORDER FLOW</span><SourceBadge type="live" /></div><div className="flowGauge"><div style={{ "--imb": `${clamp(((a?.current_imbalance ?? 0) + 1) / 2) * 100}%` } as React.CSSProperties}><span /></div><strong>{a ? n(a.current_imbalance, 3) : "—"}</strong><small>LIVE IMBALANCE</small></div><div className="miniRows"><span>Microprice<b>{a ? usd(a.current_microprice) : "—"}</b></span><span>Realized vol<b>{a ? pct(a.realized_volatility, 2) : "—"}</b></span><span>Samples<b>{a?.sample_count ?? "—"}</b></span></div></section>
      </aside>

      <section className="centerStage">
        <article className="panel chartPanel"><div className="panelHead"><span>{selected}/USD · TICK MICRO-CANDLES</span><div><SourceBadge type="live" /><span className="panelMeta">{transport}</span></div></div><div className="chartHero"><div><strong>{active ? usd(active.mid) : "—"}</strong><em className={sessionReturn >= 0 ? "positive" : "negative"}>{pct(sessionReturn, 2)}</em></div><small>Grouped from unique live ticks; not time-bucket OHLC.</small></div><MicroCandles values={values} /></article>
        <article className="panel fieldPanel"><div className="panelHead"><span>EXPIRY / RISK FIELD</span><SourceBadge type="research" /></div>{researchAvailable ? <><FieldTorus rows={selectedMarkets} edge={edge} /><div className="fieldMetrics"><span>MODEL P<b>{selectedMarket ? pct(selectedMarket.model_probability, 1) : "—"}</b></span><span>CONFIDENCE<b>{selectedMarket ? pct(selectedMarket.confidence, 1) : "—"}</b></span><span>UNCERTAINTY<b>{selectedMarket ? pct(selectedMarket.uncertainty, 1) : "—"}</b></span><span>NET EDGE<b className={edge >= 0 ? "positive" : "negative"}>{selectedMarket ? pct(edge, 2) : "—"}</b></span></div></> : <ResearchUnavailable state={researchState} />}</article>
      </section>

      <aside className="rightRail">
        <section className="panel edgePanel"><div className="panelHead"><span>EDGE OVER TIME</span><SourceBadge type="research" /></div>{researchAvailable ? <><Sparkline values={edgeHistory[selected]} tone="research" /><div className="miniRows"><span>Market P<b>{selectedMarket ? pct(selectedMarket.market_probability, 1) : "—"}</b></span><span>Model P<b>{selectedMarket ? pct(selectedMarket.model_probability, 1) : "—"}</b></span><span>Decision<b>{selectedMarket?.edge_decision ?? "—"}</b></span></div></> : <ResearchUnavailable state={researchState} />}</section>
        <section className="panel hawkesPanel"><div className="panelHead"><span>HAWKES CASCADE</span><SourceBadge type="research" /></div>{researchAvailable ? <><Sparkline values={hawkesHistory[selected]} tone="research" /><div className="cascade"><i style={{ width: `${clamp(hawkes?.current_intensity ?? 0, .04, 1) * 100}%` }} /><i style={{ width: `${clamp((hawkes?.excitation ?? 0) * .8, .04, 1) * 100}%` }} /><i style={{ width: `${clamp((hawkes?.branching_ratio ?? 0) * .7, .04, 1) * 100}%` }} /></div><div className="miniRows"><span>Intensity<b>{hawkes ? n(hawkes.current_intensity, 4) : "—"}</b></span><span>Branching<b>{hawkes ? n(hawkes.branching_ratio, 4) : "—"}</b></span></div></> : <ResearchUnavailable state={researchState} />}</section>
        <section className="panel greeksPanel"><div className="panelHead"><span>SYNTHETIC GREEKS</span><SourceBadge type="research" /></div>{researchAvailable ? <div className="greekGrid"><span>Δ<b>{greeks ? n(greeks.market_probability_delta, 4) : "—"}</b></span><span>ν<b>{greeks ? n(greeks.volatility_vega, 4) : "—"}</b></span><span>κ<b>{greeks ? n(greeks.imbalance_kappa, 4) : "—"}</b></span><span>θ<b>{greeks ? n(greeks.time_theta, 4) : "—"}</b></span></div> : <ResearchUnavailable state={researchState} />}</section>
      </aside>
    </div>

    <section className="lowerGrid">
      <article className="panel automationPanel"><div className="panelHead"><span>AUTOMATION & PAPER ENGINE</span><SourceBadge type="paper" /></div><div className="automationBody"><div className="automationSummary"><strong>{automationState}</strong><p>Automation is constrained to research, simulation, paper trading and replay. No exchange account or real-money execution path is exposed.</p><button type="button" onClick={() => setValidationOpen(true)}>OPEN VALIDATION LAB</button></div><div className="pipeline" aria-label="Paper automation pipeline">{[
        ["01","MARKET DATA",feedFresh ? "LIVE" : "WAIT"],
        ["02","RESEARCH SIGNAL",researchAvailable ? "READY" : "OFF"],
        ["03","RISK GATE","POLICY"],
        ["04","POSITION SIZING","PAPER"],
        ["05","EXECUTION SIMULATOR","SIM"],
        ["06","POSITION UPDATE","PAPER"],
      ].map(([step,label,state]) => <div key={step} className="pipeStep"><small>{step}</small><span>{label}</span><b>{state}</b></div>)}</div></div></article>

      <article className="panel portfolioPanel"><div className="panelHead"><span>PAPER PORTFOLIO</span><SourceBadge type="paper" /></div><div className="portfolioHero"><span>EQUITY / P&L SURFACE</span><strong className={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "positive" : "negative"}>{portfolio ? usd(portfolio.total_pnl_after_fees) : "—"}</strong></div><div className="portfolioStats"><span>Gross exposure<b>{portfolio ? usd(portfolio.gross_exposure) : "—"}</b></span><span>Net exposure<b>{portfolio ? usd(portfolio.net_exposure) : "—"}</b></span><span>Open positions<b>{portfolio?.open_position_count ?? "—"}</b></span><span>Drawdown<b>{portfolio ? usd(portfolio.realized_drawdown) : "—"}</b></span></div></article>

      <article className="panel riskPanel"><div className="panelHead"><span>RISK OVERVIEW</span><SourceBadge type="paper" /></div><div className="riskScore"><div style={{ "--risk": `${riskScore * 100}%` } as React.CSSProperties}><strong>{n(riskScore * 100, 0)}</strong><span>RISK SCORE</span></div></div><div className="riskRows"><span>Concentration<b>{portfolio ? pct(portfolio.max_asset_concentration, 1) : "—"}</b></span><span>Realized drawdown<b>{portfolio ? usd(portfolio.realized_drawdown) : "—"}</b></span><span>Financial connectivity<b>OFF</b></span></div></article>

      <article className="panel pnlPanel"><div className="panelHead"><span>P&L ATTRIBUTION</span><SourceBadge type="paper" /></div><div className="pnlBars"><span>Observed total<i style={{ width: "82%" }} /><b>{pnl ? usd(pnl.observed_total_pnl) : "—"}</b></span><span>Fees<i style={{ width: `${clamp(Math.abs(pnl?.fees ?? 0) / Math.max(Math.abs(pnl?.observed_total_pnl ?? 1), 1)) * 100}%` }} /><b>{pnl ? usd(pnl.fees) : "—"}</b></span><span>Slippage<i style={{ width: `${clamp(Math.abs(pnl?.slippage ?? 0) / Math.max(Math.abs(pnl?.observed_total_pnl ?? 1), 1)) * 100}%` }} /><b>{pnl ? usd(pnl.slippage) : "—"}</b></span><span>Residual<i style={{ width: `${clamp(Math.abs(pnl?.residual ?? 0) / Math.max(Math.abs(pnl?.observed_total_pnl ?? 1), 1)) * 100}%` }} /><b>{pnl ? usd(pnl.residual) : "—"}</b></span></div></article>
    </section>

    <footer className="tickerBar"><span className={feedFresh ? "positive" : "warning"}>● {feedState}</span>{SYMBOLS.map((symbol) => <span key={symbol}>{symbol}/USD <b>{frames[symbol] ? usd(frames[symbol]!.mid) : "—"}</b> <em className={(analytics[symbol]?.simple_return ?? 0) >= 0 ? "positive" : "negative"}>{pct(analytics[symbol]?.simple_return, 2)}</em></span>)}<span>HEALTH <b>{health?.status ?? "—"}</b></span><span>RESEARCH <b>{researchState.toUpperCase()}</b></span></footer>

    {validationOpen && <div className="validationOverlay" role="dialog" aria-modal="true" aria-label="Validation Lab"><div className="validationModal"><button type="button" className="validationClose" onClick={() => setValidationOpen(false)} aria-label="Close validation lab">×</button><ValidationPanel apiBase={API_BASE} /></div></div>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
