// "Controle": the mission-control view of the trading floor, the third view next to "Realista" and
// "Prédio". Loaded on demand in its own chunk (FloorControl-*.js). Every number comes from the API:
// GET /v1/floor/summary (orders and signals counted in the operational journal), the agent roster,
// the OMS ledger and the live floor state. A missing source shows "—" and says so, never a guess.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Shared } from "../../main";
import { dataOf, finiteDecimal, isRecord, rec, type XpLane, useDeskFeed } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import { type Agent, brl, type Ledger, limitsOf, parseLedger, parseRoster } from "../floorBuilding/data";
import { type Anchor, type ControlAgent, ControlEngine } from "./engine";

const POLL = 10000;
const LANE: Record<XpLane["state"], string> = { unknown: "UNKNOWN", "not-configured": "NOT CONFIGURED", unavailable: "UNAVAILABLE", live: "LIVE", halted: "HALTED" };

export type Summary = {
  total: number; today: number; refusedToday: number; signalsToday: number;
  byVenue: Record<string, number>; windowFull: boolean; day: string | null;
};

export function parseSummary(raw: unknown): Summary | null {
  if (!isRecord(raw)) return null;
  const n = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);
  const total = n(raw.orders_filled_total), today = n(raw.orders_filled_today);
  if (total === null || today === null) return null;
  const byVenue: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec(raw.orders_filled_by_venue))) { const c = n(v); if (c !== null) byVenue[k] = c; }
  return {
    total, today, refusedToday: n(raw.orders_refused_today) ?? 0, signalsToday: n(raw.signals_today) ?? 0,
    byVenue, windowFull: raw.window_full === true, day: typeof raw.day === "string" ? raw.day : null,
  };
}

const B3_OPEN = 10 * 60, B3_CLOSE = 17 * 60 + 55;
/** Whether the B3 regular session is open now (Brasilia, weekdays 10:00-17:55; no holiday calendar). */
function b3Open(now: Date): boolean {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "America/Sao_Paulo", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(now);
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? "";
  const minutes = Number(get("hour")) * 60 + Number(get("minute"));
  return !["Sat", "Sun"].includes(get("weekday")) && minutes >= B3_OPEN && minutes < B3_CLOSE;
}

const AGENT_ID = /\bAG-\d{4}-[A-Z]+\b/gi;
/** The latest action on this agent's symbol that is not about another agent (a "joined" line names one). */
export function lastFor(a: Agent, actions: FloorAction[]): FloorAction | null {
  for (let i = actions.length - 1; i >= 0; i--) {
    const act = actions[i];
    if (act.symbol !== a.symbol) continue;
    const named = act.headline.match(AGENT_ID);
    if (!named || named.some(id => id.toUpperCase() === a.id.toUpperCase())) return act;
  }
  return null;
}

/** One truthful status line per agent, from its book and the latest pipeline action on its symbol. */
export function statusLine(a: Agent, last: FloorAction | null, b3: boolean, xpLive: boolean): string {
  if (a.status === "ELIMINATED") return "eliminado · em descanso";
  if (a.floor === "xp" && !xpLive) return "nó XP indisponível";
  if (a.floor === "xp" && !b3) return "fora do pregão";
  const held = finiteDecimal(a.position);
  if (held !== null && !/^0*(\.0*)?$/.test(held)) return `posicionado · ${held} ${a.symbol.replace(/USDT$|F$/, "")}`;
  if (last) return last.headline.length > 44 ? `${last.headline.slice(0, 43)}…` : last.headline;
  return a.kind === "tradingview" ? "aguardando alerta…" : "aguardando cruzamento…";
}

function Kpis({ summary, ledger }: { summary: Summary | null; ledger: Ledger | null }) {
  const usdt = limitsOf(ledger?.accounts.find(a => a.quote === "USDT") ?? null);
  const brlAcc = limitsOf(ledger?.accounts.find(a => a.quote === "BRL") ?? null);
  const used = [usdt.daily.used, brlAcc.daily.used].filter((v): v is number => v !== null);
  const worst = used.length ? Math.max(...used) : null;
  const pct = worst === null ? null : Math.min(1, worst / 0.01);
  return (
    <section className="fc-kpis" aria-label="Totais do dia">
      <div>
        <span>ORDENS EXECUTADAS</span>
        <b>{summary ? `${summary.windowFull ? "≥ " : ""}${summary.total.toLocaleString("pt-BR")}` : "—"}</b>
        <small>{summary ? Object.entries(summary.byVenue).map(([v, c]) => `${v === "XP_B3" ? "XP/B3" : "Binance"} ${c}`).join(" · ") || "nenhuma ainda" : "SUMMARY UNAVAILABLE"}</small>
      </div>
      <i aria-hidden="true" />
      <div>
        <span>HOJE</span>
        <b>{summary ? summary.today : "—"} <em>ordens · {summary ? summary.signalsToday : "—"} sinais</em></b>
        <div className="fc-bar" role="meter" aria-label="Limite de perda diária usado" aria-valuemin={0} aria-valuemax={1} aria-valuenow={pct ?? undefined}>
          <span style={{ width: pct === null ? "0%" : `${(pct * 100).toFixed(1)}%` }} className={pct !== null && pct > 0.7 ? "hot" : ""} />
        </div>
        <small>{worst === null ? "perda diária: não medida" : `perda diária ${(worst * 100).toFixed(2)}% de 1%`}{summary?.refusedToday ? ` · ${summary.refusedToday} recusadas` : ""}</small>
      </div>
    </section>
  );
}

