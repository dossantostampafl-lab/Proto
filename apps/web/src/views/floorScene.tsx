// Isometric pixel-art trading floor, drawn in SVG. Every number and headline on it is real data from
// /v1/floor/state, /ws/floor and the live public market stream; everything else is decoration.
import type { ReactNode } from "react";
import { type Sym, SYMBOLS } from "../api";
import type { Shared } from "../main";
import { pct, usd } from "../ui";

export type Tone = "ok" | "warn" | "alert" | "working";
export type StationId = "tradingview" | "fusion" | "reviewer" | "risk" | "oms" | "reconciliation";
export type FloorAction = { seq: number; at: string; event_type: string; station: StationId; tone: Tone; headline: string; symbol: string | null };
export type Account = { equity: string; start_of_day_equity: string; peak_equity: string; gross_exposure: string; positions: Record<string, string> } | null;
export type PromotionScope = {
  scope: { account: string; venue: string; strategy_id: string; model_version: string; credential_id: string; key: string };
  stage: string;
  clean_days: number;
  next_stage: string | null;
  blockers: string[];
  violations: number;
};
export type FloorState = {
  promotion?: PromotionScope[];
  data_mode: "PAPER" | "TESTNET";
  journal: string;
  stations: Record<StationId, FloorAction | null>;
  actions: FloorAction[];
  controls: { global_kill?: boolean; killed_venues?: string[]; killed_strategies?: string[]; lockdown?: string | null; runtime_kill_switch?: string };
  accounts: Record<string, Account>;
  pipeline: { agents_enabled: boolean; reviewer: string; execution_backend: string; paper_execution_enabled: boolean };
};

/** Any global halt: the Risk Governor's kill switch or the runtime kill switch leaving ARMED. */
export const isHalted = (state: FloorState | null) =>
  Boolean(state?.controls.global_kill) || (state?.controls.runtime_kill_switch ?? "ARMED") !== "ARMED";

// ---------- geometry ----------
const TW = 104, TH = 52, OX = 500, OY = 330, ROOM = 9, WALL_H = 318;
const UNIT = Math.hypot(TW / 2, TH / 2);
const ANGLE = Math.atan(TH / TW);
const ALONG_X = `${Math.cos(ANGLE)} ${Math.sin(ANGLE)} 0 1`; // right wall (y = 0)
const ALONG_Y = `${Math.cos(ANGLE)} ${-Math.sin(ANGLE)} 0 1`; // left wall (x = 0)
type P = [number, number];
const iso = (x: number, y: number, z = 0): P => [OX + (x - y) * (TW / 2), OY + (x + y) * (TH / 2) - z];
const pts = (...p: P[]) => p.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
const rand = (n: number) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

export const TONE_COLOR: Record<Tone | "idle", string> = { ok: "#3dffa2", warn: "#ffc23d", alert: "#ff3b5c", working: "#3df0ff", idle: "#5d7fa8" };

export type StationDef = { id: StationId; label: string; role: string; x: number; y: number; hair: string; shirt: string };
export const STATIONS: StationDef[] = [
  { id: "tradingview", label: "TRADINGVIEW", role: "alerts in", x: 1.3, y: 2.4, hair: "#2b1a12", shirt: "#3a5fd0" },
  { id: "fusion", label: "FUSION", role: "signal vote", x: 3.8, y: 2.4, hair: "#c99a52", shirt: "#6b3cd0" },
  { id: "reviewer", label: "AI REVIEWER", role: "shrink only", x: 6.3, y: 2.4, hair: "#151515", shirt: "#b03ac0" },
  { id: "risk", label: "RISK ENGINE", role: "permits", x: 6.3, y: 5.6, hair: "#5a2e14", shirt: "#c8404f" },
  { id: "oms", label: "OMS / EXECUTOR", role: "orders", x: 3.8, y: 5.6, hair: "#222", shirt: "#178f6c" },
  { id: "reconciliation", label: "RECONCILIATION", role: "ledger = venue", x: 1.3, y: 5.6, hair: "#7a4f26", shirt: "#c47a1a" },
];

