import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ValidationPanel } from "./ValidationPanel";
import "./terminal.css";
import "./validation.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Health = { status: string; mode: string; version: string };
type LiveStatus = { running: boolean; receiving_data: boolean; complete: boolean; all_symbols_fresh: boolean; last_receipt_age_seconds: number | null; fresh_symbols: string[]; missing_symbols: string[]; stale_symbols: string[] };
type LiveFrame = { timestamp: string; received_at?: string | null; symbol: SymbolName; bid: number; ask: number; mid: number; spread: number; bid_size: number; ask_size: number; sequence: number; connection_generation: number };
type LiveMarketResponse = { count: number; markets: LiveFrame[] };
type LiveAnalytics = { symbol: SymbolName; sample_count: number; simple_return: number; realized_volatility: number; current_spread_bps: number; current_imbalance: number; current_microprice: number; observation_span_seconds: number };
type Portfolio = { gross_exposure: number; net_exposure: number; total_pnl_after_fees: number; realized_drawdown: number; max_asset_concentration: number; open_position_count: number };
type PnL = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
type LifecycleRow = { market_id: string; symbol: string; market_probability: number; model_probability: number; confidence: number; uncertainty: number; net_edge: number; edge_decision: string; expiry_horizon_minutes: number };
type LifecycleResponse = { source?: string; markets: LifecycleRow[] };
type Hawkes = { current_intensity: number; baseline_intensity: number; excitation: number; branching_ratio: number; event_probability: number };
type Greeks = { market_probability_delta: number; volatility_vega: number; imbalance_kappa: number; time_theta: number };
type ApiResult<T> = { ok: boolean; status: number; data: T | null };
type Cursor = { generation: number; sequence: number };
type Candle = { t: number; open: number; high: number; low: number; close: number };
type CandleBook = Record<SymbolName, Candle[]>;
type Envelope = { type: string; data?: unknown };

const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const REST_MS = 3000;
const STATUS_TTL_MS = 7500;
const RESEARCH_MS = 4000;
const CANDLE_MS = 5000;
const MAX_CANDLES = 96;
const MAX_SERIES = 120;

const emptyFrames = (): Record<SymbolName, LiveFrame | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyAnalytics = (): Record<SymbolName, LiveAnalytics | null> => ({ BTC: null, ETH: null, SOL: null });
const emptyCursors = (): Record<SymbolName, Cursor> => ({ BTC: { generation: -1, sequence: -1 }, ETH: { generation: -1, sequence: -1 }, SOL: { generation: -1, sequence: -1 } });
const emptyCandles = (): CandleBook => ({ BTC: [], ETH: [], SOL: [] });
const emptySeries = (): Record<SymbolName, number[]> => ({ BTC: [], ETH: [], SOL: [] });
const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const num = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const usd = (v: number | null | undefined) => v == null ? "—" : `$${num(v, Math.abs(v) >= 1000 ? 2 : 4)}`;
const pct = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : `${num(v * 100, d)}%`;
const append = (xs: number[], v: number) => [...xs, v].slice(-MAX_SERIES);

async function requestJson<T>(path: string): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: controller.signal });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function upsertCandle(book: CandleBook, frame: LiveFrame, generationChanged: boolean): CandleBook {
  const sourceMs = Date.parse(frame.received_at || frame.timestamp);
  if (!Number.isFinite(sourceMs)) return book;
  const bucket = Math.floor(sourceMs / CANDLE_MS) * CANDLE_MS;
  const current = generationChanged ? [] : book[frame.symbol];
  const next = [...current];
  const last = next[next.length - 1];
  if (!last || last.t !== bucket) next.push({ t: bucket, open: frame.mid, high: frame.mid, low: frame.mid, close: frame.mid });
  else next[next.length - 1] = { ...last, high: Math.max(last.high, frame.mid), low: Math.min(last.low, frame.mid), close: frame.mid };
  return { ...book, [frame.symbol]: next.slice(-MAX_CANDLES) };
}

