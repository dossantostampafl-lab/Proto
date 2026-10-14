import { useEffect, useMemo, useRef, useState } from "react";
import { get, WS_BASE } from "../api";
import type { Shared } from "../main";
import { Button, Panel, Tag } from "../ui";

import { type FloorAction, type FloorState, isHalted, Scene, STATIONS, type StationId, type Tone } from "./floorScene";

const ACTIVE_MS = 7000;

/** Bootstrap from /v1/floor/state, then stream /ws/floor; state is re-read every 5 s for accounts and controls. */
function useFloor() {
  const [state, setState] = useState<FloorState | null>(null);
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
      if (!live || !r.ok) return;
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
  const active = (s: StationId) => (activeAt[s] ?? 0) > now - ACTIVE_MS;
  return { state, actions, latest, active, connected };
}

const TONE_LABEL: Record<Tone, string> = { ok: "ok", warn: "attention", alert: "alert", working: "working" };

export function Floor({ market }: Shared) {
  const floor = useFloor();
  const { state } = floor;
  const killed = isHalted(state);
  const recent = [...floor.actions].reverse().slice(0, 30);
  return (
    <div className="floor-view">
      <Panel
        title="Trading floor"
        className="floor-stage"
        actions={<span className="floor-badges">
          <Tag source={state?.data_mode === "TESTNET" ? "testnet" : "paper"} />
          <span className={floor.connected ? "floor-live" : "floor-stale"}>{floor.connected ? "agents streaming" : "reconnecting"}</span>
        </span>}
      >
        <Scene market={market} state={state} actions={floor.actions} latest={floor.latest} active={floor.active} />
      </Panel>

      <div className="floor-side">
        <Panel title="Controls" source="paper">
          <ul className="floor-controls">
            <li><span>Agents</span><b>{state ? (state.pipeline.agents_enabled ? `on · ${state.pipeline.reviewer}` : "off") : "—"}</b></li>
            <li><span>Execution</span><b>{state ? (state.pipeline.execution_backend === "executor" ? "executor · testnet" : state.pipeline.paper_execution_enabled ? "paper venue" : "proposals only") : "—"}</b></li>
            <li><span>Journal</span><b className={state?.journal === "VALID" ? "up" : "down"}>{state?.journal ?? "—"}</b></li>
            <li><span>Kill switch</span><b className={killed ? "down" : "up"}>{killed ? "ON" : "armed"}</b></li>
            <li><span>Lockdown</span><b className={state?.controls.lockdown ? "down" : ""}>{state?.controls.lockdown ?? "none"}</b></li>
            <li><span>Halted venues</span><b className={state?.controls.killed_venues?.length ? "down" : ""}>{state?.controls.killed_venues?.join(", ") || "none"}</b></li>
          </ul>
          <div className="row">
            <Button label="Halt all trading" path="/v1/risk/killswitch" body={{ scope: "GLOBAL", action: "TRIGGER" }} kind="danger">Halt all trading</Button>
          </div>
          <p className="hint">Operator token required. Releasing a halt stays a deliberate operator action.</p>
        </Panel>

        <Panel title="Agent feed" source="paper" className="floor-feed">
          {recent.length ? (
            <ol aria-live="polite">
              {recent.map(a => (
                <li key={`${a.seq}:${a.station}`} className={`tone-${a.tone}`}>
                  <time>{a.at.slice(11, 19)}</time>
                  <b>{STATIONS.find(s => s.id === a.station)?.label}</b>
                  <span>{a.headline}</span>
                  <em>{TONE_LABEL[a.tone]}</em>
                </li>
              ))}
            </ol>
          ) : <p className="empty">No agent activity yet. Alerts from TradingView start the chain.</p>}
        </Panel>
      </div>
    </div>
  );
}
