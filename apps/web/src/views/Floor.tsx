import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { get, type Sym, SYMBOLS, WS_BASE } from "../api";
import type { Shared } from "../main";
import { Button, Panel, pct, Tag, usd } from "../ui";

// ---------- data ----------
type Tone = "ok" | "warn" | "alert" | "working";
type StationId = "tradingview" | "fusion" | "reviewer" | "risk" | "oms" | "reconciliation";
export type FloorAction = { seq: number; at: string; event_type: string; station: StationId; tone: Tone; headline: string; symbol: string | null };
type Account = { equity: string; start_of_day_equity: string; peak_equity: string; gross_exposure: string; positions: Record<string, string> } | null;
type FloorState = {
  data_mode: "PAPER" | "TESTNET";
  journal: string;
  stations: Record<StationId, FloorAction | null>;
  actions: FloorAction[];
  controls: { global_kill?: boolean; killed_venues?: string[]; killed_strategies?: string[]; lockdown?: string | null; runtime_kill_switch?: string };
  accounts: Record<string, Account>;
  pipeline: { agents_enabled: boolean; reviewer: string; execution_backend: string; paper_execution_enabled: boolean };
};

const ACTIVE_MS = 7000;

/** Any global halt: the Risk Governor's kill switch or the runtime kill switch leaving ARMED. */
const isHalted = (state: FloorState | null) =>
  Boolean(state?.controls.global_kill) || (state?.controls.runtime_kill_switch ?? "ARMED") !== "ARMED";

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

// ---------- isometric geometry ----------
const TW = 96, TH = 48, OX = 480, OY = 268;
const iso = (x: number, y: number, z = 0): [number, number] => [OX + (x - y) * (TW / 2), OY + (x + y) * (TH / 2) - z];
const pts = (...p: [number, number][]) => p.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
const WALL_H = 262, ROOM = 9;
const UNIT = Math.hypot(TW / 2, TH / 2); // on-screen length of one grid unit along a wall
const ALONG_Y = `${Math.cos(Math.atan(0.5))} ${-Math.sin(Math.atan(0.5))} 0 1`;
const ALONG_X = `${Math.cos(Math.atan(0.5))} ${Math.sin(Math.atan(0.5))} 0 1`;

/** Deterministic pseudo-random in [0, 1) so the skyline is the same on every render. */
const rand = (n: number) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

type StationDef = { id: StationId; label: string; role: string; x: number; y: number; hair: string; shirt: string };
const STATIONS: StationDef[] = [
  { id: "tradingview", label: "TRADINGVIEW", role: "alerts in", x: 1.4, y: 2.7, hair: "#2b1a12", shirt: "#3a6df0" },
  { id: "fusion", label: "FUSION", role: "signal vote", x: 3.9, y: 2.7, hair: "#d8b26a", shirt: "#7a3cf0" },
  { id: "reviewer", label: "AI REVIEWER", role: "shrink only", x: 6.4, y: 2.7, hair: "#111", shirt: "#c23ad6" },
  { id: "risk", label: "RISK ENGINE", role: "permits", x: 6.4, y: 6.3, hair: "#5a2e14", shirt: "#e0445c" },
  { id: "oms", label: "OMS / EXECUTOR", role: "orders", x: 3.9, y: 6.3, hair: "#222", shirt: "#18a07a" },
  { id: "reconciliation", label: "RECONCILIATION", role: "ledger = venue", x: 1.4, y: 6.3, hair: "#8a5a2b", shirt: "#d98a1c" },
];
const TONE_COLOR: Record<Tone | "idle", string> = { ok: "#3dffa2", warn: "#ffcc33", alert: "#ff3b5c", working: "#00f0ff", idle: "#4d6a8a" };

function Box({ x, y, w, d, h, z = 0, top, left, right }: { x: number; y: number; w: number; d: number; h: number; z?: number; top: string; left: string; right: string }) {
  const a = iso(x, y, z + h), b = iso(x + w, y, z + h), c = iso(x + w, y + d, z + h), e = iso(x, y + d, z + h);
  const b0 = iso(x + w, y, z), c0 = iso(x + w, y + d, z), e0 = iso(x, y + d, z);
  return (
    <g>
      <polygon points={pts(e, c, c0, e0)} fill={left} />
      <polygon points={pts(c, b, b0, c0)} fill={right} />
      <polygon points={pts(a, b, c, e)} fill={top} />
    </g>
  );
}

