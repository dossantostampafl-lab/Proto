// "Prédio": a stylized isometric building of the trading floor, the second view next to the
// realistic 3D room. Loaded on demand in its own chunk (FloorBuilding-*.js) only when selected.
// Desks, characters, tags and boards come from GET /v1/agents/roster, GET /v1/oms/ledger and the
// floor state; an unavailable source shows empty desks and says UNAVAILABLE, never a number.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Shared } from "../../main";
import { dataOf, type FeedState, type XpLane, useDeskFeed } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import type { Feed as BoardFeed, LimitBoard, ResultBoard } from "./boards";
import { type Agent, brl, brlOf, type FloorId, type Ledger, limitsOf, parseLedger, parseRoster, pnlSum, realizedSum, type Roster, shortTime, sign, toNum } from "./data";
import { type Anchor, BuildingEngine, FLOORS, type SceneAgent, type View } from "./engine";

const POLL = 10000;
const HISTORY = 240;
const LANE_LABEL: Record<XpLane["state"], string> = { unknown: "UNKNOWN", "not-configured": "NOT CONFIGURED", "off-hours": "OFF HOURS", unavailable: "UNAVAILABLE", live: "LIVE", halted: "HALTED" };
const feedOf = (s: FeedState<unknown>): BoardFeed => (s.kind === "live" ? "live" : s.kind === "stale" || s.kind === "paused" ? "stale" : s.kind === "unavailable" ? "unavailable" : "loading");
const FEED_LABEL: Record<BoardFeed, string> = { live: "LIVE", stale: "STALE", unavailable: "UNAVAILABLE", loading: "LOADING" };

type Point = { at: number; v: number };

/** Values read during this session only (never back-filled): per-floor P&L sum and per-agent equity. */
function useSessionHistory(roster: Roster | null, at: number | null) {
  const floors = useRef<Record<FloorId, Point[]>>({ binance: [], xp: [] });
  const agents = useRef(new Map<string, Point[]>());
  const last = useRef<number | null>(null);
  if (roster && at !== null && at !== last.current) {
    last.current = at;
    const push = (list: Point[], v: number | null) => { if (v !== null) { list.push({ at, v }); if (list.length > HISTORY) list.shift(); } };
    for (const id of FLOORS) {
      const active = roster.agents.filter(a => a.floor === id && a.status === "ACTIVE");
      if (active.length) push(floors.current[id], pnlSum(active));
    }
    for (const a of roster.agents) {
      if (!agents.current.has(a.id)) agents.current.set(a.id, []);
      push(agents.current.get(a.id)!, toNum(a.equity));
    }
  }
  return { floors: floors.current, agents: agents.current };
}

function Sparkline({ points }: { points: Point[] }) {
  if (points.length < 2) return <p className="fb-note">Curva: aguardando leituras reais (só pontos lidos desde que você abriu).</p>;
  const v = points.map(p => p.v), lo = Math.min(...v), hi = Math.max(...v), span = Math.max(hi - lo, 0.01);
  const d = v.map((n, i) => `${i ? "L" : "M"}${((i / (v.length - 1)) * 220).toFixed(1)},${(52 - ((n - lo) / span) * 48).toFixed(1)}`).join(" ");
  return (
    <figure className="fb-spark">
      <svg viewBox="0 0 220 56" role="img" aria-label={`Equity read ${points.length} times since this page was opened`}>
        <path d={d} fill="none" stroke={v[v.length - 1] >= v[0] ? "#4ade80" : "#f87171"} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption>{points.length} leituras · desde que você abriu</figcaption>
    </figure>
  );
}