function Sources({ state, stateFresh, xp, summary }: { state: FloorState | null; stateFresh: boolean; xp: XpLane; summary: Summary | null }) {
  const wd = state?.watchdog;
  const rows: { name: string; status: string; ok: boolean | null }[] = [
    { name: "Binance Spot · testnet", status: state ? (stateFresh ? "LIVE" : "STALE") : "UNAVAILABLE", ok: state ? stateFresh : null },
    { name: "XP / B3 · demo", status: LANE[xp.state], ok: xp.state === "live" ? true : xp.state === "unknown" ? null : false },
    { name: "Sinais · TradingView + própria", status: state?.stations.tradingview ? `último ${state.stations.tradingview.at.slice(11, 16)} UTC` : "nenhum sinal", ok: null },
    { name: "Estratégia própria", status: summary ? `${summary.signalsToday} sinais hoje` : "—", ok: summary ? true : null },
    { name: "Watchdog", status: wd ? (wd.active ? (wd.last_trip ? `ATIVO · último trip ${wd.last_trip.at.slice(0, 10)}` : "ATIVO") : "INATIVO") : "UNAVAILABLE", ok: wd ? wd.active : null },
  ];
  return (
    <aside className="fc-sources" aria-label="Fontes">
      <header><span aria-hidden="true">⌖</span> FONTES</header>
      <ul>{rows.map(r => <li key={r.name}><i className={r.ok === null ? "" : r.ok ? "on" : "off"} />{r.name}<em>{r.status}</em></li>)}</ul>
    </aside>
  );
}

function Queue({ actions, agents }: { actions: FloorAction[]; agents: Agent[] }) {
  const recent = useMemo(() => [...actions].reverse().slice(0, 55), [actions]);
  const [i, setI] = useState(0);
  const [open, setOpen] = useState(true);
  useEffect(() => { const t = window.setInterval(() => setI(v => v + 1), 5000); return () => window.clearInterval(t); }, []);
  const n = recent.length;
  const at = n ? i % n : 0;
  const cur = recent[at];
  return (
    <section className={`fc-queue${open ? "" : " closed"}`} aria-label="Fila do pregão">
      <header>
        <b>{n} ações recentes no pregão</b>
        <button type="button" aria-label={open ? "Recolher fila" : "Abrir fila"} onClick={() => setOpen(o => !o)}>{open ? "×" : "+"}</button>
      </header>
      {open && (cur ? (
        <div className="fc-item">
          <span className={`fc-tone ${cur.tone}`}>{STATIONS.find(s => s.id === cur.station)?.label ?? cur.station}</span>
          <p>{cur.headline}{cur.symbol ? ` · ${cur.symbol}` : ""}</p>
          <time>{cur.at.slice(11, 19)} UTC</time>
          <nav><button type="button" aria-label="Anterior" onClick={() => setI(v => v - 1 + n)}>‹</button><span>{at + 1} / {n}</span><button type="button" aria-label="Próxima" onClick={() => setI(v => v + 1)}>›</button></nav>
        </div>
      ) : <p className="fc-note">Nenhuma ação no pregão ainda.</p>)}
      <footer>
        <span>PREGÃO</span>
        {agents.map(a => <span key={a.id} className={a.status === "ACTIVE" ? "" : "out"}><i />{a.name ?? a.id} <small>{a.symbol} · {a.kind === "tradingview" ? "alertas" : "própria"}</small></span>)}
      </footer>
    </section>
  );
}

