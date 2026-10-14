// Live 3D trading room. Loaded on demand by the Floor view; reports failure so the view can fall
// back to the photo room (no WebGL, no OffscreenCanvas, or a lost GPU context).
import { useEffect, useRef } from "react";
import type { Shared } from "../../main";
import { type FloorAction, type FloorState, STATIONS, type StationId } from "../floorModel";
import { FloorEngine } from "./engine";

export default function Floor3D({ market, state, actions, latest, active, onFail }: {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  active: (s: StationId) => boolean;
  onFail: () => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<FloorEngine | null>(null);
  const fail = useRef(onFail);
  fail.current = onFail;

  useEffect(() => {
    const el = canvas.current, box = wrap.current;
    if (!el || !box) return;
    let instance: FloorEngine;
    try {
      instance = new FloorEngine(el, window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch {
      fail.current();
      return;
    }
    engine.current = instance;
    const lost = (e: Event) => { e.preventDefault(); fail.current(); };
    el.addEventListener("webglcontextlost", lost);
    const observer = new ResizeObserver(([entry]) => instance.resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(box);
    return () => {
      observer.disconnect();
      el.removeEventListener("webglcontextlost", lost);
      instance.dispose();
      engine.current = null;
    };
  }, []);

  // Every render hands the engine the latest data; it redraws the screens at most twice a second.
  useEffect(() => {
    const busy = Object.fromEntries(STATIONS.map(s => [s.id, active(s.id)])) as Record<StationId, boolean>;
    engine.current?.setData({ market, state, actions, latest, busy });
  });

  const summary = STATIONS.map(s => `${s.label}: ${latest[s.id]?.headline ?? "idle"}`).join("; ");
  return (
    <div ref={wrap} className="fl-scene fl-3d" role="img" aria-label={`Live 3D trading floor. ${summary}`}>
      <canvas ref={canvas} />
    </div>
  );
}