function City() {
  // Drawn flat, then skewed onto the left wall (which runs along the y axis).
  const [wx, wy] = iso(0, ROOM, 0);
  const width = ROOM * UNIT;
  const buildings = Array.from({ length: 22 }, (_, i) => {
    const w = 22 + rand(i) * 34, h = 80 + rand(i + 50) * 170, x = (i / 22) * width - 6;
    return { x, w, h, lit: Array.from({ length: Math.floor(h / 12) * 3 }, (_, k) => rand(i * 97 + k) > 0.62) };
  });
  return (
    <g transform={`matrix(${ALONG_Y} ${wx} ${wy})`}>
      <rect x={0} y={-WALL_H} width={width} height={WALL_H} fill="url(#floor-sky)" />
      {buildings.map((b, i) => (
        <g key={i}>
          <rect x={b.x} y={-b.h} width={b.w} height={b.h} fill={i % 3 ? "#0b1530" : "#101d40"} />
          {b.lit.map((on, k) => on && (
            <rect key={k} x={b.x + 3 + (k % 3) * (b.w / 3.2)} y={-b.h + 6 + Math.floor(k / 3) * 12} width={3} height={4}
              fill={rand(i + k) > 0.8 ? "#ff5fd2" : rand(i * k + 3) > 0.5 ? "#7ff8ff" : "#ffd27a"} opacity={0.85} />
          ))}
        </g>
      ))}
      <rect x={width * 0.18} y={-150} width={46} height={14} fill="none" stroke="#ff2bd6" strokeWidth={2} opacity={0.8} className="neon" />
      <rect x={width * 0.62} y={-120} width={38} height={10} fill="none" stroke="#00f0ff" strokeWidth={2} opacity={0.7} className="neon slow" />
      {Array.from({ length: 5 }, (_, i) => <rect key={i} x={(i * width) / 5} y={-WALL_H} width={5} height={WALL_H} fill="#05080f" />)}
      <rect x={0} y={-WALL_H} width={width} height={6} fill="#05080f" />
      <rect x={0} y={-12} width={width} height={12} fill="#0a1222" />
      <g className="rain">{Array.from({ length: 40 }, (_, i) => <line key={i} x1={rand(i * 3) * width} y1={-WALL_H + rand(i) * 200} x2={rand(i * 3) * width - 4} y2={-WALL_H + rand(i) * 200 + 14} />)}</g>
    </g>
  );
}