// ---------- primitives ----------
function Box({ x, y, w, d, h, z = 0, top, left, right, edge }: { x: number; y: number; w: number; d: number; h: number; z?: number; top: string; left: string; right: string; edge?: string }) {
  const a = iso(x, y, z + h), b = iso(x + w, y, z + h), c = iso(x + w, y + d, z + h), e = iso(x, y + d, z + h);
  const b0 = iso(x + w, y, z), c0 = iso(x + w, y + d, z), e0 = iso(x, y + d, z);
  return (
    <g>
      <polygon points={pts(e, c, c0, e0)} fill={left} />
      <polygon points={pts(c, b, b0, c0)} fill={right} />
      <polygon points={pts(a, b, c, e)} fill={top} />
      {edge && <polyline points={pts(e, c, b)} fill="none" stroke={edge} strokeWidth={1} />}
    </g>
  );
}

function Defs() {
  return (
    <defs>
      <filter id="fl-bloom" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="3.2" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="fl-glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="10" /></filter>
      <filter id="fl-haze" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="1.4" /></filter>
      <linearGradient id="fl-sky" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#0b0624" /><stop offset="0.55" stopColor="#1a1350" /><stop offset="1" stopColor="#3b1e5e" />
      </linearGradient>
      <linearGradient id="fl-wall" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#060a18" /><stop offset="1" stopColor="#101b38" />
      </linearGradient>
      <linearGradient id="fl-screen" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#071a3a" /><stop offset="1" stopColor="#0a1030" />
      </linearGradient>
      <radialGradient id="fl-amber" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stopColor="#ffb347" stopOpacity="0.38" /><stop offset="1" stopColor="#ffb347" stopOpacity="0" /></radialGradient>
      <radialGradient id="fl-cyan" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stopColor="#3df0ff" stopOpacity="0.22" /><stop offset="1" stopColor="#3df0ff" stopOpacity="0" /></radialGradient>
      <linearGradient id="fl-shaft" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stopColor="#7ff8ff" stopOpacity="0.12" /><stop offset="1" stopColor="#7ff8ff" stopOpacity="0" /></linearGradient>
      <radialGradient id="fl-vignette" cx="0.5" cy="0.45" r="0.75"><stop offset="0.6" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity="0.65" /></radialGradient>
      <pattern id="fl-scan" width="4" height="3" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="#000" opacity="0.18" /></pattern>
    </defs>
  );
}

// ---------- the room ----------
function City() {
  const [wx, wy] = iso(0, ROOM, 0);
  const width = ROOM * UNIT;
  const layer = (seed: number, count: number, minH: number, maxH: number, color: string, windows: number) =>
    Array.from({ length: count }, (_, i) => {
      const w = 26 + rand(seed + i) * 40, h = minH + rand(seed + i * 7) * (maxH - minH);
      const x = (i / count) * width + (rand(seed * 3 + i) - 0.5) * 18;
      const lit = Array.from({ length: Math.floor(h / 11) * 4 }, (_, k) => rand(seed * 11 + i * 97 + k) < windows);
      return (
        <g key={`${seed}-${i}`}>
          <rect x={x} y={-h} width={w} height={h} fill={color} />
          {rand(seed + i * 5) > 0.7 && <rect x={x + w / 2 - 1} y={-h - 18} width={2} height={18} fill={color} />}
          {rand(seed + i * 5) > 0.7 && <rect x={x + w / 2 - 2} y={-h - 20} width={4} height={3} fill="#ff4d6d" className="fl-blink" />}
          {lit.map((on, k) => on && (
            <rect key={k} x={x + 3 + (k % 4) * (w / 4.4)} y={-h + 5 + Math.floor(k / 4) * 11} width={4} height={5}
              fill={rand(i + k + seed) > 0.86 ? "#ff6ad5" : rand(i * k + seed) > 0.45 ? "#8ff3ff" : "#ffd480"} />
          ))}
        </g>
      );
    });
  return (
    <g transform={`matrix(${ALONG_Y} ${wx} ${wy})`}>
      <rect x={0} y={-WALL_H} width={width} height={WALL_H} fill="url(#fl-sky)" />
      <g opacity={0.45} filter="url(#fl-haze)">{layer(1, 18, 120, 230, "#1d2758", 0.25)}</g>
      <g opacity={0.8}>{layer(2, 14, 90, 200, "#121b40", 0.35)}</g>
      <g>{layer(3, 9, 60, 150, "#0a1028", 0.42)}</g>
      <g filter="url(#fl-bloom)">
        <rect x={width * 0.14} y={-170} width={52} height={16} fill="none" stroke="#ff3fd8" strokeWidth={3} className="fl-neon" />
        <rect x={width * 0.47} y={-128} width={14} height={44} fill="none" stroke="#3df0ff" strokeWidth={3} className="fl-neon slow" />
        <rect x={width * 0.8} y={-110} width={44} height={12} fill="none" stroke="#ffb347" strokeWidth={3} />
      </g>
      <g className="fl-rain">{Array.from({ length: 70 }, (_, i) => (
        <line key={i} x1={rand(i * 3) * width} y1={-WALL_H + rand(i) * (WALL_H - 20)} x2={rand(i * 3) * width - 5} y2={-WALL_H + rand(i) * (WALL_H - 20) + 16} />
      ))}</g>
      {[0.12, 0.42, 0.7].map(f => <polygon key={f} points={`${width * f},${-WALL_H} ${width * f + 46},${-WALL_H} ${width * f - 60},0 ${width * f - 106},0`} fill="#ffffff" opacity={0.035} />)}
      {Array.from({ length: 6 }, (_, i) => <rect key={i} x={(i * width) / 6 - 3} y={-WALL_H} width={7} height={WALL_H} fill="#03060e" />)}
      <rect x={0} y={-WALL_H} width={width} height={10} fill="#03060e" />
      <rect x={0} y={-WALL_H / 2} width={width} height={4} fill="#03060e" />
      <rect x={0} y={-16} width={width} height={16} fill="#070c1c" />
    </g>
  );
}