function StationPanel({ agent, history, capital, feed, onClose }: { agent: Agent; history: Point[]; capital: string; feed: BoardFeed; onClose: () => void }) {
  const tone = sign(agent.pnl) > 0 ? "up" : sign(agent.pnl) < 0 ? "down" : "";
  return (
    <aside className="fb-panel" aria-label={`Agent ${agent.id}`}>
      <header>
        <div><b>{agent.id}</b><span>{agent.name ?? "—"} · {agent.kindLabel}</span></div>
        <button type="button" onClick={onClose} aria-label="Fechar painel do agente">×</button>
      </header>
      <p className={`fb-chip ${feed}`}>{capital} · ROSTER {FEED_LABEL[feed]}</p>
      <dl>
        <dt>Slot</dt><dd>{agent.symbol} · {agent.floor === "xp" ? "XP / B3" : "Binance Spot"}</dd>
        <dt>Status</dt><dd className={agent.status === "ACTIVE" ? "up" : "muted"}>{agent.status === "ACTIVE" ? "ACTIVE" : "ELIMINATED"}</dd>
        <dt>Equity</dt><dd>{brl(agent.equity)}{agent.valued ? "" : " · não avaliado"}</dd>
        <dt>P&amp;L</dt><dd className={tone}>{brl(agent.pnl, true)}</dd>
        <dt>Geração</dt><dd>{agent.generation ?? "—"}</dd>
        <dt>Sobrevivência ×</dt><dd>{agent.survival ?? "—"}</dd>
        <dt>Posição</dt><dd>{agent.position ?? "—"}{agent.position && agent.positionCost ? ` · custo ${agent.positionCost}` : ""}</dd>
        <dt>Criado</dt><dd>{shortTime(agent.created)}</dd>
        {agent.status === "ELIMINATED" && <><dt>Eliminado</dt><dd>{shortTime(agent.eliminated)}{agent.reason ? ` · ${agent.reason}` : ""}</dd></>}
      </dl>
      <Sparkline points={history} />
    </aside>
  );
}

function Results({ agents, onPick, onClose }: { agents: Agent[]; onPick: (id: string) => void; onClose: () => void }) {
  const sorted = [...agents].sort((a, b) => (toNum(b.pnl) ?? -Infinity) - (toNum(a.pnl) ?? -Infinity));
  return (
    <aside className="fb-panel fb-results" aria-label="Resultados dos agentes">
      <header><div><b>Resultados</b><span>P&amp;L acumulado por agente · capital virtual</span></div><button type="button" onClick={onClose} aria-label="Fechar resultados">×</button></header>
      {sorted.length ? (
        <ol>
          {sorted.map(a => (
            <li key={a.id} className={a.status === "ELIMINATED" ? "out" : ""}>
              <button type="button" onClick={() => onPick(a.id)}>
                <b>{a.id}</b><span>{a.symbol} · {a.kindLabel}{a.status === "ELIMINATED" ? " · eliminado" : ""}</span>
                <em className={sign(a.pnl) > 0 ? "up" : sign(a.pnl) < 0 ? "down" : ""}>{brl(a.pnl, true)}</em>
              </button>
            </li>
          ))}
        </ol>
      ) : <p className="fb-note">Roster indisponível: nenhum resultado para mostrar.</p>}
    </aside>
  );
}

