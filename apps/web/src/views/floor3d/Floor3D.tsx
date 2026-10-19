// Live 3D trading floor. Loaded on demand by the Floor view (its own chunk with three.js). Its
// people and PBR textures are fetched from /floor3d/ by a second on-demand chunk while a progress
// bar shows; nothing else stands in. Reports a lost GPU context so the view can fall back to the
// photo room; a failed asset download is said in place, with a retry.
import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import type { Shared } from "../../main";
import type { XpLaneState } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import { drawerOf, FloorEngine } from "./engine";
import { parseRoster, type Roster } from "./roster";

const ROSTER_MS = 10000;

/** GET /v1/agents/roster while the floor is visible; null while unavailable (then no tags are shown). */
function useRoster(actions: FloorAction[]) {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [nonce, setNonce] = useState(0);
  const seen = useRef<number | null>(null);
  // A fresh spawn or elimination on the floor stream re-reads the roster at once.
  useEffect(() => {
    const last = [...actions].reverse().find(a => a.event_type === "AGENT_ELIMINATED" || a.event_type === "AGENT_SPAWNED");
    if (!last) return;
    if (seen.current !== null && last.seq !== seen.current) setNonce(n => n + 1);
    seen.current = last.seq;
  }, [actions]);
  useEffect(() => {
    let live = true;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      const r = await get<unknown>("/v1/agents/roster");
      if (live) setRoster(r.ok ? parseRoster(r.data) : null);
    };
    void load();
    const id = window.setInterval(() => void load(), ROSTER_MS);
    return () => { live = false; window.clearInterval(id); };
  }, [nonce]);
  return roster;
}

