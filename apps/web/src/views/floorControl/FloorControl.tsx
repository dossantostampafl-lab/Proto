// "Controle": the mission-control view of the trading floor, the third view next to "Realista" and
// "Prédio". Loaded on demand in its own chunk (FloorControl-*.js). The room is the approved mockup
// picture (public/floorcontrol/room-photo.webp); every panel, agent tag and number on it is drawn
// live over the picture, at the exact place of its counterpart, so none of the picture's sample
// figures is ever seen. Every number comes from the API:
// GET /v1/floor/summary (orders and signals counted in the operational journal), the agent roster,
// the OMS ledger and the live floor state. A missing source shows "—" and says so, never a guess.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Shared } from "../../main";
import { dataOf, finiteDecimal, isRecord, rec, type XpLane, useDeskFeed } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import { type Agent, brl, type Ledger, limitsOf, parseLedger, parseRoster, sign } from "../floorBuilding/data";
import { AgentConversation } from "../AgentConversation";

const POLL = 10000;
const LANE: Record<XpLane["state"], string> = { unknown: "UNKNOWN", "not-configured": "NOT CONFIGURED", "off-hours": "OFF HOURS", unavailable: "UNAVAILABLE", live: "LIVE", halted: "HALTED" };

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


// ---------- geometry: everything is placed in the picture's own pixels (1672 × 941) ----------
const PHOTO = { src: "floorcontrol/room-photo.webp", w: 1672, h: 941 };
type Rect = [number, number, number, number]; // x0, y0, x1, y1 in picture pixels
// The panels sit on their counterparts by CSS (styles.css, "fc-photo"), in these same pixels.
// The agent detail opens as a drawer over the right-hand column (sources and activity).
const AT = { head: [0, 0, 1672, 56] as Rect, detail: [1262, 64, 1660, 806] as Rect };
// On phones the panels move below the picture, so these blocks cover the picture's sample panels
// (header, totals, sources, agents, activity, queue): none of its sample numbers ever shows.
const SAMPLES: Rect[] = [[0, 0, 1672, 60], [22, 70, 373, 246], [609, 70, 1101, 208], [1302, 70, 1648, 336],
  [19, 594, 416, 793], [1272, 575, 1653, 798], [17, 812, 1655, 921]];
// The picture's four agent stations, each tag drawn exactly over the picture's own, in the colour of
// the station's pedestal; further agents stand on the free floor of the ring in front of the globe.
const STATIONS_AT: { tag: Rect; hue: number }[] = [
  { tag: [515, 230, 733, 306], hue: 0 },     // upper left, yellow pedestal
  { tag: [1007, 230, 1215, 306], hue: 175 }, // upper right, blue pedestal
  { tag: [340, 485, 577, 559], hue: 85 },    // left, green pedestal
  { tag: [1110, 487, 1360, 562], hue: 255 }, // right, purple pedestal
  { tag: [560, 655, 760, 729], hue: 40 },
  { tag: [925, 655, 1125, 729], hue: 145 },
];
const SPRITE = `${import.meta.env.BASE_URL}floorcontrol/robot.webp`;

/** Where the picture lands inside the box with object-fit: cover. */
function coverRect(w: number, h: number) {
  const aspect = PHOTO.w / PHOTO.h;
  const iw = w / h > aspect ? w : h * aspect, ih = iw / aspect;
  return { x: (w - iw) / 2, y: (h - ih) / 2, w: iw, h: ih };
}

// ---------- small pieces ----------
const STATION_ICON: Record<StationId, string> = { tradingview: "📈", fusion: "🧭", reviewer: "🔎", risk: "🛡", oms: "📨", reconciliation: "✔" };
const TONE: Record<FloorAction["tone"], { label: string; cls: string }> = {
  working: { label: "Em andamento", cls: "work" }, ok: { label: "Sucesso", cls: "ok" },
  warn: { label: "Atenção", cls: "warn" }, alert: { label: "Erro", cls: "err" },
};
const AGENT_NAME = /\bAG-\d{4}-([A-Z]+)\b/i;