function Badge({ kind }: { kind: "live" | "research" | "paper" }) {
  return <span className={`badge ${kind}`}>{kind === "live" ? "LIVE PUBLIC" : kind === "research" ? "SYNTHETIC RESEARCH" : "PAPER / SIM"}</span>;
}

function Spark({ values, tone = "info" }: { values: number[]; tone?: "info" | "good" | "bad" | "research" }) {
  if (values.length < 2) return <div className="sparkEmpty">collecting</div>;
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  const pts = values.map((v, i) => `${i / (values.length - 1) * 100},${28 - (v - lo) / span * 24}`).join(" ");
  return <svg className={`spark ${tone}`} viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>;
}

function CandleChart({ candles }: { candles: Candle[] }) {
  if (candles.length < 3) return <div className="empty">Building 5-second OHLC buckets from live ticks…</div>;
  const lo = Math.min(...candles.map(c => c.low)), hi = Math.max(...candles.map(c => c.high)), span = Math.max(hi - lo, 1e-9);
  const y = (v: number) => 220 - (v - lo) / span * 196;
  return <svg className="candleChart" viewBox="0 0 760 238" preserveAspectRatio="none" role="img" aria-label="Live 5-second OHLC chart">
    {[0,1,2,3,4].map(i => <line key={i} x1="0" x2="760" y1={22 + i * 46} y2={22 + i * 46} className="gridLine" />)}
    {candles.map((c, i) => { const x = 12 + i * 736 / Math.max(candles.length - 1, 1); const rise = c.close >= c.open; return <g key={c.t} className={rise ? "cUp" : "cDown"}><line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} /><rect x={x - 4} y={Math.min(y(c.open), y(c.close))} width="8" height={Math.max(2, Math.abs(y(c.open)-y(c.close)))} rx="1" /></g>; })}
  </svg>;
}

function ResearchField({ rows, edge }: { rows: LifecycleRow[]; edge: number }) {
  if (!rows.length) return <div className="empty">No isolated research markets available.</div>;
  const riskTone = edge >= 0 ? "#58dda0" : "#ef646d";
  return <div className="researchField"><svg viewBox="0 0 760 300" role="img" aria-label="Synthetic expiry risk field">
    <defs>
      <linearGradient id="riskFieldGradient" x1="0" x2="1"><stop offset="0" stopColor="#8b72ff"/><stop offset=".5" stopColor="#5ad8e6"/><stop offset="1" stopColor="#e5bd61"/></linearGradient>
    </defs>
    {[0,1,2,3,4,5].map(i => <ellipse key={`orbit-${i}`} cx="370" cy="150" rx={292-i*32} ry={112-i*11} fill="none" stroke="rgba(112,169,255,.12)" />)}
    {[0,1,2,3,4,5,6].map(i => <ellipse key={`torus-${i}`} cx="370" cy="150" rx={205+i*7} ry={64+i*5} fill="none" stroke="url(#riskFieldGradient)" strokeOpacity={.34+i*.055} transform={`rotate(${i*2-6} 370 150)`} />)}
    <ellipse cx="370" cy="150" rx="144" ry="38" fill="rgba(4,7,13,.95)" stroke="rgba(90,216,230,.18)"/>
    {rows.slice(0,10).map((m, index) => { const a = clamp(m.model_probability)*Math.PI*1.7-Math.PI*.85; const r=170+clamp(m.expiry_horizon_minutes/240)*80; const x=370+Math.cos(a)*r; const y=150+Math.sin(a)*r*.45; return <g key={m.market_id}><line x1={x} y1={y} x2="370" y2="150" stroke={m.net_edge>=0?"rgba(88,221,160,.34)":"rgba(239,100,109,.34)"}/><circle cx={x} cy={y} r={index===0?5:3.5} fill={m.net_edge>=0?"#58dda0":"#ef646d"}/></g>; })}
    <circle cx="370" cy="150" r="7" fill={riskTone} opacity=".85"/>
  </svg><div className="fieldReadout"><span>NET EDGE</span><strong className={edge>=0?"good":"bad"}>{pct(edge,2)}</strong><small>{rows.length} isolated markets</small></div></div>;
}

