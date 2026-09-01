import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Health = { status: string; mode: string; version: string };
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
type LifecycleResponse = { markets: LifecycleRow[] };
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

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const MAX_POINTS = 90;

function n(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
}
function pct(value: number | null | undefined, digits = 2) {
  return value == null ? "—" : `${n(value * 100, digits)}%`;
}
function usd(value: number | null | undefined) {
  return value == null ? "—" : `$${n(value, value >= 1000 ? 2 : 4)}`;
}
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="chartEmpty">collecting live ticks…</div>;
  const w = 680, h = 190;
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, 1e-9);
  const points = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * h}`).join(" ");
  return <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" /></svg>;
}
function Meter({ value, min = -1, max = 1 }: { value: number; min?: number; max?: number }) {
  const p = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  return <div className="meter"><div style={{ width: `${p}%` }} /></div>;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [selected, setSelected] = useState<SymbolName>("BTC");
  const [frames, setFrames] = useState<Record<SymbolName, LiveFrame | null>>({ BTC: null, ETH: null, SOL: null });
  const [analytics, setAnalytics] = useState<Record<SymbolName, LiveAnalytics | null>>({ BTC: null, ETH: null, SOL: null });
  const [history, setHistory] = useState<Record<SymbolName, number[]>>({ BTC: [], ETH: [], SOL: [] });
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [pnl, setPnl] = useState<PnLAttribution | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleResponse | null>(null);
  const [hawkes, setHawkes] = useState<HawkesState | null>(null);
  const [greeks, setGreeks] = useState<SyntheticGreeks | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("—");
  const sockets = useRef<WebSocket[]>([]);

  const active = frames[selected];
  const activeAnalytics = analytics[selected];
  const activeLifecycle = lifecycle?.markets.find((row) => row.symbol === selected) ?? lifecycle?.markets[0] ?? null;

  function ingest(frame: LiveFrame) {
    setFrames((prev) => ({ ...prev, [frame.symbol]: frame }));
    setHistory((prev) => ({ ...prev, [frame.symbol]: [...prev[frame.symbol], frame.mid].slice(-MAX_POINTS) }));
    setLastUpdate(new Date().toLocaleTimeString());
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [healthRes, liveRes, portfolioRes, pnlRes, lifecycleRes] = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/live/market-data`),
          fetch(`${API_BASE}/v1/portfolio`),
          fetch(`${API_BASE}/pnl/attribution`),
          fetch(`${API_BASE}/market-lifecycle`),
        ]);
        if (healthRes.ok) setHealth(await healthRes.json());
        if (liveRes.ok) {
          const body = (await liveRes.json()) as LiveMarketResponse;
          body.markets.forEach(ingest);
        }
        if (portfolioRes.ok) setPortfolio(await portfolioRes.json());
        if (pnlRes.ok) setPnl(await pnlRes.json());
        if (lifecycleRes.ok) setLifecycle(await lifecycleRes.json());
        const analyticsEntries = await Promise.all(SYMBOLS.map(async (symbol) => {
          const r = await fetch(`${API_BASE}/live/analytics/${symbol}`);
          return [symbol, r.ok ? await r.json() : null] as const;
        }));
        if (!cancelled) setAnalytics(Object.fromEntries(analyticsEntries) as Record<SymbolName, LiveAnalytics | null>);
      } catch { /* WS remains primary once connected */ }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);

    const channels = ["market-data", "orderbook", "analytics"];
    const opened = new Set<string>();
    sockets.current = channels.map((channel) => {
      const ws = new WebSocket(`${WS_BASE}/ws/${channel}`);
      ws.onopen = () => { opened.add(channel); setStreaming(opened.has("market-data")); };
      ws.onclose = () => { opened.delete(channel); setStreaming(false); };
      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string) as StreamEnvelope<unknown>;
          if (channel === "market-data" && message.type === "market-data" && message.data) ingest(message.data as LiveFrame);
        } catch { /* ignore malformed frames */ }
      };
      return ws;
    });
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      sockets.current.forEach((ws) => ws.close());
    };
  }, []);

  useEffect(() => {
    if (!activeLifecycle) { setHawkes(null); setGreeks(null); return; }
    let cancelled = false;
    async function loadResearch() {
      const [h, g] = await Promise.all([
        fetch(`${API_BASE}/hawkes/${encodeURIComponent(selected)}`),
        fetch(`${API_BASE}/analytics/greeks/${encodeURIComponent(activeLifecycle!.market_id)}`),
      ]);
      if (!cancelled) {
        setHawkes(h.ok ? await h.json() : null);
        setGreeks(g.ok ? await g.json() : null);
      }
    }
    void loadResearch();
    return () => { cancelled = true; };
  }, [selected, activeLifecycle?.market_id]);

  const change = useMemo(() => {
    const values = history[selected];
    return values.length > 1 ? values[values.length - 1] / values[0] - 1 : 0;
  }, [history, selected]);

  return <main className="terminal">
    <header className="topbar">
      <div><div className="brand">PROTO <span>QUANT ENGINE</span></div><div className="mode">PUBLIC READ-ONLY MARKET INTELLIGENCE · PAPER / REPLAY RESEARCH</div></div>
      <div className="topStatus"><span className={streaming ? "dot live" : "dot"} />{streaming ? "STREAMING" : "RECONCILING"}<b>{lastUpdate}</b></div>
    </header>

    <section className="ticker">
      {SYMBOLS.map((symbol) => {
        const f = frames[symbol], a = analytics[symbol];
        return <button key={symbol} className={selected === symbol ? "tickerItem active" : "tickerItem"} onClick={() => setSelected(symbol)}>
          <span>{symbol}-USD</span><strong>{f ? usd(f.mid) : "—"}</strong><em className={(a?.simple_return ?? 0) >= 0 ? "positive" : "negative"}>{a ? pct(a.simple_return) : "—"}</em>
        </button>;
      })}
      <div className="tickerMeta">API {health?.status === "ok" ? "ONLINE" : "…"}<br/><small>{health?.version ?? ""}</small></div>
    </section>

    <section className="dashboardGrid">
      <article className="panel heroMarket">
        <div className="panelHead"><span>01 / LIVE MARKET</span><b>{selected}-USD</b></div>
        <div className="marketHeadline"><div><small>MID PRICE</small><h1>{active ? usd(active.mid) : "—"}</h1><div className={change >= 0 ? "positive" : "negative"}>{pct(change)} session</div></div><div className="quote"><span>BID<b>{active ? usd(active.bid) : "—"}</b></span><span>ASK<b>{active ? usd(active.ask) : "—"}</b></span><span>SPREAD<b>{activeAnalytics ? `${n(activeAnalytics.current_spread_bps, 3)} bp` : "—"}</b></span></div></div>
        <Sparkline values={history[selected]} />
        <div className="chartFooter"><span>{activeAnalytics?.sample_count ?? 0} ticks</span><span>{activeAnalytics ? `${n(activeAnalytics.observation_span_seconds, 0)}s window` : "—"}</span><span>latency {active?.source_to_server_delta_ms != null ? `${n(active.source_to_server_delta_ms, 1)}ms` : "—"}</span></div>
      </article>

      <article className="panel microstructure">
        <div className="panelHead"><span>02 / MICROSTRUCTURE</span><b>L1</b></div>
        <div className="metricLarge"><small>ORDER IMBALANCE</small><strong>{activeAnalytics ? n(activeAnalytics.current_imbalance, 4) : "—"}</strong></div>
        <Meter value={activeAnalytics?.current_imbalance ?? 0} />
        <div className="twoCol"><span>Bid size<b>{active ? n(active.bid_size, 5) : "—"}</b></span><span>Ask size<b>{active ? n(active.ask_size, 5) : "—"}</b></span><span>Microprice<b>{activeAnalytics ? usd(activeAnalytics.current_microprice) : "—"}</b></span><span>Volume 24h<b>{active?.volume_24h != null ? n(active.volume_24h, 2) : "—"}</b></span></div>
        <div className="depthViz"><div className="bidDepth" style={{flex: Math.max(active?.bid_size ?? 0, .001)}}/><div className="askDepth" style={{flex: Math.max(active?.ask_size ?? 0, .001)}}/></div>
      </article>

      <article className="panel modelPanel">
        <div className="panelHead"><span>03 / PROBABILITY & EDGE</span><b>{activeLifecycle ? activeLifecycle.edge_decision : "RESEARCH"}</b></div>
        <div className="probabilityRing"><div><strong>{activeLifecycle ? pct(activeLifecycle.model_probability, 1) : "—"}</strong><small>MODEL P</small></div></div>
        <div className="twoCol"><span>Market P<b>{activeLifecycle ? pct(activeLifecycle.market_probability, 1) : "—"}</b></span><span>Net edge<b className={(activeLifecycle?.net_edge ?? 0) >= 0 ? "positive" : "negative"}>{activeLifecycle ? pct(activeLifecycle.net_edge, 2) : "—"}</b></span><span>Confidence<b>{activeLifecycle ? pct(activeLifecycle.confidence, 1) : "—"}</b></span><span>Uncertainty<b>{activeLifecycle ? pct(activeLifecycle.uncertainty, 1) : "—"}</b></span></div>
      </article>

      <article className="panel volatility">
        <div className="panelHead"><span>04 / VOLATILITY</span><b>REALIZED</b></div>
        <div className="metricLarge"><small>σ REALIZED</small><strong>{activeAnalytics ? pct(activeAnalytics.realized_volatility, 3) : "—"}</strong></div>
        <div className="twoCol"><span>Return<b className={(activeAnalytics?.simple_return ?? 0) >= 0 ? "positive" : "negative"}>{activeAnalytics ? pct(activeAnalytics.simple_return, 3) : "—"}</b></span><span>Avg spread<b>{activeAnalytics ? `${n(activeAnalytics.average_spread_bps, 3)} bp` : "—"}</b></span><span>Log return<b>{activeAnalytics ? n(activeAnalytics.log_return, 6) : "—"}</b></span><span>Sequence<b>{active?.sequence ?? "—"}</b></span></div>
      </article>

      <article className="panel hawkes">
        <div className="panelHead"><span>05 / HAWKES CASCADE</span><b>EVENT INTENSITY</b></div>
        <div className="pulseField"><div className="pulse p1"/><div className="pulse p2"/><div className="pulse p3"/><strong>{hawkes ? n(hawkes.current_intensity, 4) : "—"}</strong></div>
        <div className="twoCol"><span>Baseline<b>{hawkes ? n(hawkes.baseline_intensity, 4) : "—"}</b></span><span>Excitation<b>{hawkes ? n(hawkes.excitation, 4) : "—"}</b></span><span>Branching<b>{hawkes ? n(hawkes.branching_ratio, 4) : "—"}</b></span><span>Event P<b>{hawkes ? pct(hawkes.event_probability, 2) : "—"}</b></span></div>
      </article>

      <article className="panel greeks">
        <div className="panelHead"><span>06 / SYNTHETIC GREEKS</span><b>FIELD</b></div>
        <div className="greekGrid"><span>Δ<strong>{greeks ? n(greeks.market_probability_delta, 5) : "—"}</strong></span><span>ν<strong>{greeks ? n(greeks.volatility_vega, 5) : "—"}</strong></span><span>κ<strong>{greeks ? n(greeks.imbalance_kappa, 5) : "—"}</strong></span><span>θ<strong>{greeks ? n(greeks.time_theta, 5) : "—"}</strong></span></div>
      </article>

      <article className="panel portfolioPanel">
        <div className="panelHead"><span>07 / PORTFOLIO & RISK</span><b>PAPER</b></div>
        <div className="riskStrip"><span>Gross<b>{portfolio ? usd(portfolio.gross_exposure) : "—"}</b></span><span>Net<b>{portfolio ? usd(portfolio.net_exposure) : "—"}</b></span><span>P&L<b className={(portfolio?.total_pnl_after_fees ?? 0) >= 0 ? "positive" : "negative"}>{portfolio ? usd(portfolio.total_pnl_after_fees) : "—"}</b></span><span>Drawdown<b>{portfolio ? usd(portfolio.realized_drawdown) : "—"}</b></span><span>Concentration<b>{portfolio ? pct(portfolio.max_asset_concentration, 1) : "—"}</b></span><span>Positions<b>{portfolio?.open_position_count ?? 0}</b></span></div>
      </article>

      <article className="panel attribution">
        <div className="panelHead"><span>08 / P&L ATTRIBUTION</span><b>CANONICAL</b></div>
        <div className="riskStrip"><span>Observed<b>{pnl ? usd(pnl.observed_total_pnl) : "—"}</b></span><span>Fees<b>{pnl ? usd(pnl.fees) : "—"}</b></span><span>Slippage<b>{pnl ? usd(pnl.slippage) : "—"}</b></span><span>Residual<b>{pnl ? usd(pnl.residual) : "—"}</b></span></div>
      </article>
    </section>

    <footer><span>LIVE SOURCE: PUBLIC READ-ONLY</span><span>FINANCIAL CONNECTIVITY: DISABLED</span><span>REAL-MONEY EXECUTION: DISABLED</span></footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