function WallScreen({ market, state }: { market: Shared["market"]; state: FloorState | null }) {
  const [wx, wy] = iso(0.6, 0, 0);
  const width = 8 * UNIT;
  const usdt = state?.accounts?.USDT ?? null;
  const equity = usdt ? Number(usdt.equity) : null;
  const sod = usdt ? Number(usdt.start_of_day_equity) : null;
  const peak = usdt ? Number(usdt.peak_equity) : null;
  const daily = equity != null && sod ? equity / sod - 1 : null;
  const drawdown = equity != null && peak ? equity / peak - 1 : null;
  const series = market.series.BTC.slice(-60);
  const lo = Math.min(...series), hi = Math.max(...series), span = Math.max(hi - lo, 1e-9);
  const spark = series.map((v, i) => `${296 + (i / Math.max(series.length - 1, 1)) * 120},${-82 - ((v - lo) / span) * 62}`).join(" ");
  const killed = isHalted(state);
  return (
    <g transform={`matrix(${ALONG_X} ${wx} ${wy})`} className="wallscreen">
      <rect x={0} y={-238} width={width} height={176} rx={4} fill="#06142b" stroke="#2a7bd8" strokeWidth={2} />
      <rect x={4} y={-234} width={width - 8} height={168} fill="url(#floor-screen)" opacity={0.9} />
      <text x={14} y={-216} className="ws-title">PROTO · {state?.data_mode ?? "…"} · {killed ? "KILL SWITCH ON" : "ARMED"}</text>
      {SYMBOLS.map((s: Sym, i) => {
        const series_ = market.series[s];
        const mid = market.frames[s]?.mid ?? series_[series_.length - 1];
        const change = series_.length > 1 && mid ? mid / series_[0] - 1 : null;
        return (
          <g key={s} transform={`translate(14 ${-192 + i * 22})`}>
            <text className="ws-sym">{s}</text>
            <text x={50} className="ws-num">{usd(mid)}</text>
            <text x={150} className={`ws-num ${change == null ? "" : change >= 0 ? "ws-up" : "ws-down"}`}>{change == null ? "—" : `${change >= 0 ? "▲" : "▼"} ${pct(Math.abs(change))}`}</text>
          </g>
        );
      })}
      <text x={14} y={-112} className="ws-label">EQUITY USDT</text>
      <text x={110} y={-112} className="ws-num">{equity == null ? "—" : equity.toFixed(2)}</text>
      <text x={14} y={-94} className="ws-label">DAY / LIMIT</text>
      <text x={110} y={-94} className={`ws-num ${daily != null && daily < -0.0075 ? "ws-down" : ""}`}>{pct(daily)} / -1.00%</text>
      <text x={14} y={-76} className="ws-label">DD / LIMIT</text>
      <text x={110} y={-76} className={`ws-num ${drawdown != null && drawdown < -0.04 ? "ws-down" : ""}`}>{pct(drawdown)} / -5.00%</text>
      <rect x={290} y={-206} width={132} height={132} fill="none" stroke="#2a7bd8" strokeWidth={1} />
      <text x={296} y={-194} className="ws-label">BTC LIVE PUBLIC</text>
      {series.length > 1 && <polyline points={spark} fill="none" stroke="#3dffa2" strokeWidth={2} />}
    </g>
  );
}

function Person({ x, y, hair, shirt, busy, seated = true }: { x: number; y: number; hair: string; shirt: string; busy: boolean; seated?: boolean }) {
  const [px, py] = iso(x, y, seated ? 18 : 0);
  return (
    <g transform={`translate(${px.toFixed(1)} ${py.toFixed(1)}) scale(1.5)`} className={busy ? "person busy" : "person"}>
      {seated && <rect x={-9} y={-6} width={18} height={14} fill="#1a2238" />}
      <rect x={-6} y={-20} width={12} height={16} fill={shirt} />
      <rect x={-5} y={-30} width={10} height={10} fill="#e7b48f" />
      <rect x={-5} y={-32} width={10} height={4} fill={hair} />
      <rect className="arm" x={-9} y={-18} width={3} height={9} fill={shirt} />
      <rect className="arm right" x={6} y={-18} width={3} height={9} fill={shirt} />
      {!seated && <rect x={-5} y={-4} width={4} height={14} fill="#111a2e" />}
      {!seated && <rect x={1} y={-4} width={4} height={14} fill="#111a2e" />}
      {!seated && <rect x={7} y={-22} width={8} height={10} fill="#7ff8ff" opacity={0.7} />}
    </g>
  );
}

