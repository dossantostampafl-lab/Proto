import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get, WS_BASE } from "../api";
import type { Shared } from "../main";
import { Button, Panel, Tag } from "../ui";
import { DeskBoundary, DeskDrawer, DeskFailed, useDeskSelection, useXpLane, XpStrip } from "./floorDesks";
import { hms, TONE_LABEL } from "./floorModel";

import { type FloorViewMode, useFloorViewMode } from "./floorViewPref";
import { canRender3d, Fallback, type FloorAction, type FloorState, isHalted, type PromotionScope, Scene, STAGES, STATIONS, type StationId, TONE_COLOR, type Tone, type WatchdogSummary } from "./floorScene";

function PromotionPath({ scopes }: { scopes: PromotionScope[] | undefined }) {
  if (!scopes) return <p className="empty">Promotion data unavailable.</p>;
  if (!scopes.length) return <p className="empty">No scope registered yet. Operators promote scopes through /v1/promotion.</p>;
  return (
    <ul className="fl-promo">
      {scopes.map(p => {
        const at = STAGES.indexOf(p.stage as (typeof STAGES)[number]);
        return (
          <li key={p.scope.key}>
            <header>
              <b>{p.scope.strategy_id}</b>
              <span>{p.scope.venue} · {p.scope.credential_id} · {p.scope.model_version}</span>
            </header>
            <ol className="fl-ladder" aria-label={`Stage ${p.stage}`}>
              {STAGES.map((stage, i) => (
                <li key={stage} className={i < at ? "done" : i === at ? "now" : ""}>{stage}</li>
              ))}
            </ol>
            <p className="fl-promo-meta">
              <span>{p.clean_days.toFixed(1)} clean days</span>
              <span className={p.violations ? "down" : ""}>{p.violations} violations</span>
              {p.next_stage && <span>next: {p.next_stage}</span>}
            </p>
            {p.blockers.length > 0 && <ul className="fl-blockers">{p.blockers.map(b => <li key={b}>{b}</li>)}</ul>}
            {p.next_stage === "CANARY" && <p className="hint">CANARY also needs an authenticated operator and sizes on at most R$500.</p>}
          </li>
        );
      })}
    </ul>
  );
}

/** The independent watchdog as the API last saw it. It can only halt; releasing is an operator action. */
function Watchdog({ watchdog }: { watchdog: WatchdogSummary | undefined }) {
  if (!watchdog) return <p className="empty">Watchdog status unavailable.</p>;
  const covered = Math.min(1, watchdog.coverage_hours / watchdog.required_hours);
  return (
    <>
      <ul className="floor-controls">
        <li><span>Heartbeat</span><b className={watchdog.active ? "up" : "down"}>{watchdog.active ? "active" : watchdog.last_beat ? "stale" : "never seen"}</b></li>
        <li><span>Last beat</span><b>{hms(watchdog.last_beat)}</b></li>
        <li><span>Coverage</span><b className={covered >= 1 ? "up" : ""}>{watchdog.coverage_hours.toFixed(1)} h / {watchdog.required_hours} h</b></li>
        <li><span>Halts</span><b className={watchdog.trips ? "down" : ""}>{watchdog.trips}</b></li>
      </ul>
      <div className="fl-cover" role="progressbar" aria-label="Watchdog coverage toward the canary gate" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(covered * 100)}><i style={{ width: `${covered * 100}%` }} /></div>
      {watchdog.last_trip && <p className="hint down">Last halt {watchdog.last_trip.at.slice(0, 19).replace("T", " ")}: {watchdog.last_trip.reasons.join(", ") || "no reason given"}</p>}
      <p className="hint">The watchdog only halts. CANARY needs {watchdog.required_hours} h of unbroken coverage.</p>
    </>
  );
}