export default function FloorControl({ market, state, stateFresh, actions, latest, active, xp }: {
  market: Shared["market"]; state: FloorState | null; stateFresh: boolean; actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean; xp: XpLane;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engine = useRef<ControlEngine | null>(null);
  const tags = useRef(new Map<string, HTMLElement>());
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const reduced = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const [paused, setPaused] = useState(reduced);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 30000); return () => window.clearInterval(t); }, []);

  const roster = dataOf(useDeskFeed("/v1/agents/roster", parseRoster, POLL).state);
  const ledger = dataOf(useDeskFeed("/v1/oms/ledger", parseLedger, POLL).state);
  const summaryFeed = useDeskFeed("/v1/floor/summary", parseSummary, POLL);
  const summary = dataOf(summaryFeed.state);
  const agents = roster?.agents ?? [];
  const b3 = b3Open(now);
  const busySymbols = useMemo(() => new Set(STATIONS.filter(s => active(s.id)).map(s => latest[s.id]?.symbol).filter((s): s is string => Boolean(s))), [active, latest]);
  const sceneAgents: ControlAgent[] = agents.map(a => ({ id: a.id, floor: a.floor, active: a.status === "ACTIVE", busy: busySymbols.has(a.symbol) }));
  const sceneKey = JSON.stringify(sceneAgents);

  useEffect(() => {
    const el = canvasRef.current, box = wrap.current;
    if (!el || !box) return;
    let instance: ControlEngine;
    try { instance = new ControlEngine(el, reduced); } catch { setFailed(true); return; }
    engine.current = instance;
    instance.onFirstFrame = () => setReady(true);
    instance.onAnchors = (anchors: Record<string, Anchor>) => {
      const width = box.clientWidth;
      for (const [id, node] of tags.current) {
        const a = anchors[id];
        if (!a || !a.visible) { node.style.visibility = "hidden"; continue; }
        // Keep the tag inside the room: a robot near the edge gets its tag nudged inwards.
        const half = node.offsetWidth / 2 + 6;
        const x = Math.min(Math.max(a.x, half), Math.max(half, width - half));
        node.style.visibility = "visible";
        node.style.transform = `translate(${x.toFixed(1)}px, ${a.y.toFixed(1)}px) translate(-50%, -100%)`;
      }
    };
    const lost = (e: Event) => { e.preventDefault(); setFailed(true); };
    el.addEventListener("webglcontextlost", lost);
    const observer = new ResizeObserver(([entry]) => instance.resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(box);
    let onScreen = true;
    const sync = () => instance.setRunning(onScreen && document.visibilityState === "visible");
    const seen = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; sync(); });
    seen.observe(box);
    document.addEventListener("visibilitychange", sync);
    let last: number | null = null;
    const down = (e: PointerEvent) => { last = e.clientX; el.setPointerCapture(e.pointerId); };
    const move = (e: PointerEvent) => { if (last !== null) { instance.orbit(e.clientX - last); last = e.clientX; } };
    const up = () => { last = null; };
    const wheel = (e: WheelEvent) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); instance.zoom(Math.exp(e.deltaY * 0.002)); } };
    el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      observer.disconnect(); seen.disconnect();
      document.removeEventListener("visibilitychange", sync);
      el.removeEventListener("webglcontextlost", lost);
      el.removeEventListener("pointerdown", down); el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
      el.removeEventListener("wheel", wheel);
      instance.dispose();
      engine.current = null;
    };
  }, [reduced]);
  useEffect(() => { engine.current?.setAgents(sceneAgents); }, [sceneKey, ready]);
  useEffect(() => { engine.current?.setAnimate(!paused); }, [paused, ready]);

  const mode = state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM · NO REAL MONEY";
  const date = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(now).toUpperCase();
  const ref = (key: string) => (node: HTMLElement | null) => { if (node) tags.current.set(key, node); else tags.current.delete(key); };
  const xpLive = xp.state === "live";

  return (
    <div ref={wrap} className="fl-scene fc-scene" role="group" aria-label={`Controle do pregão: ${agents.length} agentes, ${summary ? `${summary.total} ordens executadas` : "totais indisponíveis"}`}>
      <canvas ref={canvasRef} aria-hidden="true" />
      <header className="fc-head">
        <b>PROTO · CONTROLE</b>
        <ul aria-label="Data in this view">
          <li className={state == null ? "stale" : ""}>{mode}</li>
          <li className={summary ? "live" : "stale"}>{summary ? "SUMMARY LIVE" : summaryFeed.state.kind === "loading" ? "SUMMARY LOADING" : "SUMMARY UNAVAILABLE"}</li>
          <li className={roster ? "live" : "stale"}>{roster ? `ROSTER LIVE · ${roster.capitalLabel}` : "AGENT ROSTER UNAVAILABLE"}</li>
          <li className={market.connected ? "live" : "stale"}>{market.connected ? "MARKET LIVE PUBLIC" : "MARKET LAST KNOWN / REST"}</li>
          <li className={b3 ? "live" : "stale"}>B3 {b3 ? "PREGÃO ABERTO" : "FECHADA"}</li>
        </ul>
        <time>{date}</time>
        <button type="button" className="fc-pause" aria-pressed={paused} disabled={reduced} title={reduced ? "Movimento reduzido pelo sistema" : undefined} onClick={() => setPaused(p => !p)}>{paused ? "Retomar animação" : "Pausar animação"}</button>
      </header>
      <Kpis summary={summary} ledger={ledger} />
      <Sources state={state} stateFresh={stateFresh} xp={xp} summary={summary} />
      <div className="fc-tags" aria-hidden="true">
        {agents.map(a => (
          <div key={a.id} ref={ref(a.id)} className={`fc-tag${a.status === "ACTIVE" ? "" : " out"}`}>
            <b><i className={busySymbols.has(a.symbol) ? "busy" : ""} />{a.name ?? a.id}</b>
            <small>{statusLine(a, lastFor(a, actions), b3, xpLive)}</small>
            <em>{brl(a.equity)}</em>
          </div>
        ))}
      </div>
      <Queue actions={actions} agents={agents} />
      {!ready && !failed && <p className="fl-3d-loading" role="status">Carregando sala de controle…</p>}
      {failed && <p className="fl-3d-loading" role="alert">Controle 3D indisponível neste dispositivo (WebGL). Use a vista Realista.</p>}
    </div>
  );
}
