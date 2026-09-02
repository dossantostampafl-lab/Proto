import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./approved-terminal.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Health = { status: string; mode: string; version: string };
type LiveStatus = { running: boolean; receiving_data: boolean; complete: boolean; all_symbols_fresh: boolean; last_receipt_age_seconds: number | null; fresh_symbols: string[]; missing_symbols: string[]; stale_symbols: string[] };
type LiveFrame = { timestamp: string; received_at?: string | null; symbol: SymbolName; bid: number; ask: number; mid: number; spread: number; bid_size: number; ask_size: number; sequence: number; connection_generation: number };
type LiveMarketResponse = { markets: LiveFrame[] };
type LiveHistoryResponse = { history: LiveFrame[] };
type LiveAnalytics = { symbol: SymbolName; sample_count: number; simple_return: number; realized_volatility: number; current_spread_bps: number; current_imbalance: number; current_microprice: number; observation_span_seconds: number };
type LifecycleRow = { market_id: string; symbol: string; market_probability: number; model_probability: number; confidence: number; uncertainty: number; net_edge: number; edge_decision: string; expiry_horizon_minutes: number };
type LifecycleResponse = { source?: string; markets: LifecycleRow[] };
type Hawkes = { current_intensity: number; baseline_intensity: number; excitation: number; branching_ratio: number; event_probability: number; decay?: number };
type Greeks = { market_probability_delta: number; volatility_vega: number; imbalance_kappa: number; time_theta: number };
type Calibration = { status: string; source: string; observation_count: number; brier_score: number | null; log_loss: number | null; expected_calibration_error: number | null; maximum_calibration_error: number | null; reliability_curve: Array<{ count: number; mean_prediction: number; observed_frequency: number; absolute_gap: number }>; computed_at?: string | null };
type Position = Record<string, unknown>;
type Portfolio = { gross_exposure: number; net_exposure: number; total_pnl_after_fees: number; total_realized_pnl?: number; total_unrealized_pnl?: number; realized_drawdown: number; max_asset_concentration: number; open_position_count: number; positions?: Position[] };
type PnL = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
type ApiResult<T> = { ok: boolean; status: number; data: T | null };
type Envelope = { type: string; data?: unknown };

const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const REST_MS = 3000;
const RESEARCH_MS = 5000;
const MAX_SERIES = 80;

const emptyFrames = (): Record<SymbolName, LiveFrame | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyAnalytics = (): Record<SymbolName, LiveAnalytics | null> => ({ BTC: null, ETH: null, SOL: null });
const emptySeries = (): Record<SymbolName, number[]> => ({ BTC: [], ETH: [], SOL: [] });
const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const append = (xs: number[], v: number) => [...xs, v].slice(-MAX_SERIES);
const n = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const usd = (v: number | null | undefined) => v == null ? "—" : `$${n(v, Math.abs(v) >= 1000 ? 2 : 4)}`;
const pct = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : `${n(v * 100, d)}%`;
const horizon = (m: number) => m < 60 ? `${m}m` : m % 1440 === 0 ? `${m / 1440}d` : `${m / 60}h`;
const pickNumber = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const pickText = (value: unknown): string => typeof value === "string" ? value : "—";

async function requestJson<T>(path: string): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: controller.signal });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function Badge({ kind, children }: { kind: "live" | "research" | "paper" | "ok"; children?: React.ReactNode }) {
  const label = children ?? (kind === "live" ? "LIVE PUBLIC" : kind === "research" ? "SYNTHETIC RESEARCH" : kind === "paper" ? "PAPER / SIM" : "HEALTHY");
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
    <div className="assetResearch">
      <div><span>MARKET PROB</span><strong>{research ? n(research.market_probability, 3) : "—"}</strong><Badge kind="research" /></div>
      <div><span>FAIR PROB</span><strong>{research ? n(research.model_probability, 3) : "—"}</strong><Badge kind="research" /></div>
      <div><span>EDGE</span><strong className={(research?.net_edge ?? 0) >= 0 ? "good" : "bad"}>{research ? `${research.net_edge >= 0 ? "+" : ""}${n(research.net_edge, 3)}` : "—"}</strong><Badge kind="research" /></div>
      <div className="assetSpark"><Line values={series} tone={change >= 0 ? "green" : "red"} /><Badge kind="live" /></div>
    </div>
    <div className="assetMeta"><span>CONFIDENCE<b>{research ? n(research.confidence, 2) : "—"}</b></span><span>UNCERTAINTY<b>{research ? n(research.uncertainty, 2) : "—"}</b></span><span>VOL (REALIZED)<b>{analytics ? pct(analytics.realized_volatility, 1) : "—"}</b></span><span>REGIME<b className={change >= 0 ? "good" : "bad"}>{Math.abs(change) > .01 ? "VOLATILE" : change >= 0 ? "UP BIAS" : "DOWN BIAS"}</b></span></div>
    <div className="assetFooter"><span>FRESHNESS <b>{frame?.received_at ? new Date(frame.received_at).toISOString().slice(11, 19) : "—"}Z</b></span><Badge kind="live" /><span>MODEL <b>{research ? horizon(research.expiry_horizon_minutes) : "—"}</b></span><Badge kind="research" /></div>
  </article>;
}