function EdgeHistory({ values, current }: { values: number[]; current: number | null }) {
  if (values.length < 2) return <div className="empty compactEmpty">Collecting edge history…</div>;
  const lo = Math.min(...values, -0.01), hi = Math.max(...values, 0.01), span = Math.max(hi-lo, 1e-9);
  const y = (v: number) => 88 - (v-lo)/span*68;
  const points = values.map((v,i)=>`${10+i*180/Math.max(values.length-1,1)},${y(v)}`).join(" ");
  const zero = y(0);
  return <div className="edgeViz"><svg viewBox="0 0 200 100" role="img" aria-label="Synthetic net edge history">
    <line x1="10" x2="190" y1={zero} y2={zero} className="zeroLine"/>
    <polyline points={points} fill="none" className="edgeLine"/>
    <circle cx="190" cy={y(values[values.length-1])} r="3" className={values[values.length-1]>=0?"edgeDot goodDot":"edgeDot badDot"}/>
  </svg><div className="vizFooter"><span>range {pct(lo,2)} → {pct(hi,2)}</span><b className={(current??0)>=0?"good":"bad"}>{current==null?"—":pct(current,2)}</b></div></div>;
}

function HawkesCascade({ series, hawkes }: { series: number[]; hawkes: Hawkes | null }) {
  if (!hawkes) return <div className="empty compactEmpty">Hawkes state unavailable.</div>;
  const nodes = [
    {x:26,y:58,w:.55},{x:58,y:28,w:.72},{x:96,y:52,w:1},{x:134,y:28,w:.65},{x:170,y:58,w:.48},{x:58,y:82,w:.44},{x:134,y:82,w:.52}
  ];
  const intensity = clamp(hawkes.current_intensity / Math.max(hawkes.current_intensity, hawkes.baseline_intensity, 1e-9));
  return <div className="hawkesViz"><svg viewBox="0 0 196 108" role="img" aria-label="Synthetic Hawkes excitation cascade">
    {nodes.slice(1).map((n,i)=><line key={`l-${i}`} x1="96" y1="52" x2={n.x} y2={n.y} className="cascadeLink" opacity={.25+n.w*.45}/>) }
    {nodes.map((n,i)=><g key={`n-${i}`}><circle cx={n.x} cy={n.y} r={5+n.w*5} className={i===2?"cascadeNode core":"cascadeNode"}/><circle cx={n.x} cy={n.y} r={2.5} className="cascadePoint"/></g>)}
  </svg><div className="hawkesMeters"><span><i style={{width:`${intensity*100}%`}}/>intensity {num(hawkes.current_intensity,4)}</span><span><i style={{width:`${clamp(hawkes.branching_ratio)*100}%`}}/>branch {num(hawkes.branching_ratio,4)}</span><span><i style={{width:`${clamp(hawkes.event_probability)*100}%`}}/>event P {pct(hawkes.event_probability,1)}</span></div>{series.length>1&&<Spark values={series.slice(-36)} tone="research"/>}</div>;
}

function GreeksPanel({ greeks }: { greeks: Greeks | null }) {
  const items = [
    ["Δ","DELTA",greeks?.market_probability_delta],
    ["ν","VEGA",greeks?.volatility_vega],
    ["κ","KAPPA",greeks?.imbalance_kappa],
    ["θ","THETA",greeks?.time_theta],
  ] as const;
  return <div className="greeksVisual">{items.map(([symbol,label,value])=>{const magnitude=value==null?0:clamp(Math.abs(value));return <div key={label}><span>{symbol}<small>{label}</small></span><b className={(value??0)>0?"good":(value??0)<0?"bad":""}>{value==null?"—":num(value,4)}</b><i><em style={{width:`${magnitude*100}%`}}/></i></div>})}</div>;
}