/** A journal line as people read it: "AG-0005-NOVA entrou no pregão" rather than the raw "joined". */
function describe(a: FloorAction): string {
  const joined = /\b(AG-\d{4}-[A-Z]+)\b.*\bjoined\b/i.exec(a.headline);
  return joined ? `${joined[1].toUpperCase()} entrou no pregão` : a.headline;
}
/** Who an action is about: the agent's name when the line names one, else the pipeline station. */
function actor(a: FloorAction): string {
  return AGENT_NAME.exec(a.headline)?.[1]?.toUpperCase() ?? STATIONS.find(s => s.id === a.station)?.label ?? a.station;
}

const PNL = ["down", "", "up"] as const;
const venue = (a: Agent) => (a.floor === "xp" ? "B3" : "BIN");
const stamp = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "—");
const held = (a: Agent) => { const q = finiteDecimal(a.position); return q === null || sign(q) === 0 ? "sem posição" : `${q} ${a.symbol}`; };

function Avatar({ hue, active }: { hue: number; active: boolean }) {
  return <i className="fc-avatar" aria-hidden="true"
    style={{ backgroundImage: `url(${SPRITE})`, filter: active ? `hue-rotate(${hue}deg)` : "grayscale(1) brightness(.7)" }} />;
}

// ---------- panels ----------
function Kpis({ summary, ledger }: { summary: Summary | null; ledger: Ledger | null }) {
  const usdt = limitsOf(ledger?.accounts.find(a => a.quote === "USDT") ?? null);
  const brlAcc = limitsOf(ledger?.accounts.find(a => a.quote === "BRL") ?? null);
  const used = [usdt.daily.used, brlAcc.daily.used].filter((v): v is number => v !== null);
  const worst = used.length ? Math.max(...used) : null;
  const signals = summary?.signalsToday ?? 0;
  const pending = summary ? Math.max(0, signals - summary.today - summary.refusedToday) : null;
  const ratio = summary && signals > 0 ? Math.min(1, summary.today / signals) : 0;
  return (
    <section className="fc-kpis" aria-label="Totais do dia">
      <div className="fc-total">
        <span>TOTAL DE ORDENS EXECUTADAS</span>
        <b>{summary ? `${summary.windowFull ? "≥ " : ""}${summary.total.toLocaleString("pt-BR")}` : "—"}</b>
        <svg className="fc-ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 12l3 3 7-7" /></svg>
        <small>{summary ? `+${summary.today} hoje` : "SUMMARY UNAVAILABLE"}</small>
      </div>
      <div className="fc-today">
        <span>PREGÃO HOJE</span>
        <b>{summary ? summary.today : "—"}<em> / {summary ? signals : "—"}</em></b>
        <div className="fc-bar" role="meter" aria-label="Ordens executadas sobre sinais de hoje" aria-valuemin={0} aria-valuemax={1} aria-valuenow={ratio}>
          <span style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
        </div>
        <small>{worst === null ? "perda diária: não medida" : `perda diária ${(worst * 100).toFixed(2)}% de 1%`}</small>
        <ul>
          <li className="ok"><i />{summary ? summary.today : "—"} <em>Executadas</em></li>
          <li className="work"><i />{summary ? summary.refusedToday : "—"} <em>Recusadas</em></li>
          <li className="idle"><i />{pending ?? "—"} <em>Sinais sem ordem</em></li>
        </ul>
      </div>
    </section>
  );
}

/** The Binance row is the execution venue: the executor's testnet when the data mode says so (healthy
 * only while its ledger reads), the in-process paper venue otherwise. A fresh floor state is not venue health. */
function binanceRow(state: FloorState | null, stateFresh: boolean, ledger: Ledger | null): { ok: boolean | null; status: string; detail: string } {
  if (!state) return { ok: null, status: "UNAVAILABLE", detail: "MODE UNAVAILABLE" };
  if (state.data_mode !== "TESTNET") return { ok: null, status: stateFresh ? "não usado" : "Stale", detail: "paper venue" };
  if (!ledger) return { ok: false, status: "UNAVAILABLE", detail: "testnet" };
  return { ok: !ledger.unavailable && stateFresh, status: ledger.unavailable ? "Executor indisponível" : stateFresh ? "Online" : "Stale", detail: "testnet" };
}