function Station({ def, action, busy }: { def: StationDef; action: FloorAction | undefined; busy: boolean }) {
  const tone: Tone | "idle" = action ? action.tone : "idle";
  const color = TONE_COLOR[tone];
  const [hx, hy] = iso(def.x + 0.8, def.y + 0.35, 112);
  const text = action?.headline ?? "waiting for work";
  return (
    <g className={`station ${busy ? "busy" : ""} tone-${tone}`}>
      <Person x={def.x + 0.75} y={def.y - 0.55} hair={def.hair} shirt={def.shirt} busy={busy} />
      <Box x={def.x} y={def.y} w={1.6} d={0.7} h={16} top="#2a3550" left="#141b2e" right="#1d2740" />
      {[0, 0.55, 1.1].map(o => {
        const [mx, my] = iso(def.x + 0.15 + o, def.y + 0.1, 16);
        return <g key={o} transform={`matrix(1 0.5 0 1 ${mx} ${my})`}><rect x={0} y={-22} width={22} height={19} fill="#ffb84d" className="monitor" /><rect x={3} y={-19} width={16} height={3} fill="#fff1c9" opacity={0.7} /></g>;
      })}
      <line x1={hx} y1={hy + 6} x2={hx} y2={hy + 46} stroke={color} strokeOpacity={0.35} strokeDasharray="2 3" />
      <g transform={`translate(${(hx - 64).toFixed(1)} ${(hy - 42).toFixed(1)})`} className="holo">
        <rect x={0} y={0} width={128} height={48} fill="#061226" fillOpacity={0.82} stroke={color} strokeOpacity={busy ? 1 : 0.5} />
        <rect x={0} y={0} width={128} height={48} fill={color} fillOpacity={busy ? 0.16 : 0.05} />
        <text x={7} y={14} className="holo-title" fill={color}>{def.label}</text>
        <text x={7} y={27} className="holo-role">{def.role}{action?.symbol ? ` · ${action.symbol}` : ""}</text>
        <text x={7} y={40} className="holo-text">{text.length > 22 ? `${text.slice(0, 21)}…` : text}</text>
      </g>
      <title>{`${def.label}: ${text}`}</title>
    </g>
  );
}

function Beam({ from, to, busy, color }: { from: StationDef; to: StationDef; busy: boolean; color: string }) {
  const [x1, y1] = iso(from.x + 0.8, from.y + 0.35, 4), [x2, y2] = iso(to.x + 0.8, to.y + 0.35, 4);
  return <line x1={x1} y1={y1} x2={x2} y2={y2} className={`beam ${busy ? "busy" : ""}`} stroke={color} />;
}

function Scene({ market, floor }: { market: Shared["market"]; floor: ReturnType<typeof useFloor> }) {
  const tiles: ReactNode[] = [];
  for (let x = 0; x < ROOM; x++) for (let y = 0; y < ROOM; y++) {
    tiles.push(<polygon key={`${x}-${y}`} points={pts(iso(x, y), iso(x + 1, y), iso(x + 1, y + 1), iso(x, y + 1))} fill={(x + y) % 2 ? "#0d1730" : "#0f1b38"} stroke="#18284a" strokeWidth={0.6} />);
  }
  const summary = STATIONS.map(s => `${s.label}: ${floor.latest[s.id]?.headline ?? "idle"}`).join("; ");
  return (
    <svg className="floor-scene" viewBox="20 0 920 720" role="img" aria-label={`Trading floor. ${summary}`} shapeRendering="crispEdges">
      <defs>
        <linearGradient id="floor-sky" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#140a35" /><stop offset="1" stopColor="#0b2a52" /></linearGradient>
        <linearGradient id="floor-screen" x1="0" x2="1"><stop offset="0" stopColor="#0a2448" /><stop offset="1" stopColor="#0d1238" /></linearGradient>
      </defs>
      <rect x={20} width={920} height={720} fill="#070b16" />
      <City />
      <polygon points={pts(iso(0, 0), iso(ROOM, 0), iso(ROOM, 0, WALL_H), iso(0, 0, WALL_H))} fill="#0f1c3a" stroke="#1e3a6a" strokeWidth={2} />
      <WallScreen market={market} state={floor.state} />
      {tiles}
      <polyline points={pts(iso(0, ROOM, 0), iso(0, 0, 0), iso(ROOM, 0, 0))} fill="none" stroke="#2a7bd8" strokeWidth={2} />
      {STATIONS.slice(1).map((s, i) => (
        <Beam key={s.id} from={STATIONS[i]} to={s} busy={floor.active(s.id)} color={TONE_COLOR[floor.latest[s.id]?.tone ?? "idle"]} />
      ))}
      {[...STATIONS].sort((a, b) => a.x + a.y - (b.x + b.y)).map(s => (
        <Station key={s.id} def={s} action={floor.latest[s.id]} busy={floor.active(s.id)} />
      ))}
      <Person x={8.1} y={1.0} hair="#1a1a1a" shirt="#1f2a44" busy={false} seated={false} />
    </svg>
  );
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
        <Scene market={market} floor={floor} />
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