function App() {
  const [health,setHealth]=useState<Health|null>(null), [status,setStatus]=useState<LiveStatus|null>(null), [statusAt,setStatusAt]=useState(0);
  const [selected,setSelected]=useState<SymbolName>("BTC"), [frames,setFrames]=useState(emptyFrames), [analytics,setAnalytics]=useState(emptyAnalytics), [candles,setCandles]=useState<CandleBook>(emptyCandles);
  const [portfolio,setPortfolio]=useState<Portfolio|null>(null), [pnl,setPnl]=useState<PnL|null>(null), [lifecycle,setLifecycle]=useState<LifecycleResponse|null>(null);
  const [hawkes,setHawkes]=useState<Hawkes|null>(null), [greeks,setGreeks]=useState<Greeks|null>(null), [edgeSeries,setEdgeSeries]=useState(emptySeries), [hawkesSeries,setHawkesSeries]=useState(emptySeries);
  const [researchState,setResearchState]=useState<"checking"|"available"|"disabled"|"error">("checking"), [wsConnected,setWsConnected]=useState(false), [validationOpen,setValidationOpen]=useState(false), [now,setNow]=useState(Date.now());
  const cursors=useRef(emptyCursors()), socket=useRef<WebSocket|null>(null), pending=useRef<Record<SymbolName,LiveFrame|null>>(emptyFrames()), flushTimer=useRef<number|null>(null), opener=useRef<HTMLElement|null>(null);

  const ingest = (frame: LiveFrame) => {
    if (!SYMBOLS.includes(frame.symbol)||!Number.isFinite(frame.mid)||frame.mid<=0||!Number.isFinite(frame.sequence)||!Number.isFinite(frame.connection_generation)) return;
    const prev=cursors.current[frame.symbol];
    if(frame.connection_generation<prev.generation || (frame.connection_generation===prev.generation&&frame.sequence<=prev.sequence)) return;
    pending.current[frame.symbol]=frame;
    if(flushTimer.current!==null) return;
    flushTimer.current=window.setTimeout(()=>{
      const batch=pending.current; pending.current=emptyFrames(); flushTimer.current=null;
      setFrames(old=>{ const next={...old}; SYMBOLS.forEach(s=>{ const f=batch[s]; if(f) next[s]=f; }); return next; });
      setCandles(old=>{ let next=old; SYMBOLS.forEach(s=>{ const f=batch[s]; if(!f) return; const prevCursor=cursors.current[s]; const generationChanged=prevCursor.generation>=0&&f.connection_generation!==prevCursor.generation; cursors.current[s]={generation:f.connection_generation,sequence:f.sequence}; next=upsertCandle(next,f,generationChanged); }); return next; });
    },120);
  };

  useEffect(()=>{ const id=window.setInterval(()=>setNow(Date.now()),1000); return()=>window.clearInterval(id); },[]);

  useEffect(()=>{
    let cancelled=false, reconnect:number|null=null;
    const refresh=async()=>{
      const [h,s,m,p,pa,l,...aa]=await Promise.all([requestJson<Health>("/health"),requestJson<LiveStatus>("/live/status"),requestJson<LiveMarketResponse>("/live/market-data"),requestJson<Portfolio>("/v1/portfolio"),requestJson<PnL>("/pnl/attribution"),requestJson<LifecycleResponse>("/market-lifecycle"),...SYMBOLS.map(x=>requestJson<LiveAnalytics>(`/live/analytics/${x}`))]);
      if(cancelled)return;
      if(h.ok&&h.data)setHealth(h.data);
      if(s.ok&&s.data){setStatus(s.data);setStatusAt(Date.now());}
      if(m.ok&&m.data)m.data.markets.forEach(ingest);
      if(p.ok&&p.data)setPortfolio(p.data);
      if(pa.ok&&pa.data)setPnl(pa.data);
      if(l.ok&&l.data){
        setLifecycle(l.data);setResearchState("available");
        setEdgeSeries(old=>{const next={...old};SYMBOLS.forEach(sym=>{const row=l.data!.markets.find(x=>x.symbol===sym);if(row&&Number.isFinite(row.net_edge))next[sym]=append(next[sym],row.net_edge)});return next;});
      } else if(l.status===503){setLifecycle(null);setResearchState("disabled");}
      else if(l.status!==0)setResearchState("error");
      setAnalytics(old=>{const next={...old};aa.forEach((r,i)=>{if(r.ok&&r.data)next[SYMBOLS[i]]=r.data});return next;});
    };
    const connect=()=>{
      if(cancelled)return;
      const ws=new WebSocket(`${WS_BASE}/ws/market-data`);socket.current=ws;
      ws.onopen=()=>!cancelled&&setWsConnected(true);
      ws.onmessage=e=>{try{const msg=JSON.parse(e.data as string) as Envelope;if(msg.type==="market-data"&&msg.data)ingest(msg.data as LiveFrame)}catch{}};
      ws.onerror=()=>ws.close();
      ws.onclose=()=>{if(!cancelled){setWsConnected(false);reconnect=window.setTimeout(connect,1500)}};
    };
    void refresh();connect();const id=window.setInterval(()=>void refresh(),REST_MS);
    return()=>{cancelled=true;window.clearInterval(id);if(reconnect!==null)window.clearTimeout(reconnect);if(flushTimer.current!==null)window.clearTimeout(flushTimer.current);socket.current?.close();};
  },[]);

  const markets=lifecycle?.markets??[], selectedMarkets=markets.filter(x=>x.symbol===selected), selectedMarket=selectedMarkets[0]??null, researchAvailable=researchState==="available";
  useEffect(()=>{
    if(!researchAvailable||!selectedMarket){setHawkes(null);setGreeks(null);return;}
    let cancelled=false;
    const load=async()=>{const[h,g]=await Promise.all([requestJson<Hawkes>(`/hawkes/${selected}`),requestJson<Greeks>(`/analytics/greeks/${encodeURIComponent(selectedMarket.market_id)}`)]);if(cancelled)return;if(h.ok&&h.data){setHawkes(h.data);setHawkesSeries(old=>({...old,[selected]:append(old[selected],h.data!.current_intensity)}));}else setHawkes(null);setGreeks(g.ok?g.data:null);};
    void load();const id=window.setInterval(()=>void load(),RESEARCH_MS);return()=>{cancelled=true;window.clearInterval(id)};
  },[researchAvailable,selected,selectedMarket?.market_id]);

  useEffect(()=>{if(!validationOpen)return;opener.current=document.activeElement instanceof HTMLElement?document.activeElement:null;const old=document.body.style.overflow;document.body.style.overflow="hidden";const key=(e:KeyboardEvent)=>{if(e.key==="Escape")setValidationOpen(false)};document.addEventListener("keydown",key);return()=>{document.body.style.overflow=old;document.removeEventListener("keydown",key);opener.current?.focus();};},[validationOpen]);

  const active=frames[selected], a=analytics[selected], edge=selectedMarket?.net_edge??0, liveFresh=Boolean(statusAt&&now-statusAt<STATUS_TTL_MS&&status?.running&&status.receiving_data&&status.all_symbols_fresh), transport=wsConnected?"WS + REST":"REST FALLBACK";
  const risk=clamp((portfolio?.max_asset_concentration??0)*.6 + Math.min(Math.abs(portfolio?.realized_drawdown??0)/Math.max(Math.abs(portfolio?.gross_exposure??1),1),1)*.4);
  const sections=["COMMAND","MARKETS","RESEARCH","AUTOMATION","PORTFOLIO","RISK","SYSTEM"];
  const go=(name:string)=>document.querySelector<HTMLElement>(`[data-section='${name}']`)?.scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"start"});

  return <main className="terminal" data-section="COMMAND" data-feed={liveFresh?"live":"stale"}>
    <header className="topbar"><div className="brand"><span>P</span><div><b>PROTO</b><small>QUANT TERMINAL</small></div></div><nav aria-label="Primary">{sections.map(s=><button key={s} type="button" onClick={()=>go(s)}>{s}</button>)}</nav><div className="systemState"><em>FEED {liveFresh?"LIVE":"STALE"}</em><b>EXEC SIMULATION</b><b className={liveFresh?"good":"warn"}>● {liveFresh?"STREAMING":"RECONCILING"}</b><span>{new Date(now).toISOString().slice(11,19)} UTC</span></div></header>

    <section className="marketStrip" data-section="MARKETS"><div className="stripLabel">LIVE MARKETS</div>{SYMBOLS.map(sym=>{const f=frames[sym],m=analytics[sym];return <button className={`marketTile ${selected===sym?"active":""}`} key={sym} onClick={()=>setSelected(sym)} type="button"><span><b>{sym}/USD</b><i className={(m?.simple_return??0)>=0?"good":"bad"}>{pct(m?.simple_return)}</i></span><strong>{f?usd(f.mid):"—"}</strong><small>spread {m?num(m.current_spread_bps,2):"—"} bp</small><Spark values={candles[sym].map(c=>c.close).slice(-30)} tone={(m?.simple_return??0)>=0?"good":"bad"}/></button>})}<div className="feedTile"><Badge kind="live"/><b>{transport}</b><small>age {status?.last_receipt_age_seconds!=null?`${num(status.last_receipt_age_seconds,2)}s`:"—"}</small><small>gen {active?.connection_generation??"—"} · seq {active?.sequence??"—"}</small></div></section>

    <section className="grid"><aside><article className="panel"><header>L1 ORDER BOOK <Badge kind="live"/></header>{active?<div className="book"><div className="ask"><span>ASK</span><b>{usd(active.ask)}</b><i>{num(active.ask_size,5)}</i></div><strong className="mid">{usd(active.mid)}</strong><div className="bid"><span>BID</span><b>{usd(active.bid)}</b><i>{num(active.bid_size,5)}</i></div></div>:<div className="empty">Waiting for live quote…</div>}</article><article className="panel"><header>ORDER FLOW <Badge kind="live"/></header><div className="flow"><div><i style={{left:`${clamp(((a?.current_imbalance??0)+1)/2)*100}%`}}/></div><strong>{a?num(a.current_imbalance,3):"—"}</strong><span>micro {a?usd(a.current_microprice):"—"}</span><span>vol {a?pct(a.realized_volatility):"—"}</span><span>samples {a?.sample_count??"—"}</span></div></article></aside>

      <div className="center"><article className="panel chart"><header>{selected}/USD · 5S OHLC <Badge kind="live"/></header><div className="quote"><strong>{active?usd(active.mid):"—"}</strong><span>{transport}</span></div><CandleChart candles={candles[selected]}/></article><article className="panel research" data-section="RESEARCH"><header>EXPIRY / RISK FIELD <Badge kind="research"/></header>{researchAvailable?<><ResearchField rows={selectedMarkets} edge={edge}/><div className="metrics"><span>MARKET P<b>{selectedMarket?pct(selectedMarket.market_probability,1):"—"}</b></span><span>MODEL P<b>{selectedMarket?pct(selectedMarket.model_probability,1):"—"}</b></span><span>CONF<b>{selectedMarket?pct(selectedMarket.confidence,1):"—"}</b></span><span>UNCERTAINTY<b>{selectedMarket?pct(selectedMarket.uncertainty,1):"—"}</b></span></div></>:<div className="empty"><b>{researchState==="disabled"?"Research disabled in live deployment":"Research unavailable"}</b><span>Live public market telemetry remains independent.</span></div>}</article></div>

      <aside className="analyticsRail"><article className="panel"><header>EDGE HISTORY <Badge kind="research"/></header>{researchAvailable?<><EdgeHistory values={edgeSeries[selected]} current={selectedMarket?.net_edge??null}/><div className="rows"><span>decision<b>{selectedMarket?.edge_decision??"—"}</b></span></div></>:<div className="empty compactEmpty">Research gate closed.</div>}</article><article className="panel"><header>HAWKES CASCADE <Badge kind="research"/></header>{researchAvailable?<HawkesCascade series={hawkesSeries[selected]} hawkes={hawkes}/>:<div className="empty compactEmpty">Research gate closed.</div>}</article><article className="panel"><header>SYNTHETIC GREEKS <Badge kind="research"/></header><GreeksPanel greeks={greeks}/></article></aside></section>

    <section className="lower"><article className="panel automation" data-section="AUTOMATION"><header>AUTOMATION & PAPER ENGINE <Badge kind="paper"/></header><div className="automationBody"><div><b>{researchAvailable&&liveFresh?"PAPER PIPELINE READY":!liveFresh?"WAITING FOR FRESH FEED":"RESEARCH GATE CLOSED"}</b><p>Research, simulation, paper trading and replay only. No exchange-account connectivity or real-money execution path is exposed.</p><button type="button" onClick={()=>setValidationOpen(true)}>OPEN VALIDATION LAB</button></div><div className="pipeline">{[["01","MARKET DATA",liveFresh?"LIVE":"WAIT"],["02","RESEARCH SIGNAL",researchAvailable?"READY":"OFF"],["03","RISK GATE","POLICY"],["04","POSITION SIZING","PAPER"],["05","EXECUTION SIMULATOR","SIM"],["06","POSITION UPDATE","PAPER"]].map(x=><div key={x[0]}><small>{x[0]}</small><span>{x[1]}</span><b>{x[2]}</b></div>)}</div></div></article>
      <article className="panel" data-section="PORTFOLIO"><header>PAPER PORTFOLIO <Badge kind="paper"/></header><div className="portfolio"><strong className={(portfolio?.total_pnl_after_fees??0)>=0?"good":"bad"}>{portfolio?usd(portfolio.total_pnl_after_fees):"—"}</strong><span>gross<b>{portfolio?usd(portfolio.gross_exposure):"—"}</b></span><span>net<b>{portfolio?usd(portfolio.net_exposure):"—"}</b></span><span>positions<b>{portfolio?.open_position_count??"—"}</b></span><span>drawdown<b>{portfolio?usd(portfolio.realized_drawdown):"—"}</b></span></div></article>
      <article className="panel" data-section="RISK"><header>RISK OVERVIEW <Badge kind="paper"/></header><div className="risk"><strong>{num(risk*100,0)}</strong><span>composite display score</span><div><i style={{width:`${risk*100}%`}}/></div><small>concentration {portfolio?pct(portfolio.max_asset_concentration,1):"—"}</small><small>financial connectivity OFF</small></div></article>
      <article className="panel"><header>P&L ATTRIBUTION <Badge kind="paper"/></header><div className="rows pnl"><span>observed<b>{pnl?usd(pnl.observed_total_pnl):"—"}</b></span><span>fees<b>{pnl?usd(pnl.fees):"—"}</b></span><span>slippage<b>{pnl?usd(pnl.slippage):"—"}</b></span><span>residual<b>{pnl?usd(pnl.residual):"—"}</b></span></div></article></section>

    <footer data-section="SYSTEM"><span>API {health?.status??"—"} · VERSION {health?.version??"—"}</span><span>LIVE PRICE / BID-ASK / SPREAD / MICROSTRUCTURE</span><span>RESEARCH {researchState.toUpperCase()} · FINANCIAL CONNECTIVITY OFF</span></footer>
    {validationOpen&&<div className="validationOverlay" role="dialog" aria-modal="true" aria-label="Validation Lab"><div className="validationModal"><button className="validationClose" type="button" onClick={()=>setValidationOpen(false)} aria-label="Close Validation Lab">×</button><ValidationPanel apiBase={API_BASE}/></div></div>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
