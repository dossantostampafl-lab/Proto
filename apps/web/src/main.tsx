import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Health = { status: string; mode: string; version: string };
type LiveFrame = { timestamp: string; received_at?: string | null; source_to_server_delta_ms?: number | null; symbol: SymbolName; bid: number; ask: number; mid: number; last?: number | null; spread: number; volume_24h?: number | null; bid_size: number; ask_size: number; sequence: number };
type LiveMarketResponse = { count: number; markets: LiveFrame[] };
type LiveAnalytics = { symbol: SymbolName; sample_count: number; first_mid: number; last_mid: number; simple_return: number; log_return: number; realized_volatility: number; average_spread_bps: number; current_spread_bps: number; current_imbalance: number; current_microprice: number; observation_span_seconds: number };
type Portfolio = { gross_exposure: number; net_exposure: number; total_pnl_after_fees: number; realized_drawdown: number; max_asset_concentration: number; open_position_count: number };
type PnLAttribution = { fees: number; slippage: number; residual: number; observed_total_pnl: number };
type LifecycleRow = { market_id: string; symbol: string; market_probability: number; model_probability: number; confidence: number; uncertainty: number; net_edge: number; edge_decision: string; expiry_horizon_minutes: number };
type LifecycleResponse = { markets: LifecycleRow[] };
type HawkesState = { current_intensity: number; baseline_intensity: number; excitation: number; branching_ratio: number; event_probability: number };
type SyntheticGreeks = { market_probability_delta: number; volatility_vega: number; imbalance_kappa: number; time_theta: number };
type StreamEnvelope<T> = { type: string; data?: T };

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const WS_BASE = API_BASE.replace(/^http/, "ws");
const SYMBOLS: SymbolName[] = ["BTC", "ETH", "SOL"];
const MAX_POINTS = 120;

function n(v: number | null | undefined, d = 2) { return v == null || Number.isNaN(v) ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }).format(v); }
function pct(v: number | null | undefined, d = 2) { return v == null ? "—" : `${n(v * 100, d)}%`; }
function usd(v: number | null | undefined) { return v == null ? "—" : `$${n(v, v >= 1000 ? 2 : 4)}`; }
function clamp(v: number, a = 0, b = 1) { return Math.max(a, Math.min(b, v)); }

