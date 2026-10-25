// "Controle": the mission-control view of the trading floor, the third view next to "Realista" and
// "Prédio". Loaded on demand in its own chunk (FloorControl-*.js). The room is a pre-rendered image
// (tools/render_control_room/render.py) with no people, text or numbers in it; robots, paths, tags
// and every panel are drawn live over it at the anchors the renderer exported. Every number comes from the API:
// GET /v1/floor/summary (orders and signals counted in the operational journal), the agent roster,
// the OMS ledger and the live floor state. A missing source shows "—" and says so, never a guess.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Shared } from "../../main";
import { dataOf, finiteDecimal, isRecord, rec, type XpLane, useDeskFeed } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import { type Agent, brl, type Ledger, limitsOf, parseLedger, parseRoster } from "../floorBuilding/data";
import ANCHORS from "./anchors.json";

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
  if (last) {
    if (/\bjoined\b/i.test(last.headline)) return "entrou no pregão";
    return last.headline.length > 30 ? `${last.headline.slice(0, 29)}…` : last.headline;
  }
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
        <span>TOTAL DE ORDENS EXECUTADAS</span>
        <b>{summary ? `${summary.windowFull ? "≥ " : ""}${summary.total.toLocaleString("pt-BR")}` : "—"}</b>
        <small>{summary ? Object.entries(summary.byVenue).map(([v, c]) => `${v === "XP_B3" ? "XP/B3" : v.startsWith("BINANCE") ? "Binance" : v} ${c}`).join(" · ") || "nenhuma ainda" : "SUMMARY UNAVAILABLE"}</small>
      </div>
      <i aria-hidden="true" />
      <div>
        <span>PREGÃO HOJE</span>
        <b>{summary ? summary.today : "—"} <em>ordens · {summary ? summary.signalsToday : "—"} sinais</em></b>
        <div className="fc-bar" role="meter" aria-label="Limite de perda diária usado" aria-valuemin={0} aria-valuemax={1} aria-valuenow={pct ?? undefined}>
          <span style={{ width: pct === null ? "0%" : `${(pct * 100).toFixed(1)}%` }} className={pct !== null && pct > 0.7 ? "hot" : ""} />
        </div>
        <small>{worst === null ? "perda diária: não medida" : `perda diária ${(worst * 100).toFixed(2)}% de 1%`}{summary?.refusedToday ? ` · ${summary.refusedToday} recusadas` : ""}</small>
      </div>
    </section>
  );
}

function Sources({ state, stateFresh, xp, summary, mobileOpen }: { state: FloorState | null; stateFresh: boolean; xp: XpLane; summary: Summary | null; mobileOpen: boolean }) {
  const wd = state?.watchdog;
  const rows: { name: string; status: string; ok: boolean | null }[] = [
    { name: "Binance Spot · testnet", status: state ? (stateFresh ? "LIVE" : "STALE") : "UNAVAILABLE", ok: state ? stateFresh : null },
    { name: "XP / B3 · demo", status: LANE[xp.state], ok: xp.state === "live" ? true : xp.state === "unknown" ? null : false },
    { name: "Sinais · TradingView + própria", status: state?.stations.tradingview ? `último ${state.stations.tradingview.at.slice(11, 16)} UTC` : "nenhum sinal", ok: null },
    { name: "Estratégia própria", status: summary ? `${summary.signalsToday} sinais hoje` : "—", ok: summary ? true : null },
    { name: "Watchdog", status: wd ? (wd.active ? (wd.last_trip ? `ATIVO · último trip ${wd.last_trip.at.slice(0, 10)}` : "ATIVO") : "INATIVO") : "UNAVAILABLE", ok: wd ? wd.active : null },
  ];
  return (
    <aside className={`fc-sources${mobileOpen ? " fc-mobile-open" : ""}`} aria-label="Fontes">
      <header><span aria-hidden="true">⌖</span> FONTES</header>
      <ul>{rows.map(r => <li key={r.name}><i className={r.ok === null ? "" : r.ok ? "on" : "off"} />{r.name}<em>{r.status}</em></li>)}</ul>
    </aside>
  );
}