function Sources({ state, stateFresh, xp, summary, ledger, mobileOpen }: { state: FloorState | null; stateFresh: boolean; xp: XpLane; summary: Summary | null; ledger: Ledger | null; mobileOpen: boolean }) {
  const wd = state?.watchdog;
  const rows: { icon: string; tone: string; name: string; status: string; ok: boolean | null; detail: string }[] = [
    { icon: "B", tone: "bin", name: "Binance Spot", ...binanceRow(state, stateFresh, ledger) },
    { icon: "XP", tone: "xp", name: "XP / B3", ok: xp.state === "live" ? true : xp.state === "unknown" || xp.state === "off-hours" ? null : false, status: xp.state === "live" ? "Online" : LANE[xp.state], detail: "demo" },
    { icon: "TV", tone: "tv", name: "TradingView", ok: null, status: state?.stations.tradingview ? "último sinal" : "—", detail: state?.stations.tradingview ? `${state.stations.tradingview.at.slice(11, 16)} UTC` : "nenhum sinal" },
    { icon: "P", tone: "own", name: "Estratégia própria", ok: null, status: summary ? "sinais (TV + própria)" : "UNAVAILABLE", detail: summary ? `${summary.signalsToday} hoje` : "—" },
    { icon: "W", tone: "wd", name: "Watchdog", ok: wd ? wd.active : null, status: wd ? (wd.active ? "Ativo" : "Inativo") : "UNAVAILABLE", detail: wd?.last_trip ? `trip ${wd.last_trip.at.slice(5, 10)}` : "—" },
    { icon: "J", tone: "jr", name: "Journal", ok: state ? state.journal === "VALID" : null, status: state ? state.journal : "UNAVAILABLE", detail: state ? (state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM") : "MODE UNAVAILABLE" },
  ];
  return (
    <aside className={`fc-sources${mobileOpen ? " fc-mobile-open" : ""}`} aria-label="Fontes">
      <header>FONTES</header>
      <ul>{rows.map(r => <li key={r.name}>
        <i className={`fc-src ${r.tone}`} aria-hidden="true">{r.icon}</i>
        <b>{r.name}</b>
        <span className={r.ok === null ? "" : r.ok ? "on" : "off"}><i />{r.status}</span>
        <em>{r.detail}</em>
      </li>)}</ul>
    </aside>
  );
}

function AgentRoster({ agents, actions, b3, xpLive, available, selectedId, onSelect }: { agents: Agent[]; actions: FloorAction[]; b3: boolean; xpLive: boolean; available: boolean; selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <aside className="fc-roster" aria-label="Agentes do pregão">
      <header>AGENTES {available && agents.length > 4 && <small>+{agents.length - 4} na legenda</small>}</header>
      {!available ? <p className="fc-note">AGENT ROSTER UNAVAILABLE</p> : agents.length === 0 ? <p className="fc-note">Nenhum agente cadastrado.</p> :
        <ul>{agents.slice(0, 4).map((a, i) => <li key={a.id}>
          <button type="button" className="fc-roster-select" aria-pressed={selectedId === a.id} onClick={() => onSelect(a.id)}>
            <Avatar hue={STATIONS_AT[i % STATIONS_AT.length].hue} active={a.status === "ACTIVE"} />
            <b>{a.name ?? a.id}</b>
            <span className={a.status === "ACTIVE" ? "on" : "off"}><i />{a.status === "ACTIVE" ? "Online" : "Eliminado"}</span>
            <small>{statusLine(a, lastFor(a, actions), b3, xpLive)}</small>
          </button>
        </li>)}</ul>}
    </aside>
  );
}

/** Everything the roster and the journal say about one agent, plus its ChatGPT explanation panel. Read only. */
function AgentDetail({ agent: a, last, line, style, onClose }: { agent: Agent; last: FloorAction | null; line: string; style?: React.CSSProperties; onClose: () => void }) {
  return (
    <aside className="fc-agent-detail" aria-label={`Detalhes do agente ${a.name ?? a.id}`} style={style}>
      <header>
        <b>AGENTE · {a.name ?? a.id}</b>
        <button type="button" onClick={onClose} aria-label="Fechar detalhes do agente">×</button>
      </header>
      <p className={`fc-detail-state ${a.status === "ACTIVE" ? "on" : "off"}`}><i />{a.status === "ACTIVE" ? "Online" : "Eliminado"} · {line}</p>
      <div className="fc-detail-kpis">
        <div><small>P&amp;L</small><b className={PNL[sign(a.pnl) + 1]}>{brl(a.pnl, true)}</b></div>
        <div><small>Capital virtual</small><b>{brl(a.equity)}</b></div>
        <div><small>Posição</small><b>{held(a)}</b></div>
      </div>
      <dl>
        <dt>Identificador</dt><dd>{a.id}</dd>
        <dt>Ambiente</dt><dd>{a.floor === "xp" ? "XP / B3 (demo)" : "Binance Spot (testnet)"} · {a.symbol}</dd>
        <dt>Estratégia</dt><dd>{a.kindLabel}</dd>
        <dt>Geração</dt><dd>{a.generation ?? "—"}</dd>
        <dt>Sobrevivência</dt><dd>{a.survival ?? "—"}</dd>
        <dt>Realizado</dt><dd>{a.realized ?? "—"}</dd>
        <dt>Taxas</dt><dd>{a.fees ?? "—"}</dd>
        <dt>Custo da posição</dt><dd>{a.positionCost ?? "—"}</dd>
        <dt>Avaliação</dt><dd>{a.valued ? "marcada a mercado" : "sem marcação"}</dd>
        <dt>Última ação</dt><dd>{last ? `${last.at.slice(11, 16)} · ${STATIONS.find(s => s.id === last.station)?.label ?? last.station} · ${describe(last)}` : "nenhuma no journal"}</dd>
        <dt>Criado</dt><dd>{stamp(a.created)}</dd>
        {a.eliminated && <><dt>Eliminado</dt><dd>{stamp(a.eliminated)} · {a.reason ?? "—"}</dd></>}
      </dl>
      <AgentConversation key={a.id} agentId={a.id} />
    </aside>
  );
}

function Activity({ actions, fresh, mobileOpen }: { actions: FloorAction[]; fresh: boolean; mobileOpen: boolean }) {
  const recent = actions.slice(-5).reverse();
  return (
    <aside className={`fc-activity${mobileOpen ? " fc-mobile-open" : ""}`} aria-label="Atividade do pregão">
      <header>ATIVIDADE EM TEMPO REAL <small>{fresh ? "LIVE" : "LAST KNOWN"}</small></header>
      {recent.length === 0 ? <p className="fc-note">Nenhum evento registrado.</p> :
        <ol>{recent.map(a => <li key={`${a.seq}:${a.station}`}>
          <time>{a.at.slice(11, 16)}</time>
          <i className={`fc-act ${TONE[a.tone].cls}`} aria-hidden="true">{STATION_ICON[a.station]}</i>
          <b>{actor(a)}</b>
          <span title={a.headline}>{describe(a).replace(/\bAG-\d{4}-[A-Z]+\s*/i, "")}{a.symbol ? ` · ${a.symbol}` : ""}</span>
        </li>)}</ol>}
    </aside>
  );
}

function Queue({ actions }: { actions: FloorAction[] }) {
  const recent = useMemo(() => [...actions].reverse(), [actions]);
  const counts = (Object.keys(TONE) as FloorAction["tone"][]).map(t => ({ t, n: recent.filter(a => a.tone === t).length }));
  const showAll = () => document.querySelector(".floor-feed")?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <section className="fc-queue" aria-label="Fila do pregão">
      <div className="fc-fila">
        <header>FILA DO PREGÃO</header>
        <ul>{counts.map(({ t, n }) => <li key={t} className={TONE[t].cls}><i />{n}<em>{TONE[t].label}</em></li>)}</ul>
      </div>
      <div className="fc-events">
        <header>EVENTOS RECENTES</header>
        {recent.length === 0 ? <p className="fc-note">Nenhuma ação no pregão ainda.</p> :
          <ol>{recent.slice(0, 4).map(a => <li key={`${a.seq}:${a.station}`}>
            <i aria-hidden="true" />
            <time>{a.at.slice(11, 16)}</time>
            <b title={a.headline}>{describe(a)}</b>
            <small>{STATIONS.find(s => s.id === a.station)?.label ?? a.station}{a.symbol ? ` · ${a.symbol}` : ""}</small>
          </li>)}</ol>}
        <button type="button" className="fc-all" onClick={showAll}>Ver todos os eventos →</button>
      </div>
    </section>
  );
}

export default function FloorControl({ market, state, stateFresh, actions, latest, active, xp }: {
  market: Shared["market"]; state: FloorState | null; stateFresh: boolean; actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean; xp: XpLane;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
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
  const xpLive = xp.state === "live";
  const busySymbols = useMemo(() => new Set(STATIONS.filter(s => active(s.id)).map(s => latest[s.id]?.symbol).filter((s): s is string => Boolean(s))), [active, latest]);

  // A ripple on an agent's avatar when a real pipeline event arrives for its symbol (new ones only).
  const seen = useRef<number | null>(null);
  const [pulse, setPulse] = useState<Record<string, number>>({});
  useEffect(() => {
    const newest = actions.reduce((m, a) => Math.max(m, a.seq), -1);
    if (seen.current === null) { seen.current = newest; return; }
    const fresh = actions.filter(a => a.seq > (seen.current ?? -1) && a.symbol);
    seen.current = Math.max(seen.current, newest);
    if (fresh.length) setPulse(old => { const next = { ...old }; for (const a of fresh) next[a.symbol!] = a.seq; return next; });
  }, [actions]);

  // Size of the stage, and whether it is on screen with the tab visible (animation stops otherwise).
  useEffect(() => {
    const box = wrap.current, room = stage.current;
    if (!box || !room) return;
    const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(room);
    let onScreen = true;
    const sync = () => setRunning(onScreen && document.visibilityState === "visible");
    const seenBox = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; sync(); });
    seenBox.observe(box);
    document.addEventListener("visibilitychange", sync);
    return () => { observer.disconnect(); seenBox.disconnect(); document.removeEventListener("visibilitychange", sync); };
  }, []);

  const animate = !paused && running;
  // Phones: the header, then the picture at full width with the agents on it, then the panels.
  const phone = size.w > 0 && size.w <= 760;
  const rect = coverRect(Math.max(1, size.w), Math.max(1, size.h));
  const u = rect.w / PHOTO.w; // screen pixels per picture pixel
  const place = ([x0, y0, x1, y1]: Rect) => ({ left: rect.x + x0 * u, top: rect.y + y0 * u, width: (x1 - x0) * u, height: (y1 - y0) * u });
  const mode = state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM · NO REAL MONEY";
  const day = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "short", day: "2-digit", month: "short", year: "numeric" }).format(now);
  const time = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", minute: "2-digit" }).format(now);
  const online = state != null && stateFresh;
  const stations = STATIONS_AT.map((st, i) => {
        const a = agents[i];
        // The picture's own four stations always carry a tag, so its sample agents never show.
        if (!a && i >= 4) return null;
        const busy = a ? busySymbols.has(a.symbol) : false;
        return (
          <div key={i} className={`fc-bot${!a ? " free" : a.status === "ACTIVE" ? "" : " out"}`} style={place(st.tag)}>
            {a && pulse[a.symbol] !== undefined && <span key={pulse[a.symbol]} className="fc-ripple" aria-hidden="true" />}
            {a ? <Avatar hue={st.hue} active={a.status === "ACTIVE"} /> : <i className="fc-avatar empty" aria-hidden="true" />}
            {a ? (
              <button type="button" className="fc-tag" aria-pressed={selectedAgentId === a.id} aria-label={`Abrir agente ${a.name ?? a.id}`} onClick={() => setSelectedAgentId(a.id)}>
                <b><i className={busy ? "busy" : ""} />{a.name ?? a.id}</b>
                <small>{statusLine(a, lastFor(a, actions), b3, xpLive)}</small>
                <em title={`${venue(a)} ${a.symbol} · capital ${brl(a.equity)}`}><span className={PNL[sign(a.pnl) + 1]}>{brl(a.pnl, true)}</span> · {a.symbol}</em>
              </button>
            ) : (
              <span className="fc-tag"><b><i className="off" />ESTAÇÃO LIVRE</b><small>{roster ? "sem agente nesta estação" : "AGENT ROSTER UNAVAILABLE"}</small><em>—</em></span>
            )}
          </div>
        );
      });
  const focus = (cls: string) => () => wrap.current?.querySelector<HTMLElement>(cls)?.animate([{ boxShadow: "0 0 0 3px #38bdf8" }, { boxShadow: "0 0 0 0 transparent" }], 900);

  return (
    <div ref={wrap} className={`fl-scene fc-scene fc-photo${animate ? "" : " fc-still"}${phone ? " fc-phone" : ""}`} style={{ ["--u" as string]: u, ["--pu" as string]: phone ? 0.62 : u }}
      role="group" aria-label={`Controle do pregão: ${agents.length} agentes, ${summary ? `${summary.total} ordens executadas` : "totais indisponíveis"}`}>
      <div ref={stage} className="fc-stage">
      <img className="fc-room" src={`${import.meta.env.BASE_URL}${PHOTO.src}`} alt="" aria-hidden="true" decoding="async"
        style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
      {phone && SAMPLES.map((r, i) => <i key={i} className="fc-mask" aria-hidden="true" style={place(r)} />)}
      {stations}
      </div>
      <header className="fc-head" style={phone ? undefined : place(AT.head)}>
        <b className="fc-logo"><i aria-hidden="true" />PROTO</b>
        <nav aria-label="Painéis do controle">
          <button type="button" className="on">Control Room</button>
          <button type="button" onClick={focus(".fc-roster")}>Agentes</button>
          <button type="button" onClick={focus(".fc-sources")}>Fontes</button>
          <button type="button" onClick={focus(".fc-activity")}>Atividade</button>
          <button type="button" onClick={focus(".fc-queue")}>Eventos</button>
        </nav>
        <ul aria-label="Data in this view">
          <li className={state == null ? "stale" : ""}>{mode}</li>
          <li className={summary ? "live" : "stale"}>{summary ? "SUMMARY LIVE" : summaryFeed.state.kind === "loading" ? "SUMMARY LOADING" : "SUMMARY UNAVAILABLE"}</li>
          <li className={roster ? "live" : "stale"}>{roster ? `ROSTER LIVE · ${roster.capitalLabel}` : "AGENT ROSTER UNAVAILABLE"}</li>
          <li className={market.connected ? "live" : "stale"}>{market.connected ? "MARKET LIVE PUBLIC" : "MARKET LAST KNOWN / REST"}</li>
          <li className={b3 ? "live" : "stale"}>B3 {b3 ? "PREGÃO ABERTO" : "FECHADA"}</li>
        </ul>
        <span className={`fc-online${online ? "" : " off"}`}><i />{online ? "Sistema Online" : state == null ? "Sistema indisponível" : "Sistema com atraso"}</span>
        <time>{day.replace(/\./g, "")}</time>
        <strong>{time}</strong>
        <button type="button" className="fc-panel-mobile-toggle" aria-expanded={mobilePanel === "sources"} onClick={() => setMobilePanel(v => v === "sources" ? null : "sources")}>Fontes</button>
        <button type="button" className="fc-panel-mobile-toggle" aria-expanded={mobilePanel === "activity"} onClick={() => setMobilePanel(v => v === "activity" ? null : "activity")}>Atividade</button>
        <button type="button" className="fc-pause" aria-pressed={paused} disabled={reduced} title={reduced ? "Movimento reduzido pelo sistema" : undefined} onClick={() => setPaused(p => !p)}>{paused ? "Retomar animação" : "Pausar animação"}</button>
        <i className="fc-me" aria-hidden="true">PR</i>
      </header>

      <div className="fc-panels">
        <Kpis summary={summary} ledger={ledger} />
        <Sources state={state} stateFresh={stateFresh} xp={xp} summary={summary} ledger={ledger} mobileOpen={mobilePanel === "sources"} />
        <AgentRoster agents={agents} actions={actions} b3={b3} xpLive={xpLive} available={roster !== null} selectedId={selectedAgentId} onSelect={setSelectedAgentId} />
        <Activity actions={actions} fresh={stateFresh} mobileOpen={mobilePanel === "activity"} />
        <Queue actions={actions} />
      </div>
      {selectedAgent && <AgentDetail agent={selectedAgent} last={lastFor(selectedAgent, actions)} line={statusLine(selectedAgent, lastFor(selectedAgent, actions), b3, xpLive)}
        style={phone ? undefined : place(AT.detail)} onClose={() => setSelectedAgentId(null)} />}
      {!loaded && !failed && <p className="fl-3d-loading" role="status">Carregando sala de controle…</p>}
      {failed && <p className="fl-3d-loading" role="alert">Imagem da sala indisponível. Os dados continuam nos painéis e na vista Realista.</p>}
    </div>
  );
}
