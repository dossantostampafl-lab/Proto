// Event-driven cinematic trading room, rendered as live SVG elements. Operational data comes from
// /v1/floor/state, /ws/floor and the live public market stream; everything else is decoration.
import type { ReactNode } from "react";
import { type Sym, SYMBOLS } from "../api";
import type { Shared } from "../main";
import { pct, usd } from "../ui";

export type Tone = "ok" | "warn" | "alert" | "working";
export type StationId = "tradingview" | "fusion" | "reviewer" | "risk" | "oms" | "reconciliation";
export type FloorAction = { seq: number; at: string; event_type: string; station: StationId; tone: Tone; headline: string; symbol: string | null };
export type Account = { equity: string; start_of_day_equity: string; peak_equity: string; gross_exposure: string; positions: Record<string, string> } | null;
export const STAGES = ["RESEARCH", "REPLAY", "SHADOW", "PAPER", "CANARY", "LIVE"] as const;

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

export const TONE_COLOR: Record<Tone | "idle", string> = { ok: "#58c39a", warn: "#d8a55b", alert: "#ea666b", working: "#adb5c1", idle: "#7a838e" };

export type StationDef = { id: StationId; label: string; role: string; x: number; y: number; hair: string; shirt: string };
export const STATIONS: StationDef[] = [
  { id: "tradingview", label: "TRADINGVIEW", role: "alerts in", x: 1.3, y: 2.4, hair: "#2b1a12", shirt: "#343b43" },
  { id: "fusion", label: "FUSION", role: "signal vote", x: 3.8, y: 2.4, hair: "#c99a52", shirt: "#343b43" },
  { id: "reviewer", label: "AI REVIEWER", role: "shrink only", x: 6.3, y: 2.4, hair: "#151515", shirt: "#343b43" },
  { id: "risk", label: "RISK ENGINE", role: "permits", x: 6.3, y: 5.6, hair: "#5a2e14", shirt: "#343b43" },
  { id: "oms", label: "OMS / EXECUTOR", role: "orders", x: 3.8, y: 5.6, hair: "#222", shirt: "#343b43" },
  { id: "reconciliation", label: "RECONCILIATION", role: "ledger = venue", x: 1.3, y: 5.6, hair: "#7a4f26", shirt: "#343b43" },
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
        <feGaussianBlur stdDeviation="1.7" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="fl-glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="9" /></filter>
      <filter id="fl-haze" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="1.1" /></filter>
      <linearGradient id="fl-sky" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#0b0e13" /><stop offset="0.62" stopColor="#191d24" /><stop offset="1" stopColor="#30313a" />
      </linearGradient>
      <linearGradient id="fl-wall" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#11161b" /><stop offset="1" stopColor="#292d34" />
      </linearGradient>
      <linearGradient id="fl-screen" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#182029" /><stop offset="0.46" stopColor="#0b1117" /><stop offset="1" stopColor="#060b10" />
      </linearGradient>
      <linearGradient id="fl-metal" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0" stopColor="#5c646d" /><stop offset="0.48" stopColor="#323941" /><stop offset="1" stopColor="#14191f" />
      </linearGradient>
      <linearGradient id="fl-glass" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0" stopColor="#d4d7db" stopOpacity="0.09" />
        <stop offset="0.36" stopColor="#eef0f2" stopOpacity="0.01" />
        <stop offset="1" stopColor="#b7bec8" stopOpacity="0.13" />
      </linearGradient>
      <linearGradient id="fl-desk" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#49515a" /><stop offset="1" stopColor="#252b33" />
      </linearGradient>
      <radialGradient id="fl-amber" cx="0.5" cy="0.5" r="0.5">
        <stop offset="0" stopColor="#d9b78c" stopOpacity="0.15" /><stop offset="1" stopColor="#d9b78c" stopOpacity="0" />
      </radialGradient>
      <radialGradient id="fl-cyan" cx="0.5" cy="0.5" r="0.5">
        <stop offset="0" stopColor="#b4bbc5" stopOpacity="0.14" /><stop offset="1" stopColor="#b4bbc5" stopOpacity="0" />
      </radialGradient>
      <linearGradient id="fl-shaft" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0" stopColor="#dde0e2" stopOpacity="0.065" /><stop offset="1" stopColor="#dde0e2" stopOpacity="0" />
      </linearGradient>
      <radialGradient id="fl-vignette" cx="0.5" cy="0.45" r="0.75">
        <stop offset="0.50" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity="0.74" />
      </radialGradient>
      <pattern id="fl-scan" width="5" height="5" patternUnits="userSpaceOnUse">
        <rect width="5" height="1" fill="#cfd6dc" opacity="0.018" />
      </pattern>
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
              fill={rand(i + k + seed) > 0.86 ? "#bca98e" : rand(i * k + seed) > 0.45 ? "#c2c8cc" : "#dec097"} />
          ))}
        </g>
      );
    });
  return (
    <g transform={`matrix(${ALONG_Y} ${wx} ${wy})`}>
      <rect x={0} y={-WALL_H} width={width} height={WALL_H} fill="url(#fl-sky)" />
      <g opacity={0.45} filter="url(#fl-haze)">{layer(1, 18, 120, 230, "#272c34", 0.25)}</g>
      <g opacity={0.8}>{layer(2, 14, 90, 200, "#1c232b", 0.35)}</g>
      <g>{layer(3, 9, 60, 150, "#101720", 0.42)}</g>
      <g filter="url(#fl-bloom)">
        <rect x={width * 0.14} y={-170} width={52} height={16} fill="none" stroke="#847e75" strokeWidth={3} className="fl-neon" />
        <rect x={width * 0.47} y={-128} width={14} height={44} fill="none" stroke="#aeb8c1" strokeWidth={3} className="fl-neon slow" />
        <rect x={width * 0.8} y={-110} width={44} height={12} fill="none" stroke="#c9ad84" strokeWidth={3} />
      </g>
      <g className="fl-rain">{Array.from({ length: 70 }, (_, i) => (
        <line key={i} x1={rand(i * 3) * width} y1={-WALL_H + rand(i) * (WALL_H - 20)} x2={rand(i * 3) * width - 5} y2={-WALL_H + rand(i) * (WALL_H - 20) + 16} />
      ))}</g>
      {[0.12, 0.42, 0.7].map(f => <polygon key={f} points={`${width * f},${-WALL_H} ${width * f + 46},${-WALL_H} ${width * f - 60},0 ${width * f - 106},0`} fill="#ffffff" opacity={0.035} />)}
      {Array.from({ length: 6 }, (_, i) => <rect key={i} x={(i * width) / 6 - 3} y={-WALL_H} width={7} height={WALL_H} fill="#12161b" />)}
      <rect x={0} y={-WALL_H} width={width} height={10} fill="#12161b" />
      <rect x={0} y={-WALL_H / 2} width={width} height={4} fill="#12161b" />
      <rect x={0} y={-16} width={width} height={16} fill="#1a1e24" />
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
  const daily = equity != null && usdt && Number(usdt.start_of_day_equity) > 0 ? equity / Number(usdt.start_of_day_equity) - 1 : null;
  const drawdown = equity != null && usdt && Number(usdt.peak_equity) > 0 ? equity / Number(usdt.peak_equity) - 1 : null;
  const halted = isHalted(state);
  const btc = market.series.BTC.slice(-90);
  const rank = (stage: string) => STAGES.indexOf(stage as (typeof STAGES)[number]);
  const lead = [...(state?.promotion ?? [])].sort((a, b) => rank(b.stage) - rank(a.stage))[0];
  const chartX = 150, chartW = 196, chartY = top + 36, chartH = 104;
  // Curved glass: the top and bottom edges bow away from the viewer.
  const outline = `M0,${top} Q${width / 2},${top - bow} ${width},${top} L${width},${top + height} Q${width / 2},${top + height + bow} 0,${top + height} Z`;
  return (
    <g transform={`matrix(${ALONG_X} ${wx} ${wy})`}>
      <path d={outline} fill="#aeb8c1" opacity={0.1} filter="url(#fl-glow)" />
      <path d={outline} fill="url(#fl-screen)" stroke="#74818d" strokeWidth={3} />
      <clipPath id="fl-screen-clip"><path d={outline} /></clipPath>
      <g clipPath="url(#fl-screen-clip)">
        <rect x={0} y={top - 10} width={width} height={30} fill="#242d37" />
        <text x={12} y={top + 13} className="fl-ws-title">PROTO · {state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM"}</text>
        <text x={width / 2 + 40} y={top + 13} textAnchor="middle" className="fl-ws-title">
          {state == null ? "" : lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE"}
        </text>
        <text x={width - 12} y={top + 13} textAnchor="end" className={`fl-ws-title ${state == null ? "" : halted ? "fl-down" : "fl-up"}`}>{state == null ? "STATUS UNAVAILABLE" : halted ? "■ HALTED" : "● ARMED"}</text>
        {SYMBOLS.map((s: Sym, i) => {
          const series = market.series[s];
          const mid = market.frames[s]?.mid ?? series[series.length - 1];
          const change = series.length > 1 && mid ? mid / series[0] - 1 : null;
          const y = top + 44 + i * 36;
          return (
            <g key={s}>
              <text x={12} y={y} className="fl-ws-sym">{s}/USD</text>
              <text x={78} y={y} className={`fl-ws-num ${change == null ? "" : change >= 0 ? "fl-up" : "fl-down"}`}>{change == null ? "" : `${change >= 0 ? "▲" : "▼"}${pct(Math.abs(change))}`}</text>
              <text x={12} y={y + 16} className="fl-ws-big">{mid != null && Number.isFinite(mid) && mid > 0 ? usd(mid) : "no feed"}</text>
            </g>
          );
        })}
        <rect x={chartX} y={chartY - 12} width={chartW} height={chartH + 22} fill="#0c141b" stroke="#354451" />
        <text x={chartX + 6} y={chartY - 1} className="fl-ws-label">BTC/USD {market.connected ? "LIVE PUBLIC" : "LAST KNOWN / REST"}</text>
        {[0.33, 0.66].map(g => <line key={g} x1={chartX} x2={chartX + chartW} y1={chartY + 6 + g * chartH} y2={chartY + 6 + g * chartH} stroke="#26343e" />)}
        {btc.length > 1
          ? <g filter="url(#fl-bloom)"><path d={sparkPath(btc, chartX + 6, chartY + 8, chartW - 12, chartH - 6)} fill="none" stroke="#3dffa2" strokeWidth={2} /></g>
          : <text x={chartX + chartW / 2} y={chartY + chartH / 2 + 8} textAnchor="middle" className="fl-ws-label">awaiting live feed</text>}
        <g transform={`translate(${chartX + chartW + 12} ${top + 40})`}>
          <text className="fl-ws-label">EQUITY USDT</text>
          <text y={16} className="fl-ws-big">{equity == null || !Number.isFinite(equity) ? "—" : equity.toFixed(0)}</text>
          <text y={38} className="fl-ws-label">DAY / -1%</text>
          <text y={53} className={`fl-ws-num ${daily != null && daily < -0.0075 ? "fl-down" : ""}`}>{pct(daily)}</text>
          <text y={75} className="fl-ws-label">DD / -5%</text>
          <text y={90} className={`fl-ws-num ${drawdown != null && drawdown < -0.04 ? "fl-down" : ""}`}>{pct(drawdown)}</text>
          <text y={112} className="fl-ws-label">LOCKDOWN</text>
          <text y={127} className={`fl-ws-num ${state?.controls.lockdown ? "fl-down" : ""}`}>{state?.controls.lockdown ?? "none"}</text>
        </g>
        <line x1={8} x2={width - 8} y1={top + 160} y2={top + 160} stroke="#354451" />
        <text x={12} y={top + 176} className="fl-ws-label">AGENT TAPE</text>
        {actions.slice(-5).reverse().map((a, i) => (
          <text key={`${a.seq}:${a.station}`} x={12} y={top + 194 + i * 15} className="fl-ws-tape" fill={TONE_COLOR[a.tone]}>
            {`${a.at.slice(11, 19)}  ${a.station.toUpperCase().slice(0, 8).padEnd(8)}  ${a.headline.slice(0, 22)}`}
          </text>
        ))}
        {!actions.length && <text x={12} y={top + 194} className="fl-ws-tape" fill="#838b94">no agent activity yet</text>}
      </g>
      <rect x={0} y={top} width={width} height={height} fill="url(#fl-scan)" clipPath="url(#fl-screen-clip)" />
    </g>
  );
}


function Floor() {
  const tiles: ReactNode[] = [];
  for (let x = 0; x < ROOM; x++) for (let y = 0; y < ROOM; y++) {
    const shade = 30 + Math.round((1 - (x + y) / (2 * ROOM)) * 13);
    tiles.push(
      <polygon key={x + "-" + y} points={pts(iso(x, y), iso(x + 1, y), iso(x + 1, y + 1), iso(x, y + 1))}
        fill={"rgb(" + shade + "," + (shade + 3) + "," + (shade + 8) + ")"}
        stroke="#474b51" strokeOpacity={0.28} strokeWidth={0.8} />
    );
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
      <polyline points={pts(iso(0, ROOM), iso(0, 0), iso(ROOM, 0))}
        fill="none" stroke="#77818b" strokeWidth={1.4} opacity={0.45} />
    </g>
  );
}

// ---------- people and desks ----------

/** Operator is visual context for a real station. Only verified activity moves its hands. */
function AgentOperator({ x, y, hair, shirt, busy }: { x: number; y: number; hair: string; shirt: string; busy: boolean }) {
  const [px, py] = iso(x, y, 0);
  return (
    <g transform={"translate(" + px.toFixed(1) + " " + py.toFixed(1) + ")"}
      data-activity={busy ? "working" : "idle"} className={busy ? "fl-person busy" : "fl-person"}>
      <ellipse cx={0} cy={3} rx={27} ry={11} fill="#000" opacity={0.4} />
      <path d="M-17 -8 L-18 -34 Q-16 -43 -10 -42 L10 -42 Q16 -43 18 -34 L17 -8"
        fill="#22272e" stroke="#69727b" strokeWidth={1.2} />
      <path d="M-13 -4 L-18 0 M13 -4 L18 0" fill="none" stroke="#69727b" strokeWidth={3} />
      <path d="M-10 -19 L-13 0 M10 -19 L13 0" fill="none" stroke="#20262d" strokeWidth={7} strokeLinecap="round" />
      <path className="fl-operator-breathe"
        d="M-13 -60 Q-18 -56 -19 -42 L-15 -24 Q0 -17 15 -24 L19 -42 Q18 -56 13 -60 Q0 -65 -13 -60Z"
        fill={shirt} stroke="#737c84" strokeWidth={1.3} />
      <path d="M-12 -52 Q0 -45 12 -52" fill="none" stroke="#a4aeb7" strokeOpacity={0.2} strokeWidth={2} />
      <path className="fl-arm" d="M-14 -54 Q-22 -46 -17 -35 L-7 -30"
        fill="none" stroke="#49515a" strokeWidth={7} strokeLinecap="round" />
      <path className="fl-arm right" d="M14 -54 Q22 -46 17 -35 L7 -30"
        fill="none" stroke="#49515a" strokeWidth={7} strokeLinecap="round" />
      <ellipse cx={0} cy={-69} rx={10.5} ry={12.5} fill="#c1967c" stroke="#e0b69a" strokeOpacity={0.4} />
      <path d="M-11 -72 Q-12 -86 0 -86 Q12 -85 11 -70 L7 -78 Q0 -75 -10 -72Z" fill={hair} />
      <path d="M-12 -73 Q-13 -78 -10 -82 M12 -73 Q13 -78 10 -82" fill="none"
        stroke="#a8afb8" strokeOpacity={0.28} strokeWidth={1.5} />
      <path d="M-12 -42 L-18 -16 L-24 -15 M12 -42 L18 -16 L24 -15" fill="none"
        stroke="#151a20" strokeWidth={2} opacity={0.65} />
    </g>
  );
}


function Station({ def, action, busy, recent, market }: {
  def: StationDef;
  action: FloorAction | undefined;
  busy: boolean;
  recent: FloorAction[];
  market: Shared["market"];
}) {
  const tone: Tone | "idle" = action ? action.tone : "idle";
  const color = TONE_COLOR[tone];
  const { x, y } = def;
  const [lx, ly] = iso(x + 0.85, y + 0.5);
  const monitors = [0.08, 0.6, 1.12].map(o => iso(x + o, y + 0.12, 22));
  const [hx, hy] = iso(x + 0.85, y + 0.3, 150);
  const headline = action?.headline ?? "waiting for work";
  return (
    <g className={busy ? "fl-station busy" : "fl-station"} data-station={def.id}>
      <ellipse cx={lx} cy={ly} rx={UNIT * 1.5} ry={UNIT * 0.66}
        fill="url(#fl-amber)" className="fl-pool" opacity={busy ? 0.95 : 0.4} />
      <ellipse cx={lx} cy={ly + 19} rx={UNIT * 1.12} ry={18} fill="#000" opacity={0.27} />
      <Box x={x + 0.08} y={y + 0.08} w={0.12} d={0.65} h={18}
        top="#485059" left="#171c23" right="#252b32" />
      <Box x={x + 1.52} y={y + 0.08} w={0.12} d={0.65} h={18}
        top="#485059" left="#171c23" right="#252b32" />
      <Box x={x} y={y} w={1.72} d={0.82} h={4} z={18}
        top="#50565f" left="#171c22" right="#2c3139" edge="#868e95" />
      <Box x={x + 0.04} y={y + 0.07} w={1.64} d={0.68} h={1} z={22}
        top="#292f35" left="#1b222a" right="#272f36" edge="#626c76" />
      {monitors.map(([mx, my], i) => {
        const symbol = SYMBOLS[i];
        const series = market.series[symbol];
        const mid = market.frames[symbol]?.mid ?? series[series.length - 1];
        return (
          <g key={symbol} transform={"matrix(" + ALONG_X + " " + mx + " " + my + ")"}>
            <path d="M23 -8 L23 1 M10 1 L36 1" fill="none" stroke="#737e89" strokeWidth={3} />
            <rect x={-1} y={-42} width={50} height={36} rx={3} fill="#080d12" stroke="#78838c" strokeWidth={1.6} />
            <rect x={2} y={-39} width={44} height={29} rx={1} fill="url(#fl-screen)" className="fl-monitor-screen" />
            <path d="M2 -39 L46 -39 L46 -10" fill="none" stroke="#c4ccd2" strokeOpacity={0.20} />
            <text x={5} y={-30} className="fl-monitor-symbol">{symbol}/USD</text>
            <text x={5} y={-18} className="fl-monitor-number">
              {mid != null && Number.isFinite(mid) && mid > 0 ? usd(mid) : "no feed"}
            </text>
            <rect x={5} y={-14} width={38} height={1} fill={busy ? color : "#89939c"} opacity={busy ? 1 : 0.35} />
          </g>
        );
      })}
      <Box x={x + 0.40} y={y + 0.53} w={0.90} d={0.15} h={1} z={22}
        top="#5c6168" left="#262d34" right="#30373e" />
      <AgentOperator x={x + 0.85} y={y + 1.01} hair={def.hair} shirt={def.shirt} busy={busy} />
      <polygon points={pts([hx - 50, hy + 54], [hx + 50, hy + 54], [lx + 30, ly - 40], [lx - 30, ly - 40])}
        fill={color} opacity={busy ? 0.065 : 0.018} />
      <g transform={"translate(" + (hx - 72).toFixed(1) + " " + (hy - 2).toFixed(1) + ")"}>
        <g className="fl-holo">
          <rect x={0} y={0} width={144} height={64} rx={5}
            fill="#10161c" fillOpacity={0.96} stroke={busy ? color : "#555e67"}
            strokeWidth={busy ? 1.6 : 0.9} />
          <rect x={1} y={1} width={142} height={17} rx={4} fill="#252d34" />
          <circle cx={134} cy={9} r={3} fill={busy ? color : "#4d555d"} />
          <text x={8} y={15} className="fl-holo-title" fill="#dce0e4">{def.label}</text>
          <text x={8} y={29} className="fl-holo-role">{def.role.toUpperCase()}{action?.symbol ? " · " + action.symbol : ""}</text>
          <text x={8} y={43} className="fl-holo-text">{headline.length > 25 ? headline.slice(0, 24) + "…" : headline}</text>
          {recent.slice(-14).map((a, i) => {
            const h = a.tone === "ok" ? 12 : a.tone === "working" ? 9 : a.tone === "warn" ? 7 : 14;
            return <rect key={a.seq + ":" + i} x={8 + i * 9.4} y={59 - h} width={6} height={h}
              fill={TONE_COLOR[a.tone]} opacity={0.85} />;
          })}
          {!recent.length && <line x1={8} x2={136} y1={57} y2={57} stroke="#626d77" strokeWidth={1} />}
        </g>
      </g>
      <title>{def.label + ": " + headline}</title>
    </g>
  );
}


function Analyst() {
  const [px, py] = iso(7.9, 0.95);
  return (
    <g transform={"translate(" + px + " " + py + ")"} opacity={0.85}>
      <ellipse cx={0} cy={2} rx={19} ry={7} fill="#000" opacity={0.38} />
      <path d="M-7 -28 L-10 0 M7 -28 L10 0" fill="none" stroke="#262b30" strokeWidth={7} />
      <path d="M-14 -50 Q0 -58 14 -50 L12 -28 L-12 -28Z" fill="url(#fl-metal)" stroke="#81878d" />
      <ellipse cx={0} cy={-60} rx={10} ry={12} fill="#b58c70" />
      <path d="M-10 -63 Q-12 -78 0 -78 Q13 -77 10 -63 L6 -71 L-7 -70Z" fill="#202126" />
      <rect x={12} y={-57} width={12} height={17} rx={3} fill="#273039" stroke="#9ca5ac" />
      <rect x={14} y={-54} width={8} height={12} rx={1} fill="#969fa7" opacity={0.6} />
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
    <svg className="fl-scene" viewBox="40 0 930 820" role="img" aria-label={`Trading floor. ${summary}`} >
      <Defs />
      <rect x={20} width={960} height={820} fill="#0b0d10" />
      <City />
      <polygon points={pts(iso(0, 0), iso(ROOM, 0), iso(ROOM, 0, WALL_H), iso(0, 0, WALL_H))} fill="url(#fl-wall)" />
      {Array.from({ length: 8 }, (_, i) => <polyline key={i} points={pts(iso(i + 1, 0, 0), iso(i + 1, 0, WALL_H))} stroke="#454b52" strokeWidth={1.5} />)}
      <WallScreen market={market} state={state} actions={actions} />
      <Floor />
      {STATIONS.slice(1).map((s, i) => (
        <Beam key={s.id} from={STATIONS[i]} to={s} busy={active(s.id)} color={TONE_COLOR[latest[s.id]?.tone ?? "idle"]} />
      ))}
      <Analyst />
      {order.map(s => (
        <Station key={s.id} def={s} action={latest[s.id]} busy={active(s.id)} recent={actions.filter(a => a.station === s.id)} market={market} />
      ))}
      <polygon points={pts(iso(0, 0, WALL_H), iso(ROOM, 0, WALL_H), iso(ROOM, 0, WALL_H - 14), iso(0, 0, WALL_H - 14))} fill="#090c10" />
      <rect x={20} width={960} height={820} fill="url(#fl-vignette)" pointerEvents="none" />
    </svg>
  );
}