function LifecycleGrid({ rows }: { rows: LifecycleRow[] }) {
  const horizons = useMemo(() => Array.from(new Set(rows.map(r => r.expiry_horizon_minutes))).sort((a, b) => a - b).slice(0, 6), [rows]);
  return <div className="tableWrap"><table className="quantTable lifecycleTable"><thead><tr><th>HORIZON</th>{SYMBOLS.flatMap(sym => [<th key={`${sym}-m`}>{sym} MKT</th>, <th key={`${sym}-f`}>FAIR</th>, <th key={`${sym}-e`}>EDGE</th>])}</tr></thead><tbody>{horizons.length ? horizons.map(h => <tr key={h}><td>{horizon(h)}</td>{SYMBOLS.flatMap(sym => { const r = rows.find(x => x.symbol === sym && x.expiry_horizon_minutes === h); return [<td key={`${sym}-${h}-m`}>{r ? n(r.market_probability, 3) : "—"}</td>, <td key={`${sym}-${h}-f`}>{r ? n(r.model_probability, 3) : "—"}</td>, <td key={`${sym}-${h}-e`} className={(r?.net_edge ?? 0) >= 0 ? "good" : "bad"}>{r ? `${r.net_edge >= 0 ? "+" : ""}${n(r.net_edge, 3)}` : "—"}</td>]; })}</tr>) : <tr><td colSpan={10}>Research lifecycle unavailable</td></tr>}</tbody></table></div>;
}

function Reliability({ calibration }: { calibration: Calibration | null }) {
  const bars = calibration?.reliability_curve ?? [];
  if (!bars.length) return <div className="panelEmpty">No persisted labeled calibration evidence.</div>;
  return <div className="reliability">{bars.slice(0, 12).map((b, i) => <div key={i} className="relBin"><i style={{ height: `${clamp(b.mean_prediction) * 100}%` }} /><em style={{ bottom: `${clamp(b.observed_frequency) * 100}%` }} /></div>)}</div>;
}