export default function FloorBuilding({ market, state, stateFresh, latest, active, xp }: {
  market: Shared["market"]; state: FloorState | null; stateFresh: boolean; actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean; xp: XpLane;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engine = useRef<BuildingEngine | null>(null);
  const tags = useRef(new Map<string, HTMLElement>());
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<View>("building");
  const viewRef = useRef(view); viewRef.current = view;
  const [picked, setPicked] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const reduced = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const [paused, setPaused] = useState(reduced);

  const rosterFeed = useDeskFeed("/v1/agents/roster", parseRoster, POLL);
  const ledgerFeed = useDeskFeed("/v1/oms/ledger", parseLedger, POLL);
  const roster = dataOf(rosterFeed.state);
  const ledger: Ledger | null = dataOf(ledgerFeed.state);
  const rosterAt = rosterFeed.state.kind === "live" || rosterFeed.state.kind === "stale" ? rosterFeed.state.at : null;
  const history = useSessionHistory(roster, rosterAt);
  const rf = feedOf(rosterFeed.state), lf = feedOf(ledgerFeed.state);
  const mode = state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM · NO REAL MONEY";
  const venueWord = state?.data_mode === "PAPER" ? "paper" : "testnet";
  const capital = roster?.capitalLabel ?? "capital virtual";

  // Busy = a pipeline station worked on this agent's symbol within the last seconds of the live stream.
  const busySymbols = useMemo(() => new Set(STATIONS.filter(s => active(s.id)).map(s => latest[s.id]?.symbol).filter((s): s is string => Boolean(s))), [active, latest]);
  const sceneAgents: SceneAgent[] | null = roster ? roster.agents.map(a => ({ id: a.id, kind: a.kind, floor: a.floor, status: a.status, busy: busySymbols.has(a.symbol) })) : null;
  const sceneKey = JSON.stringify(sceneAgents);

  useEffect(() => {
    const el = canvasRef.current, box = wrap.current;
    if (!el || !box) return;
    let instance: BuildingEngine;
    try { instance = new BuildingEngine(el, reduced); } catch { setFailed(true); return; }
    engine.current = instance;
    instance.onFirstFrame = () => setReady(true);
    // Tags follow their anchors; a tag that would cover a nearer one is lifted above it, so every tag stays readable.
    const sizes = new WeakMap<HTMLElement, { w: number; h: number; text: string }>();
    instance.onAnchors = anchors => {
      const shown: { node: HTMLElement; a: Anchor }[] = [];
      for (const [key, node] of tags.current) {
        const a: Anchor | undefined = anchors[key];
        if (!a || !a.visible) { node.style.visibility = "hidden"; continue; }
        shown.push({ node, a });
      }
      shown.sort((p, q) => q.a.y - p.a.y);
      const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
      for (const { node, a } of shown) {
        let size = sizes.get(node);
        const text = node.textContent ?? "";
        if (!size || size.text !== text) { size = { w: node.offsetWidth, h: node.offsetHeight, text }; sizes.set(node, size); }
        const x0 = a.x - size.w / 2, x1 = a.x + size.w / 2;
        let y = a.y;
        for (let i = 0; i < 12; i++) {
          const hit = placed.find(r => x0 < r.x1 + 2 && x1 > r.x0 - 2 && y - size!.h < r.y1 + 2 && y > r.y0 - 2);
          if (!hit) break;
          y = hit.y0 - 3;
        }
        placed.push({ x0, x1, y0: y - size.h, y1: y });
        node.style.visibility = "visible";
        node.style.transform = `translate(${a.x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`;
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
    sync();

    // Drag rotates, pinch or ctrl+wheel zooms, plain wheel moves between floors, tap enters a floor or opens an agent.
    const pointers = new Map<number, { x: number; y: number }>();
    let down: { x: number; y: number; at: number; moved: boolean } | null = null;
    let pinch = 0, wheelAt = 0;
    const ndc = (e: PointerEvent) => { const r = el.getBoundingClientRect(); return [((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1] as const; };
    const onDown = (e: PointerEvent) => {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) down = { x: e.clientX, y: e.clientY, at: performance.now(), moved: false };
      else { down = null; const [p, q] = [...pointers.values()]; pinch = Math.hypot(p.x - q.x, p.y - q.y); }
      el.setPointerCapture(e.pointerId);
    };
    const onMove = (e: PointerEvent) => {
      const prev = pointers.get(e.pointerId);
      if (!prev) { if (e.pointerType === "mouse") { const [x, y] = ndc(e); el.style.cursor = instance.pick(x, y) ? "pointer" : "grab"; } return; }
      const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 2) {
        const [p, q] = [...pointers.values()], dist = Math.hypot(p.x - q.x, p.y - q.y);
        if (pinch > 0 && dist > 0) instance.zoom(pinch / dist);
        pinch = dist; return;
      }
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) down.moved = true;
      if (down?.moved) instance.orbit(dx, dy);
    };
    const onUp = (e: PointerEvent) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = 0;
      if (down && !down.moved && performance.now() - down.at < 600 && e.type === "pointerup") {
        const [x, y] = ndc(e);
        const hit = instance.pick(x, y);
        if (hit?.type === "floor") setView(hit.id);
        else if (hit?.type === "agent") { setPicked(hit.id); setShowResults(false); }
      }
      down = null;
    };
    const ORDER: View[] = ["binance", "xp", "building"];
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); instance.zoom(Math.exp(e.deltaY * 0.002)); return; }
      const i = ORDER.indexOf(viewRef.current), next = ORDER[i + (e.deltaY < 0 ? 1 : -1)];
      if (!next) return; // at the top or bottom the page scrolls as usual
      e.preventDefault();
      if (performance.now() - wheelAt < 500) return;
      wheelAt = performance.now();
      setView(next);
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      observer.disconnect(); seen.disconnect();
      document.removeEventListener("visibilitychange", sync);
      el.removeEventListener("webglcontextlost", lost);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      el.removeEventListener("wheel", onWheel);
      instance.dispose();
      engine.current = null;
    };
  }, [reduced]);

  useEffect(() => { engine.current?.setView(view); }, [view, ready]);
  useEffect(() => { engine.current?.setAnimate(!paused); }, [paused, ready]);
  useEffect(() => { engine.current?.setAgents(sceneAgents); }, [sceneKey, ready]);
  useEffect(() => { engine.current?.setFloorDim("xp", xp.state !== "live" && xp.state !== "halted"); }, [xp.state, ready]);

  // Wall boards per floor, from the roster (agents) and the ledger account of that floor's currency.
  const lockdown = state == null ? null : state.controls.lockdown ?? "nenhum";
  const halted = state == null ? null : state.controls.killed_venues?.join(", ") || "nenhum";
  const floorInfo = FLOORS.map(id => {
    const here = roster?.agents.filter(a => a.floor === id) ?? [];
    const live = here.filter(a => a.status === "ACTIVE");
    const quote = id === "xp" ? "BRL" : "USDT";
    const account = ledger?.accounts.find(a => a.quote === quote) ?? null;
    const lim = limitsOf(account);
    const pnl = roster ? (live.length ? pnlSum(live) : 0) : null;
    const eq = toNum(account?.equity ?? null), sod = toNum(account?.startOfDay ?? null);
    const result: ResultBoard = {
      feed: rf, floor: id === "xp" ? "XP / B3" : "Binance Spot", capital,
      pnl: live.length ? pnl : null, pnlText: roster ? (live.length ? brlOf(pnl, true) : "R$ —") : "—", agents: live.length,
      history: history.floors[id].map(p => p.v),
      accountDay: lim.measured && eq !== null && sod !== null ? `conta hoje: ${eq - sod >= 0 ? "+" : ""}${(eq - sod).toFixed(2)} ${quote}` : null,
    };
    const limits: LimitBoard = { feed: account ? lf : lf === "live" ? "unavailable" : lf, quote, measured: lim.measured, daily: lim.daily, drawdown: lim.drawdown, lockdown, halted };
    return { id, here, live, result, limits };
  });
  const boardKey = JSON.stringify(floorInfo.map(f => [f.result, f.limits]));
  useEffect(() => { floorInfo.forEach(f => engine.current?.setBoards(f.id, f.result, f.limits)); }, [boardKey, ready]);

  const inFloor = view === "building" ? null : floorInfo.find(f => f.id === view)!;
  const all = roster?.agents ?? [];
  const realized = roster ? realizedSum(inFloor ? inFloor.here : all) : null;
  const working = (inFloor ? inFloor.live : all.filter(a => a.status === "ACTIVE")).length;
  const total = (inFloor ? inFloor.here : all).length;
  const agent = picked ? all.find(a => a.id === picked) ?? null : null;
  const floorName = (id: FloorId) => (id === "xp" ? "02 · XP / B3 (demo)" : `01 · Binance Spot (${venueWord})`);
  const floorState = (id: FloorId) => (id === "xp" ? LANE_LABEL[xp.state] : state == null ? "UNAVAILABLE" : `${state.data_mode} · ${stateFresh ? "LIVE" : "STALE"}`);
  const ref = (key: string) => (node: HTMLElement | null) => { if (node) tags.current.set(key, node); else tags.current.delete(key); };
  const summary = floorInfo.map(f => `${floorName(f.id)}: ${f.live.length} agentes, ${floorState(f.id)}`).join("; ");

  return (
    <div ref={wrap} className={`fl-scene fb-scene${view === "building" ? " fb-building" : ""}`} role="group" aria-label={`Prédio do pregão, vista isométrica. ${summary}`}>
      <canvas ref={canvasRef} aria-hidden="true" />
      <div className="fb-top">
      <ul className="fb-badges" aria-label="Data in this view">
        <li className={state == null ? "stale" : ""}>{mode}</li>
        <li className={stateFresh ? "live" : "stale"}>{stateFresh ? "FLOOR STATE LIVE" : state == null ? "FLOOR STATE UNAVAILABLE" : "FLOOR STATE STALE · last known"}</li>
        <li className={rf === "live" ? "live" : "stale"}>{roster ? `ROSTER ${FEED_LABEL[rf]} · ${capital}` : rf === "loading" ? "ROSTER LOADING" : "AGENT ROSTER UNAVAILABLE"}</li>
        <li className={lf === "live" ? "live" : "stale"}>{ledger ? `LEDGER ${FEED_LABEL[lf]}${ledger.unavailable ? " · executor unavailable" : ""}` : lf === "loading" ? "LEDGER LOADING" : "LEDGER UNAVAILABLE"}</li>
        <li className={market.connected ? "live" : "stale"}>{market.connected ? "MARKET LIVE PUBLIC" : "MARKET LAST KNOWN / REST"}</li>
      </ul>

      <nav className="fb-tabs" aria-label="Andares">
        <button type="button" aria-pressed={view === "building"} onClick={() => setView("building")}>Prédio · todos os andares</button>
        {[...FLOORS].reverse().map(id => (
          <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)}>{floorName(id)}</button>
        ))}
      </nav>
      {!roster && ready && <p className="fb-empty" role="status">{rf === "loading" ? "Lendo o roster…" : "AGENT ROSTER UNAVAILABLE · mesas vazias, nenhum valor exibido"}</p>}
      </div>

      {inFloor && (
        <div className="fb-strip" aria-label="Resumo do andar">
          <span>FECHADOS · realizado acumulado <b className={realized === null ? "" : realized > 0 ? "up" : realized < 0 ? "down" : ""}>{roster ? brlOf(realized, true) : "—"}</b></span>
          <span>Em operação <b>{roster ? `${working} / ${total}` : "—"}</b> agentes</span>
          <span className="fb-strip-cap">{capital}</span>
        </div>
      )}

      <div className="fb-tags" aria-hidden="true">
        {FLOORS.map(id => {
          const f = floorInfo.find(x => x.id === id)!;
          return (
            <div key={`${id}:label`} ref={ref(`${id}:label`)} className={`fb-card${id === "xp" && xp.state !== "live" ? " dim" : ""}`}>
              <b>{floorName(id)}</b>
              <span>{roster ? `${f.live.length} agentes` : "agentes —"}</span>
              <em className={floorState(id).includes("LIVE") ? "live" : "stale"}>{floorState(id)}</em>
            </div>
          );
        })}
        {floorInfo.map(f => f.here.map(a => {
          const s = sign(a.pnl);
          return (
            <div key={`${f.id}:${a.id}`} ref={ref(`${f.id}:${a.id}`)} className={`fb-tag ${a.status === "ELIMINATED" ? "out" : s > 0 ? "up" : s < 0 ? "down" : ""}`}>
              <b>{a.id} · {brl(a.equity)}</b>
              <small>{a.status === "ELIMINATED" ? "eliminado" : capital}</small>
            </div>
          );
        }))}
        {floorInfo.map(f => (
          <div key={`${f.id}:lounge`} ref={ref(`${f.id}:lounge`)} className="fb-lounge">
            Em descanso / Eliminados · {roster ? f.here.filter(a => a.status === "ELIMINATED").length : "—"}
          </div>
        ))}
      </div>

      <div className="fb-actions">
        <button type="button" aria-pressed={showResults} onClick={() => { setShowResults(v => !v); setPicked(null); }}>Resultados</button>
        <button type="button" aria-pressed={paused} disabled={reduced} title={reduced ? "Movimento reduzido pelo sistema" : undefined} onClick={() => setPaused(p => !p)}>{paused ? "Retomar animação" : "Pausar animação"}</button>
      </div>

      {agent && <StationPanel agent={agent} history={history.agents.get(agent.id) ?? []} capital={capital} feed={rf} onClose={() => setPicked(null)} />}
      {showResults && !agent && <Results agents={all} onPick={id => { setPicked(id); setShowResults(false); const a = all.find(x => x.id === id); if (a) setView(a.floor); }} onClose={() => setShowResults(false)} />}

      <p className="fl-3d-help" aria-hidden="true">arraste para girar · pinça ou ctrl+roda para zoom · roda troca de andar · toque num andar ou agente</p>
      {!ready && !failed && <p className="fl-3d-loading" role="status">Carregando prédio…</p>}
      {failed && <p className="fl-3d-loading" role="alert">Prédio 3D indisponível neste dispositivo (WebGL). Use a vista Realista.</p>}
    </div>
  );
}