function AgentRoster({ agents, actions, b3, xpLive, available, selectedId, onSelect }: { agents: Agent[]; actions: FloorAction[]; b3: boolean; xpLive: boolean; available: boolean; selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <aside className="fc-roster" aria-label="Agentes do pregão">
      <header>AGENTES <strong>{available ? agents.length : "—"}</strong></header>
      {!available ? <p className="fc-note">AGENT ROSTER UNAVAILABLE</p> : agents.length === 0 ? <p className="fc-note">Nenhum agente cadastrado.</p> :
        <ul>{agents.slice(0, 6).map(a => <li key={a.id}>
          <i className={a.status === "ACTIVE" ? "on" : ""} aria-hidden="true" />
          <button type="button" className="fc-roster-select" aria-pressed={selectedId === a.id} onClick={() => onSelect(a.id)}>
            <b>{a.name ?? a.id}</b>
            <small>{statusLine(a, lastFor(a, actions), b3, xpLive)}</small>
          </button>
        </li>)}</ul>}
    </aside>
  );
}


function Activity({ actions, fresh, mobileOpen }: { actions: FloorAction[]; fresh: boolean; mobileOpen: boolean }) {
  const recent = actions.slice(-5).reverse();
  return (
    <aside className={`fc-activity${mobileOpen ? " fc-mobile-open" : ""}`} aria-label="Atividade do pregão">
      <header>ATIVIDADE EM TEMPO REAL <small>{fresh ? "LIVE" : "LAST KNOWN"}</small></header>
      {recent.length === 0 ? <p className="fc-note">Nenhum evento registrado.</p> :
        <ol>{recent.map(a => <li key={a.seq}>
          <time>{a.at.slice(11, 16)}</time>
          <b>{STATIONS.find(s => s.id === a.station)?.label ?? a.station}</b>
          <span title={a.headline}>{a.headline}{a.symbol ? " · " + a.symbol : ""}</span>
        </li>)}</ol>}
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

type Pt = [number, number];
type Layout = { seats: { floor: "binance" | "xp"; seat: Pt; hub: Pt; height: number }[]; globe: { center: Pt; radius: number } };
const RENDERED = ANCHORS as Partial<Record<"landscape" | "portrait", Layout>>;
const LANDSCAPE = { src: "floorcontrol/room.webp", aspect: 16 / 9, layout: RENDERED.landscape as Layout };
const ROOMS = {
  landscape: LANDSCAPE,
  // Phones get their own render; until it exists they crop the landscape one.
  portrait: RENDERED.portrait ? { src: "floorcontrol/room-portrait.webp", aspect: 9 / 16, layout: RENDERED.portrait } : LANDSCAPE,
};
const SPRITE = `${import.meta.env.BASE_URL}floorcontrol/robot.webp`;
// The robot sprite frame is 2.6 m tall (orthographic scale); a robot stands 1.9 m in the anchors.
// Robots are drawn twice their scale so they read at a glance, as in a control-room overview.
const SPRITE_PER_HEIGHT = (2.6 / 1.9) * 2;
const BOT_HUES = [0, 175, 85, 255, 40, 145];

/** Where the image lands inside the box with object-fit: cover. */
function coverRect(w: number, h: number, aspect: number) {
  const iw = w / h > aspect ? w : h * aspect, ih = iw / aspect;
  return { x: (w - iw) / 2, y: (h - ih) / 2, w: iw, h: ih };
}

/**
 * Which seat each agent takes, in roster order: of the seats of its floor that are on screen, the one
 * nearest the hub that keeps its name tag clear of the tags already placed (else the one farthest from them).
 */
function seatsFor(agents: Agent[], layout: Layout, place: (p: Pt) => Pt, w: number, h: number): Map<string, { i: number; tag: boolean }> {
  const out = new Map<string, { i: number; tag: boolean }>();
  const placed: Pt[] = [];
  const [gx, gy] = place(layout.globe.center);
  // On screen with room for the tag above, clear of the totals (phones) and of the sources panel (wide screens, bottom right).
  const wide = w > 760;
  const onScreen = ([x, y]: Pt) => x > w * 0.06 && x < w * 0.94 && y > h * (wide ? 0.26 : 0.35) && y < h * 0.82
    && !(wide && x > w - 250 && y > h * 0.42);
  // Phones keep the upper station tiers visible below the compact top KPI cards;
  // the older 52% cutoff concealed every real desk and projected robots onto the Earth.
  // Phones show the name only, so their tags need less room.
  const gapX = wide ? 170 : 92, gapY = wide ? 80 : 36;
  const clear = ([x, y]: Pt) => placed.every(([px, py]) => Math.abs(px - x) > gapX || Math.abs(py - y) > gapY);
  for (const a of agents) {
    const free = layout.seats
      .map((s, i) => ({ i, at: place(s.seat), floor: s.floor }))
      .filter(s => s.floor === a.floor && ![...out.values()].some(o => o.i === s.i) && onScreen(s.at))
      .sort((p, q) => Math.hypot(p.at[0] - gx, p.at[1] - gy) - Math.hypot(q.at[0] - gx, q.at[1] - gy));
    // No clear seat left: the one farthest from the tags already placed.
    const spread = (p: Pt) => Math.min(...placed.map(([px, py]) => Math.max(Math.abs(px - p[0]) / gapX, Math.abs(py - p[1]) / gapY)));
    const pick = free.find(s => clear(s.at)) ?? [...free].sort((p, q) => spread(q.at) - spread(p.at))[0];
    if (!pick) continue; // no seat of this floor on screen: the legend still lists the agent
    // A crowded seat keeps its robot; on a phone its tag gives way (the queue legend lists everyone).
    const tag = wide || clear(pick.at);
    out.set(a.id, { i: pick.i, tag }); if (tag) placed.push(pick.at);
  }
  return out;
}

export default function FloorControl({ market, state, stateFresh, actions, latest, active, xp }: {
  market: Shared["market"]; state: FloorState | null; stateFresh: boolean; actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean; xp: XpLane;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const reduced = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const [paused, setPaused] = useState(reduced);
  const [running, setRunning] = useState(true);
  const [now, setNow] = useState(() => new Date());
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"sources" | "activity" | null>(null);
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 30000); return () => window.clearInterval(t); }, []);

  const roster = dataOf(useDeskFeed("/v1/agents/roster", parseRoster, POLL).state);
  const ledger = dataOf(useDeskFeed("/v1/oms/ledger", parseLedger, POLL).state);
  const summaryFeed = useDeskFeed("/v1/floor/summary", parseSummary, POLL);
  const summary = dataOf(summaryFeed.state);
  const agents = useMemo(() => roster?.agents ?? [], [roster]);
  const selectedAgent = agents.find(agent => agent.id === selectedAgentId) ?? null;
  const b3 = b3Open(now);
  const busySymbols = useMemo(() => new Set(STATIONS.filter(s => active(s.id)).map(s => latest[s.id]?.symbol).filter((s): s is string => Boolean(s))), [active, latest]);

  // Size of the stage, and whether it is on screen with the tab visible (animation stops otherwise).
  useEffect(() => {
    const box = wrap.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(box);
    let onScreen = true;
    const sync = () => setRunning(onScreen && document.visibilityState === "visible");
    const seen = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; sync(); });
    seen.observe(box);
    document.addEventListener("visibilitychange", sync);
    return () => { observer.disconnect(); seen.disconnect(); document.removeEventListener("visibilitychange", sync); };
  }, []);

  const room = size.w > 0 && size.w / Math.max(1, size.h) < 0.9 ? ROOMS.portrait : ROOMS.landscape;
  const rect = coverRect(Math.max(1, size.w), Math.max(1, size.h), room.aspect);
  const at = ([x, y]: Pt): Pt => [rect.x + x * rect.w, rect.y + y * rect.h];
  const seatOf = useMemo(() => seatsFor(agents, room.layout, at, size.w, size.h),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agents, room, size.w, size.h]);
  const animate = !paused && running;

  // Walk progress per agent: 0 at its desk, 1 at the hub console. Busy agents walk out and back.
  const [walk, setWalk] = useState<Record<string, number>>({});
  const [frame, setFrame] = useState(0);
  const busyKey = agents.map(a => `${a.id}:${a.status === "ACTIVE" && busySymbols.has(a.symbol) ? 1 : 0}`).join(",");
  useEffect(() => {
    const goal: Record<string, number> = {};
    for (const a of agents) goal[a.id] = a.status === "ACTIVE" && busySymbols.has(a.symbol) ? 1 : 0;
    if (!animate) { setWalk(goal); return; }
    let raf = 0, last = performance.now(), tick = 0;
    const step = (t: number) => {
      const dt = Math.min(0.1, (t - last) / 1000); last = t;
      tick += dt;
      setWalk(old => {
        const next: Record<string, number> = {};
        for (const id of Object.keys(goal)) {
          const cur = old[id] ?? 0, g = goal[id];
          next[id] = g > cur ? Math.min(g, cur + dt * 0.22) : Math.max(g, cur - dt * 0.22);
        }
        return next;
      });
      setFrame(Math.floor(tick * 7) % 4);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busyKey, animate]);

  const mode = state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM · NO REAL MONEY";
  const date = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(now).toUpperCase();
  const xpLive = xp.state === "live";
  const [gx, gy] = at(room.layout.globe.center);
  const gr = room.layout.globe.radius * rect.h;
  // Decorative glow must remain readable on narrower, wider-framed portrait renders.
  const glowSize = size.w <= 760 ? Math.max(140, gr * 2.6) : gr * 2.6;

  return (
    <div ref={wrap} className={`fl-scene fc-scene${animate ? "" : " fc-still"}`} role="group" aria-label={`Controle do pregão: ${agents.length} agentes, ${summary ? `${summary.total} ordens executadas` : "totais indisponíveis"}`}>
      <img className="fc-room" src={`${import.meta.env.BASE_URL}${room.src}`} alt="" aria-hidden="true" decoding="async"
        style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
      <i className="fc-globe" aria-hidden="true" style={{ left: gx, top: gy, width: glowSize, height: glowSize }} />
      <svg className="fc-paths" aria-hidden="true" width={size.w} height={size.h}>
        {agents.map(a => {
          const i = seatOf.get(a.id)?.i;
          if (i === undefined || !(a.status === "ACTIVE" && busySymbols.has(a.symbol))) return null;
          const s = room.layout.seats[i], [x1, y1] = at(s.seat), [x2, y2] = at(s.hub);
          return <g key={a.id}><line x1={x1} y1={y1} x2={x2} y2={y2} /><circle cx={x2} cy={y2} r={Math.max(6, s.height * rect.h * 0.22)} /></g>;
        })}
      </svg>
      {agents.map((a, index) => {
        const seat = seatOf.get(a.id);
        // Portrait camera focuses on the Earth and can crop every physical desk.
        // Keep agent badges discoverable on a projected hub ring rather than
        // inventing an on-screen seat or dropping all agent labels.
        const onHub = seat === undefined && size.w <= 760;
        if (seat === undefined && !onHub) return null;
        const s = seat === undefined ? null : room.layout.seats[seat.i];
        const t = s ? (walk[a.id] ?? 0) : 0;
        const [x1, y1] = s ? at(s.seat) : [0, 0];
        const [x2, y2] = s ? at(s.hub) : [0, 0];
        const a1 = (index / Math.max(agents.length, 4)) * Math.PI * 2 - Math.PI * .7;
        const markerX = gx + Math.cos(a1) * Math.min(size.w * .34, gr * 1.34);
        const markerY = gy + Math.sin(a1) * gr * .85;
        const x = s ? x1 + (x2 - x1) * t : markerX;
        const y = s ? y1 + (y2 - y1) * t : markerY;
        const h = s ? Math.min(84, Math.max(20, s.height * rect.h * SPRITE_PER_HEIGHT)) : 34;
        const moving = s !== null && t > 0 && t < 1;
        const busy = busySymbols.has(a.symbol);
        return (
          <div key={a.id} className={`fc-bot${a.status === "ACTIVE" ? "" : " out"}`} style={{ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)` }}>
            <i style={{ width: h, height: h, backgroundImage: `url(${SPRITE})`, backgroundPosition: `${((moving && animate ? frame : 0) * 100) / 3}% 0`, filter: a.status === "ACTIVE" ? `hue-rotate(${BOT_HUES[index % BOT_HUES.length]}deg) drop-shadow(0 4px 6px rgba(0,0,0,.55))` : "grayscale(1) brightness(.7)" }} />
            {(onHub || seat?.tag) && <button type="button" className="fc-tag" style={{ bottom: h * 0.82 }} aria-pressed={selectedAgentId === a.id} aria-label={`Abrir agente ${a.name ?? a.id}`} onClick={() => setSelectedAgentId(a.id)}>
              <b><i className={busy ? "busy" : ""} />{a.name ?? a.id}</b>
              <small>{statusLine(a, lastFor(a, actions), b3, xpLive)}</small>
              <em>{brl(a.equity)} · capital virtual</em>
            </button>}
          </div>
        );
      })}
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
        <button type="button" className="fc-panel-mobile-toggle" aria-expanded={mobilePanel === "sources"} onClick={() => setMobilePanel(v => v === "sources" ? null : "sources")}>Fontes</button>
        <button type="button" className="fc-panel-mobile-toggle" aria-expanded={mobilePanel === "activity"} onClick={() => setMobilePanel(v => v === "activity" ? null : "activity")}>Atividade</button>
        <button type="button" className="fc-pause" aria-pressed={paused} disabled={reduced} title={reduced ? "Movimento reduzido pelo sistema" : undefined} onClick={() => setPaused(p => !p)}>{paused ? "Retomar animação" : "Pausar animação"}</button>
      </header>
      <Kpis summary={summary} ledger={ledger} />
      <Sources state={state} stateFresh={stateFresh} xp={xp} summary={summary} mobileOpen={mobilePanel === "sources"} />
      <AgentRoster agents={agents} actions={actions} b3={b3} xpLive={xpLive} available={roster !== null} selectedId={selectedAgentId} onSelect={setSelectedAgentId} />
      {selectedAgent && (
        <aside className="fc-agent-detail" aria-label={`Detalhes do agente ${selectedAgent.name ?? selectedAgent.id}`}>
          <header>
            <b>AGENTE · {selectedAgent.name ?? selectedAgent.id}</b>
            <button type="button" onClick={() => setSelectedAgentId(null)} aria-label="Fechar detalhes do agente">×</button>
          </header>
          <dl>
            <dt>Identificador</dt><dd>{selectedAgent.id}</dd>
            <dt>Instrumento</dt><dd>{selectedAgent.symbol}</dd>
            <dt>Ambiente</dt><dd>{selectedAgent.floor === "xp" ? "XP / B3" : "Binance"}</dd>
            <dt>Situação</dt><dd>{statusLine(selectedAgent, lastFor(selectedAgent, actions), b3, xpLive)}</dd>
            <dt>Capital virtual</dt><dd>{brl(selectedAgent.equity)}</dd>
          </dl>
          <p>{lastFor(selectedAgent, actions)?.headline ?? "Sem atividade registrada para este instrumento."}</p>
        </aside>
      )}
      <Activity actions={actions} fresh={stateFresh} mobileOpen={mobilePanel === "activity"} />
      <Queue actions={actions} agents={agents} />
      {!loaded && !failed && <p className="fl-3d-loading" role="status">Carregando sala de controle…</p>}
      {failed && <p className="fl-3d-loading" role="alert">Imagem da sala indisponível. Os dados continuam abaixo e na vista Realista.</p>}
    </div>
  );
}