function Candles({ values }: { values: number[] }) {
  if (values.length < 8) return <div className="waiting">COLLECTING LIVE TICKS…</div>;
  const groups: number[][] = [];
  for (let i = 0; i < values.length; i += 4) groups.push(values.slice(i, i + 4));
  const all = groups.flat(); const lo = Math.min(...all); const hi = Math.max(...all); const span = Math.max(hi - lo, 1e-9);
  const y = (v: number) => 190 - ((v - lo) / span) * 168;
  return <svg className="candleChart" viewBox="0 0 620 205" preserveAspectRatio="none">
    <defs><linearGradient id="glow" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="rgba(126,249,193,.18)"/><stop offset="1" stopColor="rgba(126,249,193,0)"/></linearGradient></defs>
    {[0,1,2,3].map(i => <line key={i} x1="0" x2="620" y1={25+i*45} y2={25+i*45} className="gridLine"/>)}
    {groups.map((g,i) => { const open=g[0], close=g[g.length-1], high=Math.max(...g), low=Math.min(...g); const x=12+i*(596/Math.max(groups.length-1,1)); const up=close>=open; return <g key={i} className={up?"cUp":"cDown"}><line x1={x} x2={x} y1={y(high)} y2={y(low)}/><rect x={x-4} y={Math.min(y(open),y(close))} width="8" height={Math.max(2,Math.abs(y(open)-y(close))}/></g>; })}
  </svg>;
}

function AreaLine({ values, positive = true }: { values: number[]; positive?: boolean }) {
  if (values.length < 2) return <div className="waiting">WAITING</div>;
  const lo=Math.min(...values), hi=Math.max(...values), span=Math.max(hi-lo,1e-9); const w=500,h=130;
  const pts=values.map((v,i)=>`${(i/(values.length-1))*w},${h-((v-lo)/span)*h}`).join(" ");
  const area=`0,${h} ${pts} ${w},${h}`;
  return <svg className={positive?"areaLine positiveLine":"areaLine amberLine"} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"><polygon points={area}/><polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5"/></svg>;
}

function Torus({ lifecycle, edge }: { lifecycle: LifecycleRow[]; edge: number }) {
  const strength=clamp(Math.abs(edge)*18,0.15,1);
  return <div className="torusWrap"><svg className="torus" viewBox="0 0 900 470" preserveAspectRatio="xMidYMid meet">
    <defs>
      <radialGradient id="torusFill"><stop offset="0" stopColor={`rgba(232,193,90,${.14+.18*strength})`}/><stop offset=".45" stopColor={`rgba(92,227,155,${.12+.15*strength})`}/><stop offset="1" stopColor="rgba(55,93,164,.035)"/></radialGradient>
      <filter id="blur"><feGaussianBlur stdDeviation="8"/></filter>
    </defs>
    <ellipse cx="465" cy="236" rx="298" ry="150" fill="url(#torusFill)" stroke="rgba(113,167,255,.22)" strokeWidth="1"/>
    {[0,1,2,3,4,5,6].map(i=><ellipse key={`e${i}`} cx="465" cy="236" rx={298-i*28} ry={150-i*13} fill="none" stroke="rgba(113,167,255,.11)" strokeWidth="1"/>)}
    {Array.from({length:18}).map((_,i)=>{const a=(i/18)*Math.PI*2;const x=465+Math.cos(a)*285;const y=236+Math.sin(a)*142;return <line key={i} x1="465" y1="236" x2={x} y2={y} stroke="rgba(86,166,217,.09)"/>})}
    <ellipse cx="610" cy="245" rx={105+strength*35} ry={118+strength*20} fill="rgba(240,95,101,.09)" stroke="rgba(232,193,90,.65)" strokeWidth="3"/>
    <ellipse cx="610" cy="245" rx={82+strength*25} ry={93+strength*15} fill="none" stroke="rgba(92,227,155,.5)" strokeWidth="2"/>
    {lifecycle.slice(0,8).map((m,i)=>{const x=240+i*68;const y=160+Math.sin(i*.9)*62;return <g key={m.market_id}><line x1={x} y1={y} x2={610} y2={245} stroke={m.net_edge>=0?"rgba(92,227,155,.45)":"rgba(240,95,101,.4)"}/><circle cx={x} cy={y} r={3+clamp(Math.abs(m.net_edge)*40,0,5)} fill={m.net_edge>=0?"#5ce39b":"#f05f65"}/></g>})}
    <circle cx="610" cy="245" r={46+strength*16} fill="rgba(232,193,90,.08)" filter="url(#blur)"/>
  </svg><div className="torusCore"><small>EDGE FIELD</small><strong>{pct(edge,2)}</strong><span>{lifecycle.length} markets mapped</span></div></div>;
}

function GreeksField({ g, values }: { g: SyntheticGreeks | null; values: number[] }) {
  const vals=[g?.market_probability_delta??0,g?.volatility_vega??0,g?.imbalance_kappa??0,g?.time_theta??0];
  return <div className="fieldWrap"><svg viewBox="0 0 520 210" preserveAspectRatio="none" className="fieldSvg">
    {[0,1,2,3,4].map(i=><line key={i} x1="0" x2="520" y1={35+i*35} y2={35+i*35} className="gridLine"/>)}
    {Array.from({length:18}).map((_,i)=>{const y=105+(i-9)*5;const power=vals[i%4];const end=480-Math.abs(power)*90;return <line key={i} x1="15" y1={y} x2={end} y2={105+(i-9)*1.2} stroke={power>=0?"rgba(92,227,155,.55)":"rgba(240,95,101,.48)"} strokeWidth={1+Math.abs(power)*4}/>})}
    {values.slice(-10).map((v,i)=>{const base=values[values.length-10]||v;const x=150+i*32;const r=4+clamp(Math.abs(v/base-1)*220,0,10);return <circle key={i} cx={x} cy={108+i%3*7} r={r} fill="none" stroke="rgba(232,193,90,.8)" strokeWidth="2"/>})}
  </svg></div>;
}

function App() {
  const [health,setHealth]=useState<Health|null>(null); const [selected,setSelected]=useState<SymbolName>("BTC");
  const [frames,setFrames]=useState<Record<SymbolName,LiveFrame|null>>({BTC:null,ETH:null,SOL:null});
  const [analytics,setAnalytics]=useState<Record<SymbolName,LiveAnalytics|null>>({BTC:null,ETH:null,SOL:null});
  const [history,setHistory]=useState<Record<SymbolName,number[]>>({BTC:[],ETH:[],SOL:[]});
  const [portfolio,setPortfolio]=useState<Portfolio|null>(null); const [pnl,setPnl]=useState<PnLAttribution|null>(null);
  const [lifecycle,setLifecycle]=useState<LifecycleResponse|null>(null); const [hawkes,setHawkes]=useState<HawkesState|null>(null); const [greeks,setGreeks]=useState<SyntheticGreeks|null>(null);
  const [streaming,setStreaming]=useState(false); const [lastUpdate,setLastUpdate]=useState("—"); const sockets=useRef<WebSocket[]>([]);
  const active=frames[selected], a=analytics[selected], markets=lifecycle?.markets??[];
  const selectedMarket=markets.find(m=>m.symbol===selected)??markets[0]??null;
  const selectedMarkets=markets.filter(m=>m.symbol===selected); const edge=selectedMarket?.net_edge??0;

  function ingest(f:LiveFrame){setFrames(p=>({...p,[f.symbol]:f}));setHistory(p=>({...p,[f.symbol]:[...p[f.symbol],f.mid].slice(-MAX_POINTS)}));setLastUpdate(new Date().toLocaleTimeString());}

  useEffect(()=>{let cancelled=false;async function refresh(){try{const [hr,lr,pr,pnr,mr]=await Promise.all([fetch(`${API_BASE}/health`),fetch(`${API_BASE}/live/market-data`),fetch(`${API_BASE}/v1/portfolio`),fetch(`${API_BASE}/pnl/attribution`),fetch(`${API_BASE}/market-lifecycle`)]);if(hr.ok)setHealth(await hr.json());if(lr.ok){const b=await lr.json() as LiveMarketResponse;b.markets.forEach(ingest);}if(pr.ok)setPortfolio(await pr.json());if(pnr.ok)setPnl(await pnr.json());if(mr.ok)setLifecycle(await mr.json());const entries=await Promise.all(SYMBOLS.map(async s=>{const r=await fetch(`${API_BASE}/live/analytics/${s}`);return [s,r.ok?await r.json():null] as const;}));if(!cancelled)setAnalytics(Object.fromEntries(entries) as Record<SymbolName,LiveAnalytics|null>);}catch{}}
    void refresh();const timer=window.setInterval(()=>void refresh(),3000);const opened=new Set<string>();sockets.current=["market-data","orderbook","analytics"].map(ch=>{const ws=new WebSocket(`${WS_BASE}/ws/${ch}`);ws.onopen=()=>{opened.add(ch);if(ch==="market-data")setStreaming(true)};ws.onclose=()=>{opened.delete(ch);if(ch==="market-data")setStreaming(false)};ws.onmessage=e=>{try{const m=JSON.parse(e.data as string) as StreamEnvelope<unknown>;if(ch==="market-data"&&m.type==="market-data"&&m.data)ingest(m.data as LiveFrame);}catch{}};return ws});return()=>{cancelled=true;clearInterval(timer);sockets.current.forEach(w=>w.close())}},[]);

  useEffect(()=>{if(!selectedMarket){setHawkes(null);setGreeks(null);return;}let cancelled=false;async function load(){const [h,g]=await Promise.all([fetch(`${API_BASE}/hawkes/${selected}`),fetch(`${API_BASE}/analytics/greeks/${encodeURIComponent(selectedMarket!.market_id)}`)]);if(!cancelled){setHawkes(h.ok?await h.json():null);setGreeks(g.ok?await g.json():null)}}void load();const t=setInterval(()=>void load(),4000);return()=>{cancelled=true;clearInterval(t)}},[selected,selectedMarket?.market_id]);

  const values=history[selected]; const sessionReturn=useMemo(()=>values.length>1?values.at(-1)!/values[0]-1:0,[values]);
  const edgeSeries=useMemo(()=>values.length<2?[]:values.map((v,i)=>i? v/values[i-1]-1:0),[values]);
  const hawkesSeries=useMemo(()=>edgeSeries.slice(-60).map((v,i)=>Math.abs(v)*(1+(hawkes?.current_intensity??0))*10000+i*.002),[edgeSeries,hawkes]);

  return <main className="terminal">
    <header className="commandBar"><div className="logoBox">PQE</div><div className="commandTitle"><b>PROTO // QUANT TERMINAL</b><span>HFT DIRECTIONAL · HEDGE · FAIR PROBABILITY · PUBLIC READ-ONLY</span></div><div className="commandMetrics"><span>BTC <b>{frames.BTC?usd(frames.BTC.mid):"—"}</b></span><span>MKT P <b>{selectedMarket?pct(selectedMarket.market_probability,1):"—"}</b></span><span>FAIR P <b>{selectedMarket?pct(selectedMarket.model_probability,1):"—"}</b></span><span>EDGE <b className={edge>=0?"positive":"negative"}>{pct(edge,2)}</b></span></div><div className="clock"><b>{lastUpdate}</b><span>UTC/LIVE</span></div></header>
    <div className="alertTape"><span className="liveFlag">● LIVE FEED</span><div>EDGE FIRE · {selected} · MKT {selectedMarket?pct(selectedMarket.market_probability,0):"—"} VS FAIR {selectedMarket?pct(selectedMarket.model_probability,0):"—"} · EDGE {pct(edge,2)} · VOL {a?pct(a.realized_volatility,2):"—"} · SPREAD {a?`${n(a.current_spread_bps,2)}BP`:"—"}</div></div>

    <section className="topMatrix">
      <article className="frame portfolioBox"><div className="frameTitle">◆ PAPER PORTFOLIO <span>{streaming?"ACTIVE":"SYNC"}</span></div><div className="pnlHero" data-sign={(portfolio?.total_pnl_after_fees??0)>=0?"plus":"minus"}>{portfolio?usd(portfolio.total_pnl_after_fees):"—"}</div><div className="tinyStats"><span>GROSS<b>{portfolio?usd(portfolio.gross_exposure):"—"}</b></span><span>NET<b>{portfolio?usd(portfolio.net_exposure):"—"}</b></span><span>POSITIONS<b>{portfolio?.open_position_count??0}</b></span><span>DRAWDOWN<b>{portfolio?usd(portfolio.realized_drawdown):"—"}</b></span></div><div className="label">INVENTORY / FLOW</div><div className="bar"><i style={{width:`${clamp(portfolio?.max_asset_concentration??0)*100}%`}}/></div><div className="label">SESSION MIX</div><div className="mix">{SYMBOLS.map(s=><span key={s}>{s} {analytics[s]?pct(Math.abs(analytics[s]!.simple_return)/(SYMBOLS.reduce((q,k)=>q+Math.abs(analytics[k]?.simple_return??0),0)||1),0):"—"}</span>)}</div><div className="paperFoot">REAL MONEY EXECUTION DISABLED</div></article>

      <article className="frame marketBox"><div className="frameTitle">◆ {selected} SPOT · MODEL FEED <span>{streaming?"LIVE":"RECONCILING"}</span></div><div className="marketTop"><div><small>{selected}/USD · LIVE</small><strong>{active?usd(active.mid):"—"}</strong><em className={sessionReturn>=0?"positive":"negative"}>{pct(sessionReturn,2)}</em></div><div className="symbolTabs">{SYMBOLS.map(s=><button key={s} onClick={()=>setSelected(s)} className={selected===s?"active":""}>{s}</button>)}</div></div><Candles values={values}/><div className="bookRows"><div className="askRow"><span>ASK</span><b>{active?usd(active.ask):"—"}</b><i>{active?n(active.ask_size,5):"—"}</i></div><div className="spreadRow">SPREAD {a?`${n(a.current_spread_bps,3)} BP`:"—"}</div><div className="bidRow"><span>BID</span><b>{active?usd(active.bid):"—"}</b><i>{active?n(active.bid_size,5):"—"}</i></div></div></article>

      <div className="rightStack">
        <article className="frame lifecycleBox"><div className="frameTitle">◆ MARKET LIFECYCLE · ENTRY → RESOLVE <span>{selectedMarkets.length} TRACKED</span></div><div className="lifeRows">{(selectedMarkets.length?selectedMarkets:markets).slice(0,6).map((m,i)=><div className="lifeRow" key={m.market_id}><span>{m.symbol}</span><div className="lifeTrack"><i style={{left:`${clamp(m.market_probability)*88}%`}}/><b style={{left:`${clamp(m.model_probability)*88}%`}}/></div><em className={m.net_edge>=0?"positive":"negative"}>{pct(m.net_edge,1)}</em></div>)}{markets.length===0&&<div className="waiting">WAITING FOR LIFECYCLE ANALYTICS</div>}</div></article>
        <article className="frame resolutionBox"><div className="frameTitle">◆ RESOLUTION GRID · LIVE EXPIRIES <span>{markets.length}</span></div><div className="resolutionGrid">{markets.slice(0,18).map(m=><div key={m.market_id} className={m.net_edge>=0?"resCell pos":"resCell neg"}><small>{m.symbol}</small><strong>{pct(m.model_probability,0)}</strong><span>{n(m.expiry_horizon_minutes,0)}m</span></div>)}{markets.length===0&&Array.from({length:12}).map((_,i)=><div key={i} className="resCell emptyCell">—</div>)}</div></article>
      </div>
    </section>

    <section className="frame torusPanel"><div className="frameTitle">◆ EXPIRY TORUS <span>EDGE × TIME × PROBABILITY</span></div><div className="torusBody"><div className="expiryLadder">{(selectedMarkets.length?selectedMarkets:markets).slice(0,8).map(m=><div key={m.market_id}><span>{n(m.expiry_horizon_minutes,0)}m</span><div><i style={{width:`${clamp(Math.abs(m.net_edge)*20)*100}%`}}/></div><b className={m.net_edge>=0?"positive":"negative"}>{pct(m.net_edge,2)}</b></div>)}</div><Torus lifecycle={selectedMarkets.length?selectedMarkets:markets} edge={edge}/><div className="torusStats"><span>MARKETS<b>{markets.length}</b></span><span>MODEL P<b>{selectedMarket?pct(selectedMarket.model_probability,1):"—"}</b></span><span>CONF<b>{selectedMarket?pct(selectedMarket.confidence,1):"—"}</b></span><span>VOL<b>{a?pct(a.realized_volatility,2):"—"}</b></span><span>IMBALANCE<b>{a?n(a.current_imbalance,3):"—"}</b></span></div></div><div className="edgeTimeline"><AreaLine values={edgeSeries} positive={edge>=0}/><div className="zeroAxis"/><div className="timelineLabel">NET EDGE / MICRO-RETURN FLOW</div></div></section>

    <section className="bottomMatrix"><article className="frame greeksBox"><div className="frameTitle">◆ GREEKS FIELD · THE GAMMA WALL <span>{selected}</span></div><GreeksField g={greeks} values={values}/><div className="greekStats"><span>Δ <b>{greeks?n(greeks.market_probability_delta,4):"—"}</b></span><span>ν <b>{greeks?n(greeks.volatility_vega,4):"—"}</b></span><span>κ <b>{greeks?n(greeks.imbalance_kappa,4):"—"}</b></span><span>θ <b>{greeks?n(greeks.time_theta,4):"—"}</b></span></div></article><article className="frame hawkesBox"><div className="frameTitle">◆ HAWKES CASCADE · SELF-EXCITING FLOW <span>{hawkes?`λ ${n(hawkes.current_intensity,3)}`:"WAIT"}</span></div><AreaLine values={hawkesSeries} positive={false}/><div className="cascadeLines">{[1,.72,.5,.34].map((k,i)=><div key={i}><i style={{width:`${clamp((hawkes?.current_intensity??0)*k,0.05,1)*100}%`}}/></div>)}</div><div className="hawkesStats"><span>BASELINE<b>{hawkes?n(hawkes.baseline_intensity,4):"—"}</b></span><span>EXCITATION<b>{hawkes?n(hawkes.excitation,4):"—"}</b></span><span>BRANCHING<b>{hawkes?n(hawkes.branching_ratio,4):"—"}</b></span><span>EVENT P<b>{hawkes?pct(hawkes.event_probability,1):"—"}</b></span></div></article></section>

    <footer className="terminalFooter"><span>MODEL {selectedMarket?pct(selectedMarket.model_probability,0):"—"} VS QUOTE {selectedMarket?pct(selectedMarket.market_probability,0):"—"}</span><span>OBS P&L {pnl?usd(pnl.observed_total_pnl):"—"} · FEES {pnl?usd(pnl.fees):"—"} · SLIPPAGE {pnl?usd(pnl.slippage):"—"}</span><span>SOURCE PUBLIC READ-ONLY · FINANCIAL CONNECTIVITY OFF</span></footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