export default function Floor3D({ market, state, stateFresh, actions, latest, active, xp, onSelect, selected, onFail }: {
  market: Shared["market"];
  state: FloorState | null;
  stateFresh?: boolean;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  active: (s: StationId) => boolean;
  xp?: XpLaneState | null;
  onSelect?: (id: StationId) => void;
  selected?: StationId | null;
  onFail: () => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<FloorEngine | null>(null);
  const fail = useRef(onFail);
  fail.current = onFail;
  const select = useRef(onSelect);
  select.current = onSelect;
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const roster = useRoster(actions);
  const rosterRef = useRef(roster);
  rosterRef.current = roster;

  useEffect(() => {
    const el = canvas.current, box = wrap.current;
    if (!el || !box) return;
    let cancelled = false;
    let instance: FloorEngine | null = null;
    const cleanups: (() => void)[] = [];
    setReady(false);
    setFailed(false);
    setProgress(0);

    const start = async () => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const coarse = window.matchMedia("(pointer: coarse)").matches;
      let assets;
      try {
        const { loadFloorAssets } = await import("./Floor3DAssets");
        assets = await loadFloorAssets(f => { if (!cancelled) setProgress(f); }, coarse ? 2 : 4);
        if (cancelled) return;
        instance = new FloorEngine(el, assets, reduced, coarse);
      } catch {
        if (!cancelled) setFailed(true);
        return;
      }
      if (cancelled) { instance.dispose(); return; }
      const inst = instance;
      engine.current = inst;
      inst.onFirstFrame = () => setReady(true);
      inst.setRoster(rosterRef.current);
      const lost = (e: Event) => { e.preventDefault(); fail.current(); };
      el.addEventListener("webglcontextlost", lost);
      const observer = new ResizeObserver(([entry]) => inst.resize(entry.contentRect.width, entry.contentRect.height));
      observer.observe(box);

      // Render only while the floor is on screen and the tab is visible.
      let onScreen = true;
      const sync = () => inst.setRunning(onScreen && document.visibilityState === "visible");
      const seen = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; sync(); });
      seen.observe(box);
      document.addEventListener("visibilitychange", sync);

      // Drag to orbit, pinch or ctrl+wheel to zoom, tap a desk to open its drawer.
      const pointers = new Map<number, { x: number; y: number }>();
      let down: { x: number; y: number; at: number; moved: boolean } | null = null;
      let pinch = 0;
      const ndc = (e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        return [((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1] as const;
      };
      const onDown = (e: PointerEvent) => {
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 1) down = { x: e.clientX, y: e.clientY, at: performance.now(), moved: false };
        else { down = null; const [p, q] = [...pointers.values()]; pinch = Math.hypot(p.x - q.x, p.y - q.y); }
        el.setPointerCapture(e.pointerId);
      };
      const onMove = (e: PointerEvent) => {
        const prev = pointers.get(e.pointerId);
        if (!prev) {
          if (e.pointerType === "mouse") { const [x, y] = ndc(e); el.style.cursor = inst.pick(x, y) ? "pointer" : "grab"; }
          return;
        }
        const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 2) {
          const [p, q] = [...pointers.values()];
          const dist = Math.hypot(p.x - q.x, p.y - q.y);
          if (pinch > 0 && dist > 0) inst.zoom(pinch / dist);
          pinch = dist;
          return;
        }
        if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) down.moved = true;
        if (down?.moved) inst.orbit(dx, dy);
      };
      const onUp = (e: PointerEvent) => {
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinch = 0;
        if (down && !down.moved && performance.now() - down.at < 600 && e.type === "pointerup") {
          const [x, y] = ndc(e);
          const id = inst.pick(x, y);
          if (id) select.current?.(drawerOf(id));
        }
        down = null;
      };
      const onWheel = (e: WheelEvent) => {
        // Plain wheel scrolls the page; ctrl+wheel (and trackpad pinch) zooms the floor.
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        inst.zoom(Math.exp(e.deltaY * 0.002));
      };
      el.addEventListener("pointerdown", onDown);
      el.addEventListener("pointermove", onMove);
      el.addEventListener("pointerup", onUp);
      el.addEventListener("pointercancel", onUp);
      el.addEventListener("wheel", onWheel, { passive: false });
      cleanups.push(() => {
        observer.disconnect();
        seen.disconnect();
        document.removeEventListener("visibilitychange", sync);
        el.removeEventListener("webglcontextlost", lost);
        el.removeEventListener("pointerdown", onDown);
        el.removeEventListener("pointermove", onMove);
        el.removeEventListener("pointerup", onUp);
        el.removeEventListener("pointercancel", onUp);
        el.removeEventListener("wheel", onWheel);
      });
    };
    void start();
    return () => {
      cancelled = true;
      cleanups.forEach(f => f());
      instance?.dispose();
      engine.current = null;
    };
  }, [attempt]);

  // Every render hands the engine the latest data; it redraws a screen only when its content changed.
  useEffect(() => {
    const busy = Object.fromEntries(STATIONS.map(s => [s.id, active(s.id)])) as Record<StationId, boolean>;
    engine.current?.setData({ market, state, actions, latest, busy, xp: xp ?? null, selected: selected ?? null });
  });
  useEffect(() => { engine.current?.setRoster(roster); }, [roster, ready]);

  const summary = STATIONS.map(s => `${s.label}: ${latest[s.id]?.headline ?? "idle"}`).join("; ");
  const mode = state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM";
  const floorState = stateFresh ? "FLOOR STATE LIVE" : state == null ? "FLOOR STATE UNAVAILABLE" : "FLOOR STATE STALE · last known";
  const pct = Math.round(progress * 100);
  return (
    <div ref={wrap} className="fl-scene fl-3d" role="group" aria-label={`Live 3D trading floor, desks in pipeline order. ${summary}`}>
      <canvas ref={canvas} aria-hidden="true" />
      <ul className="fl-3d-badges" aria-label="Data on this floor">
        <li className={state == null ? "stale" : ""}>{mode}</li>
        <li className={stateFresh ? "live" : "stale"}>{floorState}</li>
        <li className={market.connected ? "live" : "stale"}>{market.connected ? "MARKET LIVE PUBLIC" : "MARKET LAST KNOWN / REST"}</li>
        <li className={roster ? "" : "stale"}>{roster ? `AGENTS ${roster.capital}` : "AGENT ROSTER UNAVAILABLE"}</li>
      </ul>
      <p className="fl-3d-help" aria-hidden="true">arraste para girar · pinça para zoom · toque numa mesa</p>
      {!ready && !failed && (
        <div className="fl-3d-loading" role="status">
          <span>Carregando pregão 3D… {pct}%</span>
          <span className="fl-3d-bar" role="progressbar" aria-label="3D floor assets" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><i style={{ width: `${pct}%` }} /></span>
        </div>
      )}
      {failed && (
        <div className="fl-3d-loading" role="alert">
          <span>3D floor assets unavailable: the room could not be loaded.</span>
          <button type="button" className="btn" onClick={() => setAttempt(n => n + 1)}>Try again</button>
        </div>
      )}
    </div>
  );
}