/** One card per desk: the same live headline in readable type, and a button that opens the desk. */
function DeskCards({ latest, active, selected, onSelect }: {
  latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean;
  selected: StationId | null; onSelect: (id: StationId) => void;
}) {
  return (
    <ul className="fl-desks" aria-label="Agent desks">
      {STATIONS.map(s => {
        const a = latest[s.id];
        const color = TONE_COLOR[a?.tone ?? "idle"];
        return (
          <li key={s.id} className={active(s.id) ? "busy" : ""} style={{ borderColor: color }}>
            <button type="button" aria-haspopup="dialog" data-desk={s.id} aria-label={`Open the ${s.label} desk, status ${TONE_LABEL[a?.tone ?? "idle"]}`} onClick={() => onSelect(s.id)}>
              <b style={{ color }}>{s.label}</b>
              <span>{a?.headline ?? "waiting for work"}</span>
              <small>{TONE_LABEL[a?.tone ?? "idle"]} · {a ? `${hms(a.at)}${a.symbol ? ` · ${a.symbol}` : ""}` : s.role}</small>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

const ACTIVE_MS = 7000;

// The stylized building ("Prédio") is its own on-demand chunk, loaded only when that view is chosen.
const FloorBuilding = lazy(() => import("./floorBuilding/FloorBuilding"));
// The mission-control room ("Controle") is a third on-demand chunk, loaded only when chosen.
const FloorControl = lazy(() => import("./floorControl/FloorControl"));

/** "Realista | Prédio | Controle": which view of the same live floor is shown; the choice is remembered on this device. */
function ViewToggle({ mode, choose }: { mode: FloorViewMode; choose: (m: FloorViewMode) => void }) {
  return (
    <div className="fl-toggle" role="group" aria-label="Vista do pregão">
      <button type="button" aria-pressed={mode === "realistic"} onClick={() => choose("realistic")}>Realista</button>
      <button type="button" aria-pressed={mode === "building"} onClick={() => choose("building")}>Prédio</button>
      <button type="button" aria-pressed={mode === "control"} onClick={() => choose("control")}>Controle</button>
    </div>
  );
}

function BuildingUnavailable({ text }: { text: string }) {
  return <div className="fl-scene fb-scene" role="status"><p className="fl-3d-loading">{text}</p></div>;
}

/** Bootstrap from /v1/floor/state, then stream /ws/floor; state is re-read every 5 s for accounts and controls. */
function useFloor() {
  const [state, setState] = useState<FloorState | null>(null);
  const [stateFresh, setStateFresh] = useState(false);
  const [actions, setActions] = useState<FloorAction[]>([]);
  const [activeAt, setActiveAt] = useState<Partial<Record<StationId, number>>>({});
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const seen = useRef(new Set<string>());

  useEffect(() => {
    let live = true;
    const key = (a: FloorAction) => `${a.seq}:${a.station}`;
    const merge = (incoming: FloorAction[], fresh: boolean) => {
      const novel = incoming.filter(a => !seen.current.has(key(a)));
      if (!novel.length) return;
      novel.forEach(a => seen.current.add(key(a)));
      setActions(old => [...old, ...novel].sort((a, b) => a.seq - b.seq).slice(-120));
      if (fresh) setActiveAt(old => ({ ...old, ...Object.fromEntries(novel.map(a => [a.station, Date.now()])) }));
    };
    const load = async () => {
      const r = await get<FloorState>("/v1/floor/state");
      if (!live) return;
      setStateFresh(r.ok);
      if (!r.ok) return;
      setState(r.data);
      merge(r.data.actions, false);
    };
    void load();
    const poll = window.setInterval(() => void load(), 5000);
    const tick = window.setInterval(() => setNow(Date.now()), 500);

    let socket: WebSocket | null = null;
    let retry: number | null = null;
    let attempt = 0;
    const connect = () => {
      socket = new WebSocket(`${WS_BASE}/ws/floor`);
      socket.onopen = () => { attempt = 0; setConnected(true); };
      socket.onmessage = event => {
        try {
          const msg = JSON.parse(String(event.data)) as { type: string; data?: FloorAction };
          if (msg.type === "floor" && msg.data) merge([msg.data], true);
        } catch { /* malformed frame ignored */ }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!live) return;
        retry = window.setTimeout(connect, Math.min(15000, 750 * 2 ** attempt));
        attempt = Math.min(attempt + 1, 5);
      };
    };
    connect();
    return () => {
      live = false;
      window.clearInterval(poll);
      window.clearInterval(tick);
      if (retry !== null) window.clearTimeout(retry);
      socket?.close();
    };
  }, []);

  const latest = useMemo(() => {
    const out: Partial<Record<StationId, FloorAction>> = {};
    for (const a of actions) out[a.station] = a;
    return out;
  }, [actions]);
  const active = (s: StationId) => connected && (activeAt[s] ?? 0) > now - ACTIVE_MS;
  return { state, stateFresh, actions, latest, active, connected };
}


export function Floor({ market }: Shared) {
  const floor = useFloor();
  const { selected, select } = useDeskSelection();
  const xp = useXpLane();
  const [viewMode, chooseView] = useFloorViewMode();
  const [buildingFailed, setBuildingFailed] = useState(false);
  const closeDesk = useCallback(() => select(null), [select]);
  const pan = useRef<HTMLDivElement>(null);
  // On narrow screens the scene pans; start centred on the desks, not on the window wall.
  useEffect(() => {
    const el = pan.current;
    if (el && el.scrollWidth > el.clientWidth) el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
  }, []);
  const { state } = floor;
  const killed = isHalted(state);
  const recent = [...floor.actions].reverse().slice(0, 30);
  return (
    <div className={`floor-view${viewMode === "control" ? " floor-view-wide" : ""}`}>
      <XpStrip xp={xp} />
      <Panel
        title="Trading floor"
        className="floor-stage"
        actions={<span className="floor-badges">
          <ViewToggle mode={viewMode} choose={chooseView} />
          {state ? <Tag source={state.data_mode === "TESTNET" ? "testnet" : "paper"} /> : <span className="floor-stale">mode unavailable</span>}
          <span className={floor.connected ? "floor-live" : "floor-stale"}>{floor.connected ? "agent stream connected" : "agent stream reconnecting"}</span>
          <span className={market.connected ? "floor-live" : "floor-stale"}>{market.connected ? "market stream connected" : "market stream offline · REST polling"}</span>
          {!floor.stateFresh && <span className="floor-stale">floor state {state == null ? "unavailable" : "last known"}</span>}
        </span>}
      >
        {viewMode === "control" ? (
          (
            <Fallback onError={() => setBuildingFailed(true)}>
              <Suspense fallback={<BuildingUnavailable text="Carregando sala de controle…" />}>
                <FloorControl market={market} state={state} stateFresh={floor.stateFresh} actions={floor.actions} latest={floor.latest} active={floor.active} xp={xp.lane} />
              </Suspense>
            </Fallback>
          )
        ) : viewMode === "building" ? (
          buildingFailed || !canRender3d() ? <BuildingUnavailable text="Prédio indisponível: este dispositivo não roda WebGL. Use a vista Realista." /> : (
            <Fallback onError={() => setBuildingFailed(true)}>
              <Suspense fallback={<BuildingUnavailable text="Carregando prédio…" />}>
                <FloorBuilding market={market} state={state} stateFresh={floor.stateFresh} actions={floor.actions} latest={floor.latest} active={floor.active} xp={xp.lane} />
              </Suspense>
            </Fallback>
          )
        ) : <div className="fl-pan" ref={pan}><Scene market={market} state={state} stateFresh={floor.stateFresh} actions={floor.actions} latest={floor.latest} active={floor.active} xp={xp.lane.state} onSelect={select} selected={selected} /></div>}
        <p className="fl-hint">Tap or click a desk to open it: live data, its recent actions and, with the operator token, its actions. Drag to orbit the room; the desk cards below open the same desks from the keyboard.</p>
        <DeskCards latest={floor.latest} active={floor.active} selected={selected} onSelect={select} />
      </Panel>

      <div className="floor-side">
        <Panel title="Controls" source={state == null ? undefined : state.data_mode === "TESTNET" ? "testnet" : "paper"}>
          <ul className="floor-controls">
            <li><span>Agents</span><b>{state ? (state.pipeline.agents_enabled ? `on · ${state.pipeline.reviewer}` : "off") : "—"}</b></li>
            <li><span>Execution</span><b>{state ? (state.pipeline.execution_backend === "executor" ? "executor · testnet" : state.pipeline.paper_execution_enabled ? "paper venue" : "proposals only") : "—"}</b></li>
            <li><span>Journal</span><b className={state == null ? "muted" : state.journal === "VALID" ? "up" : "down"}>{state?.journal ?? "—"}</b></li>
            <li><span>Kill switch</span><b className={state == null ? "muted" : killed ? "down" : "up"}>{state == null ? "—" : killed ? "ON" : "armed"}</b></li>
            <li><span>Lockdown</span><b className={state?.controls.lockdown ? "down" : ""}>{state == null ? "—" : state.controls.lockdown ?? "none"}</b></li>
            <li><span>Halted venues</span><b className={state?.controls.killed_venues?.length ? "down" : ""}>{state == null ? "—" : state.controls.killed_venues?.join(", ") || "none"}</b></li>
          </ul>
          <div className="row">
            <Button label="Halt all trading" path="/v1/risk/killswitch" body={{ scope: "GLOBAL", action: "TRIGGER" }} kind="danger">Halt all trading</Button>
          </div>
          <p className="hint">Operator token required. Releasing a halt stays a deliberate operator action.</p>
        </Panel>

        <Panel title="Watchdog" source={state == null ? undefined : state.data_mode === "TESTNET" ? "testnet" : "paper"}>
          <Watchdog watchdog={state?.watchdog} />
        </Panel>

        <Panel title="Promotion path" source={state == null ? undefined : state.data_mode === "TESTNET" ? "testnet" : "paper"}>
          <PromotionPath scopes={state?.promotion} />
        </Panel>

        <Panel title="Agent feed" source={state == null ? undefined : state.data_mode === "TESTNET" ? "testnet" : "paper"} className="floor-feed">
          {recent.length ? (
            <ol aria-live="polite">
              {recent.map(a => (
                <li key={`${a.seq}:${a.station}`} className={`tone-${a.tone}`}>
                  <time>{hms(a.at)}</time>
                  <b>{STATIONS.find(s => s.id === a.station)?.label}</b>
                  <span>{a.headline}</span>
                  <em>{TONE_LABEL[a.tone]}</em>
                  {a.why && a.why.length > 0 && (
                    <details>
                      <summary>why</summary>
                      <ul>{a.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          ) : <p className="empty">No agent activity yet. Alerts from TradingView start the chain.</p>}
        </Panel>
      </div>
      {selected && (
        <DeskBoundary key={selected} fallback={<DeskFailed onClose={closeDesk} />}>
          <DeskDrawer id={selected} state={state} stateFresh={floor.stateFresh} actions={floor.actions} latest={floor.latest} xp={xp} onClose={closeDesk} />
        </DeskBoundary>
      )}
    </div>
  );
}