function Positions({ positions }: { positions: Position[] }) {
  return <div className="tableWrap"><table className="quantTable"><thead><tr><th>ASSET</th><th>MARKET</th><th>SIDE</th><th>QTY</th><th>AVG PRICE</th><th>MARK</th><th>UNREALIZED P&L</th><th>REALIZED P&L</th><th>EXPOSURE</th></tr></thead><tbody>{positions.length ? positions.slice(0, 6).map((p, i) => { const side = pickText(p.side); const qty = pickNumber(p.quantity ?? p.qty); const avg = pickNumber(p.average_price ?? p.avg_price); const mark = pickNumber(p.mark_price ?? p.mark); const upnl = pickNumber(p.unrealized_pnl); const rpnl = pickNumber(p.realized_pnl); const exposure = pickNumber(p.notional ?? p.exposure); return <tr key={String(p.market_id ?? i)}><td>{pickText(p.asset)}</td><td>{pickText(p.market_id)}</td><td className={side.toLowerCase().includes("short") ? "bad" : "good"}>{side}</td><td>{qty == null ? "—" : n(qty, 4)}</td><td>{avg == null ? "—" : n(avg, 4)}</td><td>{mark == null ? "—" : n(mark, 4)}</td><td className={(upnl ?? 0) >= 0 ? "good" : "bad"}>{usd(upnl)}</td><td className={(rpnl ?? 0) >= 0 ? "good" : "bad"}>{usd(rpnl)}</td><td>{usd(exposure)}</td></tr>; }) : <tr><td colSpan={9}>No paper positions currently open.</td></tr>}</tbody></table></div>;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [frames, setFrames] = useState(emptyFrames);
  const [analytics, setAnalytics] = useState(emptyAnalytics);
  const [lifecycle, setLifecycle] = useState<LifecycleRow[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [pnl, setPnl] = useState<PnL | null>(null);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [hawkes, setHawkes] = useState<Hawkes | null>(null);
  const [greeks, setGreeks] = useState<Greeks | null>(null);
  const [selected, setSelected] = useState<SymbolName>("BTC");
  const [wsConnected, setWsConnected] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [priceSeries, setPriceSeries] = useState(emptySeries);
  const [edgeSeries, setEdgeSeries] = useState(emptySeries);
  const [pnlSeries, setPnlSeries] = useState<number[]>([]);
  const [hawkesSeries, setHawkesSeries] = useState<number[]>([]);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);

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
        requestJson<Health>("/health"), requestJson<LiveStatus>("/live/status"), requestJson<LiveMarketResponse>("/live/market-data"), requestJson<LifecycleResponse>("/market-lifecycle"), requestJson<Portfolio>("/v1/portfolio"), requestJson<PnL>("/pnl/attribution"), requestJson<Calibration>("/models/calibration"), ...SYMBOLS.map(sym => requestJson<LiveAnalytics>(`/live/analytics/${sym}`))
      ]);
      if (cancelled) return;
      if (h.ok) setHealth(h.data); if (s.ok) setStatus(s.data); market.data?.markets.forEach(ingest);
      if (life.ok && life.data) { setLifecycle(life.data.markets); setEdgeSeries(old => { const next = { ...old }; SYMBOLS.forEach(sym => { const r = life.data!.markets.find(x => x.symbol === sym); if (r) next[sym] = append(next[sym], r.net_edge); }); return next; }); }
      if (port.ok && port.data) { setPortfolio(port.data); setPnlSeries(old => append(old, port.data!.total_pnl_after_fees)); }
      if (attribution.ok) setPnl(attribution.data); if (calibrationResult.ok) setCalibration(calibrationResult.data);
      setAnalytics(old => { const next = { ...old }; aa.forEach((r, i) => { if (r.ok && r.data) next[SYMBOLS[i]] = r.data; }); return next; });
    };
    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(`${WS_BASE}/ws/market-data`); socket.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = event => { try { const msg = JSON.parse(event.data as string) as Envelope; if (msg.type === "market-data" && msg.data) ingest(msg.data as LiveFrame); } catch { /* ignore malformed frames */ } };
      ws.onerror = () => ws.close();
      ws.onclose = () => { if (cancelled) return; setWsConnected(false); reconnect = window.setTimeout(connect, 1500); };
    };
    void refresh(); connect(); const id = window.setInterval(() => void refresh(), REST_MS);
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
    void load(); const id = window.setInterval(() => void load(), RESEARCH_MS); return () => { cancelled = true; window.clearInterval(id); };
  }, [selected, selectedMarket?.market_id]);

  const liveFresh = Boolean(status?.running && status.receiving_data && status.all_symbols_fresh);
  const researchBySymbol = Object.fromEntries(SYMBOLS.map(sym => [sym, lifecycle.find(x => x.symbol === sym) ?? null])) as Record<SymbolName, LifecycleRow | null>;
  const selectedAnalytics = analytics[selected];
  const selectedFrame = frames[selected];
  const riskUsage = clamp(Math.max(portfolio?.max_asset_concentration ?? 0, Math.min(Math.abs(portfolio?.realized_drawdown ?? 0) / Math.max(Math.abs(portfolio?.gross_exposure ?? 1), 1), 1)));
  const positions = Array.isArray(portfolio?.positions) ? portfolio!.positions! : [];
  const aggressiveBuy = selectedFrame ? selectedFrame.bid_size : null;
  const aggressiveSell = selectedFrame ? selectedFrame.ask_size : null;

  return <main className="terminal approvedTerminal" data-feed={liveFresh ? "live" : "stale"}>
    <aside className="sideNav" aria-label="Terminal sections"><div className="sideLogo">P</div>{["▦", "⌁", "◇", "▤", "⬡", "◫", "ϟ", "⚙", "◌"].map((x, i) => <button type="button" key={i} className={i === 0 ? "active" : ""}>{x}</button>)}</aside>

    <header className="commandBar"><div className="brandWord"><b>PROTO</b><span>Prediction Market Quant Engine</span></div><Badge kind="ok">LIVE MONITORING</Badge><div className="commandMetric"><span>FEED STATUS</span><b className={liveFresh ? "good" : "warn"}>● FEED {liveFresh ? "LIVE" : "STALE"}</b></div><div className="commandMetric"><span>BACKEND HEALTH</span><b className={health?.status === "ok" ? "good" : "warn"}>● {health?.status === "ok" ? "HEALTHY" : "CHECKING"}</b></div><div className="commandMetric"><span>WEBSOCKET</span><b className={wsConnected ? "good" : "warn"}>● {wsConnected ? "CONNECTED" : "RECONCILING"}</b></div><div className="commandMetric"><span>FRESHNESS</span><b>{status?.last_receipt_age_seconds == null ? "—" : `${n(status.last_receipt_age_seconds * 1000, 0)} ms`}</b></div><div className="commandMetric"><span>UTC TIME</span><b>{new Date(now).toISOString().slice(11, 19)}Z</b></div><div className="freshSet">{SYMBOLS.map(sym => <span key={sym}>{sym}<b className={status?.fresh_symbols.includes(sym) ? "good" : "warn"}>● {status?.fresh_symbols.includes(sym) ? "FRESH" : "WAIT"}</b></span>)}</div></header>

    <section className="assetGrid" data-section="MARKETS">{SYMBOLS.map(sym => <button type="button" className={`marketTile assetButton ${selected === sym ? "active" : ""}`} key={sym} onClick={() => setSelected(sym)}><AssetCard symbol={sym} frame={frames[sym]} analytics={analytics[sym]} research={researchBySymbol[sym]} series={priceSeries[sym]} /></button>)}</section>

    <section className="dashboardGrid">
      <article className="panel lifecyclePanel"><header>MARKET LIFECYCLE / RESOLUTION GRID <Badge kind="research" /></header><LifecycleGrid rows={lifecycle} /></article>
      <article className="panel portfolioPanel"><header>PORTFOLIO STATUS <Badge kind="paper" /></header><div className="portfolioBody"><div className="portfolioStats"><span>EQUITY / P&L<b className={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "good" : "bad"}>{usd(portfolio?.total_pnl_after_fees)}</b></span><span>REALIZED P&L<b>{usd(portfolio?.total_realized_pnl)}</b></span><span>UNREALIZED P&L<b>{usd(portfolio?.total_unrealized_pnl)}</b></span><span>GROSS EXPOSURE<b>{usd(portfolio?.gross_exposure)}</b></span><span>NET EXPOSURE<b>{usd(portfolio?.net_exposure)}</b></span><span>DRAWDOWN<b className="bad">{usd(portfolio?.realized_drawdown)}</b></span></div><div className="riskDonut" style={{ background: `conic-gradient(#2ecf78 0 ${Math.max(0, 1 - riskUsage) * 100}%, #287dff ${Math.max(0, 1 - riskUsage) * 100}% 100%)` }}><div><span>RISK USAGE</span><strong>{n(riskUsage * 100, 1)}%</strong><small>OF LIMIT</small></div></div></div></article>
      <article className="panel orderFlowPanel"><header>ORDER FLOW <Badge kind="live" /></header><div className="orderRows"><span>IMBALANCE<b className={(selectedAnalytics?.current_imbalance ?? 0) >= 0 ? "good" : "bad"}>{selectedAnalytics ? `${selectedAnalytics.current_imbalance >= 0 ? "+" : ""}${n(selectedAnalytics.current_imbalance, 3)}` : "—"}</b></span><span>BEST BID SIZE<b className="good">{n(aggressiveBuy, 5)}</b></span><span>BEST ASK SIZE<b className="bad">{n(aggressiveSell, 5)}</b></span><span>MICROPRICE<b>{usd(selectedAnalytics?.current_microprice)}</b></span><span>REALIZED VOL<b>{pct(selectedAnalytics?.realized_volatility, 2)}</b></span><span>OBSERVATIONS<b>{selectedAnalytics?.sample_count ?? "—"}</b></span></div></article>
      <article className="panel riskPanel"><header>RISK <Badge kind="paper" /></header><div className="riskRows"><span>LIMIT UTILIZATION<i><em style={{ width: `${riskUsage * 100}%` }} /></i><b>{n(riskUsage * 100, 1)}%</b></span><span>CONCENTRATION<i><em style={{ width: `${clamp(portfolio?.max_asset_concentration ?? 0) * 100}%` }} /></i><b>{pct(portfolio?.max_asset_concentration, 1)}</b></span><span>OPEN POSITIONS<b>{portfolio?.open_position_count ?? "—"}</b></span><span>FINANCIAL CONNECTIVITY<b className="good">OFF</b></span><span>KILL SWITCH<b className="good">ARMED / SAFE</b></span></div></article>

      <article className="panel edgePanel"><header>EDGE TIMELINE <Badge kind="research" /></header><div className="multiLines">{SYMBOLS.map((sym, i) => <div key={sym}><span>{sym} EDGE</span><Line values={edgeSeries[sym]} tone={i === 0 ? "orange" : i === 1 ? "blue" : "green"} height={82} /></div>)}</div></article>
      <article className="panel pnlPanel"><header>P&L CURVE <Badge kind="paper" /></header><div className="wideChart"><Line values={pnlSeries} tone={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "green" : "red"} height={100} /><div className="chartLegend"><span>CURRENT {usd(portfolio?.total_pnl_after_fees)}</span><span>FEES {usd(pnl?.fees)}</span><span>SLIPPAGE {usd(pnl?.slippage)}</span></div></div></article>

      <article className="panel positionsPanel"><header>POSITIONS <Badge kind="paper" /></header><Positions positions={positions} /></article>
      <article className="panel calibrationPanel"><header>MODEL CALIBRATION <Badge kind="research">PERSISTED RESEARCH</Badge></header><div className="calMetrics"><span>BRIER SCORE<b>{n(calibration?.brier_score, 3)}</b></span><span>LOG LOSS<b>{n(calibration?.log_loss, 3)}</b></span><span>ECE<b>{n(calibration?.expected_calibration_error, 3)}</b></span><span>SAMPLE COUNT<b>{calibration?.observation_count ?? 0}</b></span></div><Reliability calibration={calibration} /></article>
      <article className="panel hawkesPanel"><header>HAWKES CASCADE <Badge kind="research" /></header><div className="hawkesStats"><span>BASELINE INTENSITY<b>{n(hawkes?.baseline_intensity, 4)}</b></span><span>CURRENT INTENSITY<b>{n(hawkes?.current_intensity, 4)}</b></span><span>EXCITATION<b>{n(hawkes?.excitation, 4)}</b></span><span>BRANCHING RATIO<b>{n(hawkes?.branching_ratio, 4)}</b></span></div><Line values={hawkesSeries} tone="purple" height={78} /></article>
      <article className="panel greeksPanel"><header>SYNTHETIC GREEKS <Badge kind="research" /></header><div className="greekRows">{[["DELTA", greeks?.market_probability_delta], ["VEGA", greeks?.volatility_vega], ["KAPPA", greeks?.imbalance_kappa], ["THETA", greeks?.time_theta]].map(([label, value]) => <span key={String(label)}>{label}<b className={(Number(value) || 0) >= 0 ? "good" : "bad"}>{n(value as number | undefined, 4)}</b><i><em style={{ width: `${clamp(Math.abs(Number(value) || 0)) * 100}%` }} /></i></span>)}</div></article>

      <div className="center compatibilityCenter"><article className="panel chart compactLiveChart"><header>{selected}/USD · LIVE PUBLIC MICRO-CHART <Badge kind="live" /></header><div className="compactChartValue"><strong>{usd(selectedFrame?.mid)}</strong><span>{wsConnected ? "STREAMING" : "RECONCILING"}</span></div><Line values={priceSeries[selected]} tone={(selectedAnalytics?.simple_return ?? 0) >= 0 ? "green" : "red"} height={62} /></article></div>
    </section>

    <footer className="systemTicker"><b>SYSTEM LOG</b><span>{new Date(now).toISOString().slice(11, 19)}Z</span><span>LIVE PUBLIC BTC/ETH/SOL</span><span>RESEARCH {lifecycle.length ? "AVAILABLE" : "UNAVAILABLE"}</span><span>PAPER ENGINE {health?.status === "ok" ? "READY" : "CHECKING"}</span><span>FINANCIAL CONNECTIVITY OFF</span></footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