function sparkPath(values: number[], x: number, y: number, w: number, h: number) {
  if (values.length < 2) return "";
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  return values.map((v, i) => `${i ? "L" : "M"}${(x + (i / (values.length - 1)) * w).toFixed(1)},${(y + h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
}

function WallScreen({ market, state, actions }: { market: Shared["market"]; state: FloorState | null; actions: FloorAction[] }) {
  const [wx, wy] = iso(0.45, 0, 0);
  const width = 8.3 * UNIT, top = -WALL_H + 30, height = 262, bow = 14;
  const usdt = state?.accounts?.USDT ?? null;
  const equity = usdt ? Number(usdt.equity) : null;
  const daily = equity != null && usdt ? equity / Number(usdt.start_of_day_equity) - 1 : null;
  const drawdown = equity != null && usdt ? equity / Number(usdt.peak_equity) - 1 : null;
  const halted = isHalted(state);
  const btc = market.series.BTC.slice(-90);
  const stageRank = ["RESEARCH", "REPLAY", "SHADOW", "PAPER", "CANARY", "LIVE"];
  const lead = [...(state?.promotion ?? [])].sort((a, b) => stageRank.indexOf(b.stage) - stageRank.indexOf(a.stage))[0];
  const chartX = 150, chartW = 196, chartY = top + 36, chartH = 104;
  // Curved glass: the top and bottom edges bow away from the viewer.
  const outline = `M0,${top} Q${width / 2},${top - bow} ${width},${top} L${width},${top + height} Q${width / 2},${top + height + bow} 0,${top + height} Z`;
  return (
    <g transform={`matrix(${ALONG_X} ${wx} ${wy})`}>
      <path d={outline} fill="#3df0ff" opacity={0.1} filter="url(#fl-glow)" />
      <path d={outline} fill="url(#fl-screen)" stroke="#2f8cff" strokeWidth={3} />
      <clipPath id="fl-screen-clip"><path d={outline} /></clipPath>
      <g clipPath="url(#fl-screen-clip)">
        <rect x={0} y={top - 10} width={width} height={30} fill="#0d2a5c" />
        <text x={12} y={top + 13} className="fl-ws-title">PROTO · {state?.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM"}</text>
        <text x={width / 2 + 40} y={top + 13} textAnchor="middle" className="fl-ws-title">
          {lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE"}
        </text>
        <text x={width - 12} y={top + 13} textAnchor="end" className={`fl-ws-title ${halted ? "fl-down" : "fl-up"}`}>{halted ? "■ HALTED" : "● ARMED"}</text>
        {SYMBOLS.map((s: Sym, i) => {
          const series = market.series[s];
          const mid = market.frames[s]?.mid ?? series[series.length - 1];
          const change = series.length > 1 && mid ? mid / series[0] - 1 : null;
          const y = top + 44 + i * 36;
          return (
            <g key={s}>
              <text x={12} y={y} className="fl-ws-sym">{s}/USD</text>
              <text x={78} y={y} className={`fl-ws-num ${change == null ? "" : change >= 0 ? "fl-up" : "fl-down"}`}>{change == null ? "" : `${change >= 0 ? "▲" : "▼"}${pct(Math.abs(change))}`}</text>
              <text x={12} y={y + 16} className="fl-ws-big">{mid ? usd(mid) : "no feed"}</text>
            </g>
          );
        })}
        <rect x={chartX} y={chartY - 12} width={chartW} height={chartH + 22} fill="#061633" stroke="#1e4f99" />
        <text x={chartX + 6} y={chartY - 1} className="fl-ws-label">BTC/USD LIVE PUBLIC</text>
        {[0.33, 0.66].map(g => <line key={g} x1={chartX} x2={chartX + chartW} y1={chartY + 6 + g * chartH} y2={chartY + 6 + g * chartH} stroke="#123870" />)}
        {btc.length > 1
          ? <g filter="url(#fl-bloom)"><path d={sparkPath(btc, chartX + 6, chartY + 8, chartW - 12, chartH - 6)} fill="none" stroke="#3dffa2" strokeWidth={2} /></g>
          : <text x={chartX + chartW / 2} y={chartY + chartH / 2 + 8} textAnchor="middle" className="fl-ws-label">awaiting live feed</text>}
        <g transform={`translate(${chartX + chartW + 12} ${top + 40})`}>
          <text className="fl-ws-label">EQUITY USDT</text>
          <text y={16} className="fl-ws-big">{equity == null ? "—" : equity.toFixed(0)}</text>
          <text y={38} className="fl-ws-label">DAY / -1%</text>
          <text y={53} className={`fl-ws-num ${daily != null && daily < -0.0075 ? "fl-down" : ""}`}>{pct(daily)}</text>
          <text y={75} className="fl-ws-label">DD / -5%</text>
          <text y={90} className={`fl-ws-num ${drawdown != null && drawdown < -0.04 ? "fl-down" : ""}`}>{pct(drawdown)}</text>
          <text y={112} className="fl-ws-label">LOCKDOWN</text>
          <text y={127} className={`fl-ws-num ${state?.controls.lockdown ? "fl-down" : ""}`}>{state?.controls.lockdown ?? "none"}</text>
        </g>
        <line x1={8} x2={width - 8} y1={top + 160} y2={top + 160} stroke="#1e4f99" />

        <text x={12} y={top + 176} className="fl-ws-label">AGENT TAPE</text>
        {actions.slice(-5).reverse().map((a, i) => (
          <text key={`${a.seq}:${a.station}`} x={12} y={top + 194 + i * 15} className="fl-ws-tape" fill={TONE_COLOR[a.tone]}>
            {`${a.at.slice(11, 19)}  ${a.station.toUpperCase().slice(0, 8).padEnd(8)}  ${a.headline.slice(0, 22)}`}
          </text>
        ))}
        {!actions.length && <text x={12} y={top + 194} className="fl-ws-tape" fill="#5d7fa8">no agent activity yet</text>}
      </g>
      <rect x={0} y={top} width={width} height={height} fill="url(#fl-scan)" clipPath="url(#fl-screen-clip)" />
    </g>
  );
}

function Floor() {
  const tiles: ReactNode[] = [];
  for (let x = 0; x < ROOM; x++) for (let y = 0; y < ROOM; y++) {
    const shade = 18 + Math.round((1 - (x + y) / (2 * ROOM)) * 10);
    tiles.push(<polygon key={`${x}-${y}`} points={pts(iso(x, y), iso(x + 1, y), iso(x + 1, y + 1), iso(x, y + 1))} fill={`rgb(${shade - 6},${shade + 4},${shade + 30})`} stroke="#1b2f5c" strokeWidth={0.8} />);
  }
  const [sx, sy] = iso(4.6, 0.9);
  return (
    <g>
      {tiles}
      <ellipse cx={sx} cy={sy} rx={UNIT * 4.4} ry={UNIT * 1.1} fill="url(#fl-cyan)" />
      {[0.12, 0.42, 0.7].map(f => {
        const a = iso(0, ROOM * (1 - f)), b = iso(0, ROOM * (1 - f) - 0.6);
        const c = iso(4.6, ROOM * (1 - f) - 0.6 + 1.8), d = iso(4.6, ROOM * (1 - f) + 1.8);
        return <polygon key={f} points={pts(a, b, c, d)} fill="url(#fl-shaft)" />;
      })}
      <polyline points={pts(iso(0, ROOM), iso(0, 0), iso(ROOM, 0))} fill="none" stroke="#2f8cff" strokeWidth={2} opacity={0.6} />
    </g>
  );
}

// ---------- people and desks ----------
function Trader({ x, y, hair, shirt, busy }: { x: number; y: number; hair: string; shirt: string; busy: boolean }) {
  const [px, py] = iso(x, y, 0);
  return (
    <g transform={`translate(${px.toFixed(1)} ${py.toFixed(1)})`} className={busy ? "fl-person busy" : "fl-person"}>
      <ellipse cx={0} cy={2} rx={20} ry={8} fill="#000" opacity={0.35} />
      <rect x={-2} y={-12} width={4} height={12} fill="#1b2236" />
      <rect x={-12} y={-20} width={24} height={9} fill="#26304d" />
      <rect x={-12} y={-46} width={24} height={26} fill="#1e2740" />
      <rect x={-10} y={-48} width={20} height={22} fill={shirt} />
      <rect x={-10} y={-48} width={20} height={4} fill="#ffffff" opacity={0.12} />
      <rect className="fl-arm" x={-14} y={-46} width={5} height={16} fill={shirt} />
      <rect className="fl-arm right" x={9} y={-46} width={5} height={16} fill={shirt} />
      <rect x={-7} y={-63} width={14} height={15} fill={hair} />
      <rect x={-7} y={-52} width={14} height={4} fill="#d9a07c" />
    </g>
  );
}

function Station({ def, action, busy, recent }: { def: StationDef; action: FloorAction | undefined; busy: boolean; recent: FloorAction[] }) {
  const tone: Tone | "idle" = action ? action.tone : "idle";
  const color = TONE_COLOR[tone];
  const { x, y } = def;
  const [lx, ly] = iso(x + 0.85, y + 0.5);
  const monitors = [0.08, 0.6, 1.12].map(o => iso(x + o, y + 0.12, 22));
  const [hx, hy] = iso(x + 0.85, y + 0.3, 150);
  const text = action?.headline ?? "waiting for work";
  return (
    <g className={`fl-station ${busy ? "busy" : ""}`}>
      <ellipse cx={lx} cy={ly} rx={UNIT * 1.3} ry={UNIT * 0.62} fill="url(#fl-amber)" className="fl-pool" />
      <Box x={x + 0.05} y={y + 0.05} w={0.1} d={0.6} h={18} top="#151d33" left="#0b1122" right="#10172a" />
      <Box x={x + 1.55} y={y + 0.05} w={0.1} d={0.6} h={18} top="#151d33" left="#0b1122" right="#10172a" />
      <Box x={x} y={y} w={1.7} d={0.7} h={4} z={18} top="#2c3a5c" left="#141c31" right="#1b2540" edge="#3f5a8f" />
      {monitors.map(([mx, my], i) => (
        <g key={i} transform={`matrix(${ALONG_X} ${mx} ${my})`}>
          <rect x={22} y={-8} width={4} height={8} fill="#0d1324" />
          <rect x={0} y={-40} width={48} height={33} fill="#0b0f1c" />
          <rect x={2} y={-38} width={44} height={29} fill="#e8952e" className="fl-monitor" style={{ animationDelay: `${(i + x) * 0.37}s` }} />
          <rect x={2} y={-38} width={44} height={6} fill="#ffd28a" opacity={0.55} />
          {Array.from({ length: 4 }, (_, k) => <rect key={k} x={5} y={-29 + k * 5} width={8 + rand(x * 13 + i * 7 + k) * 30} height={2} fill="#4a2a00" opacity={0.6} />)}
        </g>
      ))}
      <Box x={x + 0.45} y={y + 0.48} w={0.8} d={0.16} h={1} z={22} top="#3d4a6a" left="#1c2438" right="#232d47" />
      <Trader x={x + 0.85} y={y + 0.98} hair={def.hair} shirt={def.shirt} busy={busy} />
      <polygon points={pts([hx - 50, hy + 54], [hx + 50, hy + 54], [lx + 30, ly - 40], [lx - 30, ly - 40])} fill={color} opacity={busy ? 0.1 : 0.035} />
      <g transform={`translate(${(hx - 72).toFixed(1)} ${(hy - 2).toFixed(1)})`}><g className="fl-holo">
        <rect x={0} y={0} width={144} height={64} fill="#04101f" fillOpacity={0.82} />
        <g filter="url(#fl-bloom)"><rect x={0} y={0} width={144} height={64} fill="none" stroke={color} strokeWidth={busy ? 2 : 1} strokeOpacity={busy ? 1 : 0.6} /></g>
        <rect x={0} y={0} width={144} height={64} fill={color} fillOpacity={busy ? 0.14 : 0.04} />
        <text x={8} y={15} className="fl-holo-title" fill={color}>{def.label}</text>
        <text x={8} y={27} className="fl-holo-role">{def.role.toUpperCase()}{action?.symbol ? ` · ${action.symbol}` : ""}</text>
        <text x={8} y={41} className="fl-holo-text">{text.length > 25 ? `${text.slice(0, 24)}…` : text}</text>
        {recent.slice(-14).map((a, i) => (
          <rect key={`${a.seq}:${i}`} x={8 + i * 9.4} y={58 - (a.tone === "ok" ? 12 : a.tone === "working" ? 9 : a.tone === "warn" ? 7 : 14)} width={6} height={a.tone === "ok" ? 12 : a.tone === "working" ? 9 : a.tone === "warn" ? 7 : 14} fill={TONE_COLOR[a.tone]} opacity={0.85} />
        ))}
        {!recent.length && <rect x={8} y={55} width={128} height={2} fill={color} opacity={0.4} />}
      </g></g>
      <title>{`${def.label}: ${text}`}</title>
    </g>
  );
}

function Analyst() {
  const [px, py] = iso(7.9, 0.95);
  return (
    <g transform={`translate(${px} ${py})`}>
      <ellipse cx={0} cy={2} rx={16} ry={6} fill="#000" opacity={0.4} />
      <rect x={-7} y={-28} width={6} height={28} fill="#0d1426" />
      <rect x={1} y={-28} width={6} height={28} fill="#0d1426" />
      <rect x={-10} y={-58} width={20} height={32} fill="#1a2440" />
      <rect x={-7} y={-74} width={14} height={16} fill="#111" />
      <g filter="url(#fl-bloom)"><rect x={8} y={-52} width={12} height={15} fill="#7ff8ff" opacity={0.9} /></g>
    </g>
  );
}

function Beam({ from, to, busy, color }: { from: StationDef; to: StationDef; busy: boolean; color: string }) {
  const [x1, y1] = iso(from.x + 0.85, from.y + 1.0), [x2, y2] = iso(to.x + 0.85, to.y + 1.0);
  return <line x1={x1} y1={y1} x2={x2} y2={y2} className={`fl-beam ${busy ? "busy" : ""}`} stroke={color} filter={busy ? "url(#fl-bloom)" : undefined} />;
}

export function Scene({ market, state, actions, latest, active }: {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  active: (s: StationId) => boolean;
}) {
  const summary = STATIONS.map(s => `${s.label}: ${latest[s.id]?.headline ?? "idle"}`).join("; ");
  const order = [...STATIONS].sort((a, b) => a.x + a.y - (b.x + b.y));
  return (
    <svg className="fl-scene" viewBox="40 0 930 820" role="img" aria-label={`Trading floor. ${summary}`} shapeRendering="crispEdges">
      <Defs />
      <rect x={20} width={960} height={820} fill="#04060d" />
      <City />
      <polygon points={pts(iso(0, 0), iso(ROOM, 0), iso(ROOM, 0, WALL_H), iso(0, 0, WALL_H))} fill="url(#fl-wall)" />
      {Array.from({ length: 8 }, (_, i) => <polyline key={i} points={pts(iso(i + 1, 0, 0), iso(i + 1, 0, WALL_H))} stroke="#0b1430" strokeWidth={2} />)}
      <WallScreen market={market} state={state} actions={actions} />
      <Floor />
      {STATIONS.slice(1).map((s, i) => (
        <Beam key={s.id} from={STATIONS[i]} to={s} busy={active(s.id)} color={TONE_COLOR[latest[s.id]?.tone ?? "idle"]} />
      ))}
      <Analyst />
      {order.map(s => (
        <Station key={s.id} def={s} action={latest[s.id]} busy={active(s.id)} recent={actions.filter(a => a.station === s.id)} />
      ))}
      <polygon points={pts(iso(0, 0, WALL_H), iso(ROOM, 0, WALL_H), iso(ROOM, 0, WALL_H - 14), iso(0, 0, WALL_H - 14))} fill="#02040a" />
      <rect x={20} width={960} height={820} fill="url(#fl-vignette)" pointerEvents="none" />
    </svg>
  );
}
