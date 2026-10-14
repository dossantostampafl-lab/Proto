// Vector fallback of the trading room, drawn when the room photo cannot be loaded.
// Every number on a screen comes from /v1/floor/state, /ws/floor or the live public market stream;
// the room, the people and the plants are decoration.
import type { ReactNode } from "react";
import { type Sym, SYMBOLS } from "../api";
import type { Shared } from "../main";
import { pct, usd } from "../ui";

import { changeOf, type FloorAction, type FloorState, isHalted, priceOf, sparkPath, STAGES, type StationId, type Tone, TONE_COLOR } from "./floorModel";

// ---------- camera: one-point perspective, high behind the central desk ----------
const VW = 1280, VH = 720;
const F = 780; // focal length in px per metre at 1 m
const VX = 640, HY = 118; // vanishing point
const CAM_Y = 4.6; // camera height in metres
const ROOM_X = 7.4, ROOM_H = 6.0, BACK_Z = 13.4, NEAR_Z = 2.4;
type P = [number, number];
const proj = (x: number, y: number, z: number): P => [VX + (x * F) / z, HY + ((CAM_Y - y) * F) / z];
const scale = (z: number) => F / z;
const pts = (...p: P[]) => p.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
const flat = (x0: number, x1: number, z0: number, z1: number, y: number) => pts(proj(x0, y, z0), proj(x1, y, z0), proj(x1, y, z1), proj(x0, y, z1));
/** A billboard: local drawing in centimetres, origin at world (x, y, z), y up. */
const at = (x: number, y: number, z: number, extra = 1) => {
  const [px, py] = proj(x, y, z);
  return `translate(${px.toFixed(1)} ${py.toFixed(1)}) scale(${((scale(z) / 100) * extra).toFixed(4)})`;
};
/** Fits a w x h local drawing onto a world quad (top-left, top-right, bottom-left, bottom-right). */
const fit = (tl: P, tr: P, bl: P, br: P, w: number, h: number) => {
  const left: P = [bl[0] - tl[0], bl[1] - tl[1]], right: P = [br[0] - tr[0], br[1] - tr[1]];
  const down: P = [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2];
  const f = (n: number) => n.toFixed(4);
  return `matrix(${f((tr[0] - tl[0]) / w)} ${f((tr[1] - tl[1]) / w)} ${f(down[0] / h)} ${f(down[1] / h)} ${f(tl[0])} ${f(tl[1])})`;
};
const rand = (n: number) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

type StationDef = {
  id: StationId; label: string; role: string;
  x: number; z: number; yaw: number; w: number; hub?: boolean;
  hair: string; suit: string;
};
// Pipeline order, left to right around the room; the AI reviewer sits at the central hub.
const STATIONS: StationDef[] = [
  { id: "tradingview", label: "TRADINGVIEW", role: "Mercado e dados", x: -4.7, z: 8.0, yaw: 0.42, w: 1.15, hair: "#1d1612", suit: "#1f252c" },
  { id: "fusion", label: "FUSION", role: "Sinais e estratégias", x: -2.1, z: 9.8, yaw: 0.16, w: 1.05, hair: "#151515", suit: "#232930" },
  { id: "reviewer", label: "AI REVIEWER", role: "Análise e validação", x: 0, z: 6.5, yaw: 0, w: 1.45, hub: true, hair: "#121212", suit: "#1b2026" },
  { id: "risk", label: "RISK ENGINE", role: "Risco e controle", x: 2.1, z: 9.8, yaw: -0.16, w: 1.05, hair: "#2a1d14", suit: "#20262d" },
  { id: "oms", label: "OMS / EXECUTOR", role: "Ordens e execução", x: 4.7, z: 8.0, yaw: -0.42, w: 1.15, hair: "#3b2416", suit: "#262a31" },
  { id: "reconciliation", label: "RECONCILIATION", role: "Balanço e auditoria", x: 0, z: 11.1, yaw: 0, w: 0.9, hair: "#5c3a1e", suit: "#22282e" },
];

const DESK_H = 0.76, DESK_D = 0.9;
/** Desk-local metres (lx right, lz towards the wall) to world. */
const local = (d: StationDef, lx: number, y: number, lz: number): [number, number, number] => [
  d.x + lx * Math.cos(d.yaw) + lz * Math.sin(d.yaw), y, d.z - lx * Math.sin(d.yaw) + lz * Math.cos(d.yaw),
];
const pd = (d: StationDef, lx: number, y: number, lz: number) => proj(...local(d, lx, y, lz));

function Defs() {
  return (
    <defs>
      <filter id="fl-bloom" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="1.6" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="fl-led" x="-10%" y="-200%" width="120%" height="500%">
        <feGaussianBlur stdDeviation="2" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="fl-glow" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="16" /></filter>
      <filter id="fl-soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="7" /></filter>
      <filter id="fl-dof" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="18" /></filter>
      <filter id="fl-haze" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="1.1" /></filter>
      <linearGradient id="fl-sky" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#060708" /><stop offset="0.6" stopColor="#141619" /><stop offset="1" stopColor="#2a2b2d" />
      </linearGradient>
      <linearGradient id="fl-ceiling" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#030405" /><stop offset="1" stopColor="#15181c" />
      </linearGradient>
      <linearGradient id="fl-floor" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#1b1e22" /><stop offset="0.45" stopColor="#101215" /><stop offset="1" stopColor="#060708" />
      </linearGradient>
      <linearGradient id="fl-glass" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0" stopColor="#b4b9be" stopOpacity="0.09" /><stop offset="0.5" stopColor="#e2e4e6" stopOpacity="0.02" /><stop offset="1" stopColor="#a4a9ae" stopOpacity="0.11" />
      </linearGradient>
      <linearGradient id="fl-screen" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#17191c" /><stop offset="0.5" stopColor="#0e1012" /><stop offset="1" stopColor="#08090a" />
      </linearGradient>
      <linearGradient id="fl-desk" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#24292f" /><stop offset="1" stopColor="#3a4048" />
      </linearGradient>
      <linearGradient id="fl-panel" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#2a2f35" /><stop offset="1" stopColor="#15181c" />
      </linearGradient>
      <linearGradient id="fl-cone" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#f1e3c8" stopOpacity="0.09" /><stop offset="1" stopColor="#f1e3c8" stopOpacity="0" />
      </linearGradient>
      <linearGradient id="fl-reflect" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#bfc4c9" stopOpacity="0.13" /><stop offset="1" stopColor="#bfc4c9" stopOpacity="0" />
      </linearGradient>
      <linearGradient id="fl-chair" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#1e2227" /><stop offset="1" stopColor="#0c0e10" />
      </linearGradient>
      <radialGradient id="fl-spot" cx="0.5" cy="0.5" r="0.5">
        <stop offset="0" stopColor="#f0dfc0" stopOpacity="0.20" /><stop offset="1" stopColor="#f0dfc0" stopOpacity="0" />
      </radialGradient>
      <radialGradient id="fl-cool" cx="0.5" cy="0.5" r="0.5">
        <stop offset="0" stopColor="#cdd2d6" stopOpacity="0.16" /><stop offset="1" stopColor="#cdd2d6" stopOpacity="0" />
      </radialGradient>
      <radialGradient id="fl-vignette" cx="0.5" cy="0.45" r="0.78">
        <stop offset="0.55" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity="0.75" />
      </radialGradient>
      <pattern id="fl-scan" width="4" height="4" patternUnits="userSpaceOnUse">
        <rect width="4" height="1" fill="#cfd6dc" opacity="0.02" />
      </pattern>
    </defs>
  );
}

// ---------- the room ----------

/** Distant skyline seen through the glass; far away, so drawn flat. */
function City() {
  const base = HY + 150;
  const layer = (seed: number, count: number, minH: number, maxH: number, color: string, windows: number) =>
    Array.from({ length: count }, (_, i) => {
      const w = 20 + rand(seed + i) * 44, h = minH + rand(seed + i * 7) * (maxH - minH);
      const x = (i / count) * VW + (rand(seed * 3 + i) - 0.5) * 30;
      const lit = Array.from({ length: Math.floor(h / 9) * 4 }, (_, k) => rand(seed * 11 + i * 97 + k) < windows);
      return (
        <g key={`${seed}-${i}`}>
          <rect x={x} y={base - h} width={w} height={h} fill={color} />
          {rand(seed + i * 5) > 0.8 && <rect x={x + w / 2 - 1.5} y={base - h - 20} width={3} height={20} fill={color} />}
          {rand(seed + i * 5) > 0.8 && <rect x={x + w / 2 - 2} y={base - h - 22} width={4} height={3} fill="#7d8287" />}
          {lit.map((on, k) => on && (
            <rect key={k} x={x + 3 + (k % 4) * (w / 4.4)} y={base - h + 5 + Math.floor(k / 4) * 9} width={3.2} height={4}
              fill={rand(i + k + seed) > 0.55 ? "#d6cfc1" : "#c3c8cc"} opacity={0.62} />
          ))}
        </g>
      );
    });
  return (
    <g>
      <rect width={VW} height={VH} fill="url(#fl-sky)" />
      <g opacity={0.5} filter="url(#fl-haze)">{layer(1, 40, 120, 300, "#232a33", 0.25)}</g>
      <g opacity={0.85}>{layer(2, 30, 80, 230, "#171e27", 0.34)}</g>
      <g>{layer(3, 22, 50, 160, "#0e141b", 0.4)}</g>
      <rect y={base} width={VW} height={VH - base} fill="#0c0f13" />
      {Array.from({ length: 60 }, (_, i) => <circle key={i} cx={rand(i * 9) * VW} cy={base + 4 + rand(i * 4) * 40} r={1.2} fill="#d6cfc1" opacity={0.35} />)}
    </g>
  );
}

const screenZ = (x: number) => 12.8 - 0.04 * x * x; // the wall of screens bows towards the room

function Room() {
  const glassWall = (sx: number) => (
    <g>
      <polygon points={pts(proj(sx, 0, NEAR_Z), proj(sx, 0, BACK_Z), proj(sx, ROOM_H, BACK_Z), proj(sx, ROOM_H, NEAR_Z))} fill="url(#fl-glass)" />
      {[8.2, 9.4, 10.6, 11.8].map(z => <polyline key={z} points={pts(proj(sx, 0, z), proj(sx, ROOM_H, z))} stroke="#0a0c0f" strokeWidth={6} />)}
      {[0.3, 3.2].map(y => <polyline key={y} points={pts(proj(sx, y, NEAR_Z), proj(sx, y, BACK_Z))} stroke="#0a0c0f" strokeWidth={4} />)}
      <polygon points={pts(proj(sx, 0, NEAR_Z), proj(sx, 0, BACK_Z), proj(sx, 0.3, BACK_Z), proj(sx, 0.3, NEAR_Z))} fill="#0d1013" />
    </g>
  );
  const [bl] = proj(-ROOM_X, 0, BACK_Z), [br] = proj(ROOM_X, 0, BACK_Z);
  const [, btop] = proj(0, ROOM_H, BACK_Z), [, bfloor] = proj(0, 0, BACK_Z);
  const [sl] = proj(-6.0, 0, BACK_Z), [sr] = proj(6.0, 0, BACK_Z);
  const lights: ReactNode[] = [];
  for (const z of [4.5, 7.5, 10.5]) for (const x of [-5, -2.5, 0, 2.5, 5]) {
    const [cx, cy] = proj(x, ROOM_H - 0.01, z), s = scale(z);
    lights.push(<ellipse key={`${x}:${z}`} cx={cx} cy={cy} rx={s * 0.12} ry={s * 0.035} fill="#fff6e6" opacity={0.85} filter="url(#fl-bloom)" />);
    const [fx, fy] = proj(x, 0, z);
    lights.push(<ellipse key={`f${x}:${z}`} cx={fx} cy={fy} rx={s * 1.1} ry={s * 0.32} fill="url(#fl-spot)" />);
  }
  return (
    <g>
      {glassWall(-ROOM_X)}
      {glassWall(ROOM_X)}
      <rect x={bl} y={btop} width={br - bl} height={bfloor - btop} fill="url(#fl-glass)" />
      {[-6.6, -5.2, 5.2, 6.6].map(x => <polyline key={x} points={pts(proj(x, 0, BACK_Z), proj(x, ROOM_H, BACK_Z))} stroke="#0a0c0f" strokeWidth={4} />)}
      <rect x={sl} y={btop} width={sr - sl} height={bfloor - btop} fill="#0b0d10" />
      <polygon points={pts(proj(-ROOM_X, ROOM_H, NEAR_Z), proj(ROOM_X, ROOM_H, NEAR_Z), proj(ROOM_X, ROOM_H, BACK_Z), proj(-ROOM_X, ROOM_H, BACK_Z))} fill="url(#fl-ceiling)" />
      {[-ROOM_X + 0.6, ROOM_X - 0.6].map(x => (
        <polygon key={x} points={flat(x - 0.25, x + 0.25, NEAR_Z, BACK_Z, ROOM_H - 0.02)} fill="#e9e2d4" opacity={0.25} filter="url(#fl-bloom)" />
      ))}
      <polygon points={pts(proj(-ROOM_X, 0, NEAR_Z), proj(ROOM_X, 0, NEAR_Z), proj(ROOM_X, 0, BACK_Z), proj(-ROOM_X, 0, BACK_Z))} fill="url(#fl-floor)" />
      {Array.from({ length: 13 }, (_, i) => i * 1.2 - 7.2).map(x => (
        <polyline key={`x${x}`} points={pts(proj(x, 0, NEAR_Z), proj(x, 0, BACK_Z))} stroke="#2e3339" strokeOpacity={0.35} strokeWidth={0.8} />
      ))}
      {[3, 4.2, 5.4, 6.6, 7.8, 9, 10.2, 11.4, 12.6].map(z => (
        <polyline key={`z${z}`} points={pts(proj(-ROOM_X, 0, z), proj(ROOM_X, 0, z))} stroke="#2e3339" strokeOpacity={0.3} strokeWidth={0.8} />
      ))}
      {lights}
    </g>
  );
}



/** One screen of the curved wall, x0..x1 metres wide, laid out in centimetres. */
function WallPanel({ x0, x1, top, bottom, children }: { x0: number; x1: number; top: number; bottom: number; children: (w: number, h: number) => ReactNode }) {
  const w = (x1 - x0) * 100, h = (top - bottom) * 100;
  const tl = proj(x0, top, screenZ(x0)), tr = proj(x1, top, screenZ(x1)), bl = proj(x0, bottom, screenZ(x0)), br = proj(x1, bottom, screenZ(x1));
  return (
    <g>
      <polygon points={pts(tl, tr, br, bl)} fill="#050709" stroke="#323a42" strokeWidth={3} strokeLinejoin="round" />
      <g transform={fit(tl, tr, bl, br, w, h)}>
        <rect x={6} y={6} width={w - 12} height={h - 12} fill="url(#fl-screen)" />
        {children(w, h)}
        <rect x={6} y={6} width={w - 12} height={h - 12} fill="url(#fl-scan)" />
      </g>
    </g>
  );
}

function SymbolPanel({ s, market, w, h }: { s: Sym; market: Shared["market"]; w: number; h: number }) {
  const mid = priceOf(market, s), change = changeOf(market, s);
  const series = market.series[s].slice(-160);
  return (
    <g>
      <text x={22} y={40} className="fl-wp-label">{s}/USD</text>
      <text x={22} y={88} className="fl-wp-big">{mid != null ? usd(mid) : "no feed"}</text>
      <text x={22} y={118} className={`fl-wp-num ${change == null ? "" : change >= 0 ? "fl-up" : "fl-down"}`}>{change == null ? "—" : `${change >= 0 ? "+" : "-"}${pct(Math.abs(change))}`}</text>
      {series.length > 1
        ? <path d={sparkPath(series, 22, 138, w - 44, h - 168)} fill="none" stroke="#e6edf2" strokeWidth={2.4} opacity={0.9} />
        : <text x={22} y={h - 40} className="fl-wp-axis">awaiting live feed</text>}
    </g>
  );
}

function WallScreens({ market, state, actions }: { market: Shared["market"]; state: FloorState | null; actions: FloorAction[] }) {
  const usdt = state?.accounts?.USDT ?? null;
  const equity = usdt ? Number(usdt.equity) : null;
  const daily = equity != null && usdt && Number(usdt.start_of_day_equity) > 0 ? equity / Number(usdt.start_of_day_equity) - 1 : null;
  const drawdown = equity != null && usdt && Number(usdt.peak_equity) > 0 ? equity / Number(usdt.peak_equity) - 1 : null;
  const halted = isHalted(state);
  const rank = (stage: string) => STAGES.indexOf(stage as (typeof STAGES)[number]);
  const lead = [...(state?.promotion ?? [])].sort((a, b) => rank(b.stage) - rank(a.stage))[0];
  const btc = market.series.BTC.slice(-240);
  const btcMid = priceOf(market, "BTC"), btcChange = changeOf(market, "BTC");
  const lo = btc.length ? Math.min(...btc) : null, hi = btc.length ? Math.max(...btc) : null;
  const [gx0, gy0] = proj(-5.8, 5.6, screenZ(-5.8)), [gx1, gy1] = proj(5.8, 2.1, screenZ(0));
  return (
    <g>
      <rect x={gx0} y={gy0} width={gx1 - gx0} height={gy1 - gy0} fill="#c4c9ce" opacity={0.06} filter="url(#fl-glow)" />
      <WallPanel x0={-1.5} x1={1.5} top={5.72} bottom={5.4}>
        {w => (
          <text x={w / 2} y={22} textAnchor="middle" className="fl-wp-tape" fill="#d7dce1">
            PROTO · {state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM"}
          </text>
        )}
      </WallPanel>
      <WallPanel x0={-5.7} x1={-1.55} top={5.3} bottom={2.75}>
        {(w, h) => (
          <g>
            <text x={24} y={44} className="fl-wp-label">BTC/USD · {market.connected ? "LIVE PUBLIC" : "LAST KNOWN / REST"}</text>
            <text x={24} y={104} className="fl-wp-huge">{btcMid != null ? usd(btcMid) : "no feed"}</text>
            <text x={24} y={140} className={`fl-wp-num ${btcChange == null ? "" : btcChange >= 0 ? "fl-up" : "fl-down"}`}>{btcChange == null ? "—" : `${btcChange >= 0 ? "+" : "-"}${pct(Math.abs(btcChange))}`}</text>
            {[0, 1, 2, 3].map(g => <line key={g} x1={24} x2={w - 90} y1={160 + g * ((h - 190) / 3)} y2={160 + g * ((h - 190) / 3)} stroke="#1d2833" />)}
            {btc.length > 1 ? (
              <g>
                <path d={`${sparkPath(btc, 24, 160, w - 120, h - 190)} L${w - 96},${h - 30} L24,${h - 30} Z`} fill="#e6edf2" opacity={0.06} />
                <path d={sparkPath(btc, 24, 160, w - 120, h - 190)} fill="none" stroke="#dfe3e6" strokeWidth={2.4} />
                <text x={w - 82} y={170} className="fl-wp-axis">{hi != null ? usd(hi) : ""}</text>
                <text x={w - 82} y={h - 30} className="fl-wp-axis">{lo != null ? usd(lo) : ""}</text>
              </g>
            ) : <text x={w / 2} y={h / 2 + 60} textAnchor="middle" className="fl-wp-label">awaiting live feed</text>}
          </g>
        )}
      </WallPanel>
      <WallPanel x0={-1.45} x1={0.95} top={5.3} bottom={2.75}>{(w, h) => <SymbolPanel s="ETH" market={market} w={w} h={h} />}</WallPanel>
      <WallPanel x0={1.05} x1={3.45} top={5.3} bottom={2.75}>{(w, h) => <SymbolPanel s="SOL" market={market} w={w} h={h} />}</WallPanel>
      <WallPanel x0={3.55} x1={5.7} top={5.3} bottom={2.75}>
        {() => (
          <g>
            <text x={22} y={40} className="fl-wp-label">EQUITY USDT · {state == null ? "N/A" : state.data_mode === "TESTNET" ? "TESTNET" : "PAPER"}</text>
            <text x={22} y={90} className="fl-wp-big">{equity == null || !Number.isFinite(equity) ? "—" : equity.toFixed(2)}</text>
            <text x={22} y={128} className="fl-wp-label">DAY / -1%</text>
            <text x={22} y={156} className={`fl-wp-num ${daily == null ? "" : daily < 0 ? "fl-down" : "fl-up"}`}>{pct(daily)}</text>
            <text x={22} y={188} className="fl-wp-label">DD / -5%</text>
            <text x={22} y={216} className={`fl-wp-num ${drawdown != null && drawdown < 0 ? "fl-down" : ""}`}>{pct(drawdown)}</text>
            <text x={128} y={128} className="fl-wp-label">LOCKDOWN</text>
            <text x={128} y={156} className={`fl-wp-num ${state?.controls.lockdown ? "fl-down" : ""}`}>{state == null ? "—" : state.controls.lockdown ?? "none"}</text>
            <text x={128} y={188} className="fl-wp-label">STATUS</text>
            <text x={128} y={216} className={`fl-wp-num ${state == null ? "" : halted ? "fl-down" : "fl-up"}`}>{state == null ? "UNAVAILABLE" : halted ? "HALTED" : "ARMED"}</text>
          </g>
        )}
      </WallPanel>
      <WallPanel x0={-5.7} x1={5.7} top={2.6} bottom={2.18}>
        {w => (
          <g>
            <text x={24} y={28} className="fl-wp-label">ÚLTIMAS AÇÕES</text>
            {actions.slice(-3).reverse().map((a, i) => (
              <text key={`${a.seq}:${a.station}`} x={200 + i * 245} y={28} className="fl-wp-tape" fill={TONE_COLOR[a.tone]}>
                {`● ${a.at.slice(11, 16)} ${a.station.slice(0, 6).toUpperCase()} ${a.headline.slice(0, 14)}`}
              </text>
            ))}
            <text x={w - 24} y={28} textAnchor="end" className="fl-wp-tape" fill="#c4ccd3">
              {state == null ? "" : lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE"}
            </text>
            {!actions.length && <text x={200} y={28} className="fl-wp-tape" fill="#838b94">no agent activity yet</text>}
          </g>
        )}
      </WallPanel>
    </g>
  );
}

/** The polished floor reflects the wall of screens. */
function Reflection() {
  const [x0, y0] = proj(-5.7, 0, screenZ(-5.7)), [x1] = proj(5.7, 0, screenZ(5.7));
  return <rect x={x0} y={y0 + 4} width={x1 - x0} height={170} fill="url(#fl-reflect)" filter="url(#fl-soft)" />;
}

// ---------- people, desks, plants ----------

/** Operator seen from behind in a high-back chair, in centimetres. Only verified activity moves the hands. */
function Operator({ x, z, hair, suit, busy, color }: { x: number; z: number; hair: string; suit: string; busy: boolean; color: string }) {
  const rim = busy ? color : "#c2c8ce";
  return (
    <g transform={at(x, 0, z)} data-activity={busy ? "working" : "idle"} className={busy ? "fl-person busy" : "fl-person"}>
      <ellipse cx={0} cy={0} rx={50} ry={10} fill="#000" opacity={0.55} />
      {[-1, 1].map(s => <path key={s} d={`M0 -7 L${s * 32} 1 M0 -7 L${s * 17} 6`} stroke="#121417" strokeWidth={4.5} strokeLinecap="round" />)}
      <rect x={-3} y={-46} width={6} height={40} fill="#2a2e33" />
      <path d="M-18 -52 L-22 -114 Q-25 -129 -12 -133 L12 -133 Q25 -129 22 -114 L18 -52Z" fill={suit} />
      <path className="fl-arm" d="M-22 -126 Q-38 -108 -34 -93 Q-29 -86 -14 -90" fill="none" stroke={suit} strokeWidth={11} strokeLinecap="round" />
      <path className="fl-arm right" d="M22 -126 Q38 -108 34 -93 Q29 -86 14 -90" fill="none" stroke={suit} strokeWidth={11} strokeLinecap="round" />
      <rect x={-5} y={-142} width={10} height={11} fill="#7d5e4c" />
      {[-1, 1].map(s => <ellipse key={s} cx={s * 10.4} cy={-149} rx={2.3} ry={3.6} fill="#8d6b56" />)}
      <ellipse cx={0} cy={-151} rx={10.6} ry={12.8} fill={hair} />
      <path d="M-24 -50 L-26 -128 Q-26 -146 -12 -147 L12 -147 Q26 -146 26 -128 L24 -50 Z" fill="url(#fl-chair)" stroke="#2c3137" strokeWidth={1.2} opacity={0.97} />
      <path d="M-26 -128 Q-26 -146 -12 -147 L12 -147 Q26 -146 26 -128" fill="none" stroke="#3a4048" strokeWidth={1.4} />
      <ellipse cx={0} cy={-49} rx={29} ry={7} fill="#15181b" />
      {[-1, 1].map(s => <path key={s} d={`M${s * 26} -96 L${s * 33} -98 L${s * 33} -78`} fill="none" stroke="#1a1d21" strokeWidth={4} />)}
      <g filter="url(#fl-bloom)" opacity={busy ? 0.95 : 0.45}>
        <path d="M-9.5 -160 Q0 -165.5 9.5 -160" fill="none" stroke={rim} strokeWidth={1.4} />
        <path d="M-26 -130 Q-26 -146 -12 -147.5 L12 -147.5 Q26 -146 26 -130" fill="none" stroke={rim} strokeWidth={1} opacity={0.6} />
      </g>
    </g>
  );
}

function Monitor({ d, lx, lz, turn, children }: { d: StationDef; lx: number; lz: number; turn: number; children: ReactNode }) {
  const half = 0.31, y0 = 0.98, y1 = 1.4;
  const dx = Math.cos(turn) * half, dz = Math.sin(turn) * half;
  const tl = pd(d, lx - dx, y1, lz + dz), tr = pd(d, lx + dx, y1, lz - dz), bl = pd(d, lx - dx, y0, lz + dz), br = pd(d, lx + dx, y0, lz - dz);
  const foot = pd(d, lx, DESK_H, lz + 0.04), neck = pd(d, lx, y0, lz);
  return (
    <g>
      <polyline points={pts(foot, neck)} stroke="#30363d" strokeWidth={Math.max(1.5, scale(local(d, lx, 0, lz)[2]) * 0.035)} />
      <polygon points={pts(tl, tr, br, bl)} fill="#06080a" stroke="#4e5862" strokeWidth={1} />
      <g transform={fit(tl, tr, bl, br, 62, 42)}>
        <rect x={1.5} y={1.5} width={59} height={39} fill="url(#fl-screen)" className="fl-monitor-screen" />
        {children}
      </g>
    </g>
  );
}

function MonitorSymbol({ s, market, color }: { s: Sym; market: Shared["market"]; color: string }) {
  const mid = priceOf(market, s), series = market.series[s].slice(-60);
  return (
    <g>
      <text x={4} y={9} className="fl-mon-sym">{s}/USD</text>
      <text x={4} y={19} className="fl-mon-num">{mid != null ? usd(mid) : "no feed"}</text>
      {series.length > 1 ? <path d={sparkPath(series, 4, 22, 54, 15)} fill="none" stroke={color} strokeWidth={1} /> : <line x1={4} x2={58} y1={34} y2={34} stroke="#33414c" strokeWidth={0.8} />}
    </g>
  );
}

function MonitorLog({ recent }: { recent: FloorAction[] }) {
  return (
    <g>
      <text x={4} y={9} className="fl-mon-sym">JOURNAL</text>
      {recent.slice(-3).reverse().map((a, i) => (
        <text key={a.seq} x={4} y={18 + i * 8} className="fl-mon-log" fill={TONE_COLOR[a.tone]}>{`${a.at.slice(11, 19)} ${a.headline.slice(0, 14)}`}</text>
      ))}
      {!recent.length && <text x={4} y={20} className="fl-mon-log" fill="#77818b">no events yet</text>}
    </g>
  );
}

function Station({ def, action, busy, recent, market, index }: {
  def: StationDef;
  action: FloorAction | undefined;
  busy: boolean;
  recent: FloorAction[];
  market: Shared["market"];
  index: number;
}) {
  const tone: Tone | "idle" = action ? action.tone : "idle";
  const color = TONE_COLOR[tone];
  const W = def.w, D = def.hub ? 1.05 : DESK_D, c = 0.28;
  const top = [pd(def, -W, DESK_H, 0), pd(def, W, DESK_H, 0), pd(def, W, DESK_H, D - c), pd(def, W - c, DESK_H, D), pd(def, -W + c, DESK_H, D), pd(def, -W, DESK_H, D - c)];
  const base = [pd(def, -W + 0.05, 0.02, 0.05), pd(def, W - 0.05, 0.02, 0.05), pd(def, W - 0.05, 0.02, D - c), pd(def, W - c, 0.02, D - 0.05), pd(def, -W + c, 0.02, D - 0.05), pd(def, -W + 0.05, 0.02, D - c)];
  const pedestal = (side: number) => {
    const a = side * (W - 0.05), b = side * (W - 0.45);
    return pts(pd(def, a, 0, 0.06), pd(def, b, 0, 0.06), pd(def, b, DESK_H - 0.04, 0.06), pd(def, a, DESK_H - 0.04, 0.06));
  };
  const spread = def.hub ? 0.68 : 0.64;
  const symbols = [SYMBOLS[index % SYMBOLS.length], SYMBOLS[(index + 1) % SYMBOLS.length]];
  const headline = action?.headline ?? "waiting for work";
  const [px, py] = pd(def, 0, DESK_H, D * 0.5);
  const s = scale(local(def, 0, 0, D * 0.5)[2]);
  const [, , zop] = local(def, 0, 0, -0.32);
  const [xop] = local(def, 0, 0, -0.32);
  const plate = local(def, 0, 1.56, D - 0.1);
  return (
    <g className={busy ? "fl-station busy" : "fl-station"} data-station={def.id}>
      <polygon points={pts(...base)} fill="none" stroke={busy ? color : "#c9ced3"} strokeWidth={1.2} opacity={busy ? 0.75 : 0.28} filter="url(#fl-led)" />
      <polygon points={pedestal(-1)} fill="url(#fl-panel)" stroke="#2f353c" strokeWidth={0.8} />
      <polygon points={pedestal(1)} fill="url(#fl-panel)" stroke="#2f353c" strokeWidth={0.8} />
      <polygon points={pts(...top)} fill="url(#fl-desk)" stroke="#59616a" strokeWidth={0.8} />
      <polyline points={pts(top[0], top[1])} stroke={busy ? color : "#c9ced3"} strokeWidth={1} opacity={0.35} />
      <ellipse cx={px} cy={py} rx={W * s * 0.85} ry={s * 0.2} fill="url(#fl-cool)" className="fl-pool" />
      <Monitor d={def} lx={-spread} lz={D - 0.2} turn={-0.35}><MonitorSymbol s={symbols[0]} market={market} color={busy ? color : "#cfd8df"} /></Monitor>
      <Monitor d={def} lx={0} lz={D - 0.12} turn={0}><MonitorLog recent={recent} /></Monitor>
      <Monitor d={def} lx={spread} lz={D - 0.2} turn={0.35}><MonitorSymbol s={symbols[1]} market={market} color={busy ? color : "#cfd8df"} /></Monitor>
      <g transform={at(plate[0], plate[1], plate[2], 0.8)}>
        <g className="fl-holo" transform="translate(-80 -52)">
          <rect width={160} height={52} rx={6} fill="#0d1116" fillOpacity={0.92} stroke={busy ? color : "#4a535c"} strokeWidth={busy ? 1.8 : 1} />
          <rect x={10} y={11} width={24} height={24} rx={5} fill="none" stroke="#c8d0d7" strokeWidth={1.4} />
          <path d="M16 29 L22 21 L26 25 L30 17" fill="none" stroke={busy ? color : "#c8d0d7"} strokeWidth={1.6} />
          <text x={42} y={21} className="fl-holo-title" fill="#e6eaee">{def.label}</text>
          <text x={42} y={33} className="fl-holo-role">{def.role}{action?.symbol ? " · " + action.symbol : ""}</text>
          <text x={42} y={45} className="fl-holo-text">{headline.length > 24 ? headline.slice(0, 23) + "…" : headline}</text>
          <circle cx={150} cy={10} r={3} fill={busy ? color : "#4d555d"} />
        </g>
      </g>
      <Operator x={xop} z={zop} hair={def.hair} suit={def.suit} busy={busy} color={color} />
      <title>{def.label + ": " + headline}</title>
    </g>
  );
}

/** A potted plant, in centimetres: decoration. */
function Plant({ x, z, size = 1 }: { x: number; z: number; size?: number }) {
  const leaves = Array.from({ length: 14 }, (_, i) => {
    const a = -Math.PI / 2 + (rand(i * 3 + x) - 0.5) * 2.4, len = (45 + rand(i + z) * 45) * size;
    const ex = Math.cos(a) * len, ey = -60 * size + Math.sin(a) * len;
    return <path key={i} d={`M0 ${-50 * size} Q${ex * 0.4 - 8} ${ey * 0.6} ${ex} ${ey} Q${ex * 0.5 + 8} ${ey * 0.5} 0 ${-50 * size}`} fill={i % 3 ? "#1d2621" : "#2a352d"} opacity={0.95} />;
  });
  return (
    <g transform={at(x, 0, z)}>
      <ellipse cx={0} cy={0} rx={30 * size} ry={7 * size} fill="#000" opacity={0.5} />
      <path d={`M${-20 * size} ${-52 * size} L${-16 * size} 0 L${16 * size} 0 L${20 * size} ${-52 * size} Z`} fill="#2a2d31" stroke="#454b52" strokeWidth={1} />
      {leaves}
    </g>
  );
}

/** Glowing floor lines carrying work between desks; a segment lights when its target desk is working. */
function Pipeline({ latest, active }: { latest: Partial<Record<StationId, FloorAction>>; active: (s: StationId) => boolean }) {
  const foot = (d: StationDef): P => pd(d, 0, 0.01, d.hub ? -0.2 : 0.2);
  return (
    <g>
      {STATIONS.slice(1).map((to, i) => {
        const from = STATIONS[i], busy = active(to.id);
        const [x1, y1] = foot(from), [x2, y2] = foot(to);
        const mid: P = [(x1 + x2) / 2, Math.max(y1, y2) + 22];
        return (
          <path key={to.id} d={`M${x1.toFixed(1)},${y1.toFixed(1)} Q${mid[0].toFixed(1)},${mid[1].toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`}
            className={busy ? "fl-beam busy" : "fl-beam"} stroke={busy ? TONE_COLOR[latest[to.id]?.tone ?? "working"] : "#c9ced3"} filter="url(#fl-led)" />
        );
      })}
    </g>
  );
}

export function VectorScene({ market, state, actions, latest, active }: {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  active: (s: StationId) => boolean;
}) {
  const summary = STATIONS.map(s => `${s.label}: ${latest[s.id]?.headline ?? "idle"}`).join("; ");
  const plants: [number, number, number][] = [[-6.5, 10.8, 1.3], [6.5, 10.8, 1.3], [-6.2, 6.4, 1.2], [6.3, 6.0, 1.2], [-2.6, 7.4, 0.8], [2.7, 7.4, 0.8]];
  const items: { z: number; node: ReactNode }[] = [
    ...STATIONS.map((s, i) => ({
      z: s.z,
      node: <Station key={s.id} index={i} def={s} action={latest[s.id]} busy={active(s.id)} recent={actions.filter(a => a.station === s.id)} market={market} />,
    })),
    ...plants.map(([x, z, size]) => ({ z, node: <Plant key={`p${x}:${z}`} x={x} z={z} size={size} /> })),
  ].sort((a, b) => b.z - a.z);
  return (
    <svg className="fl-scene" viewBox={`0 0 ${VW} ${VH}`} role="img" aria-label={`Trading floor seen from behind the central desk. ${summary}`}>
      <Defs />
      <City />
      <Room />
      <WallScreens market={market} state={state} actions={actions} />
      <Reflection />
      <Pipeline latest={latest} active={active} />
      {items.map(i => i.node)}
      <g filter="url(#fl-dof)" pointerEvents="none">
        <circle cx={120} cy={70} r={40} fill="#f0e3c8" opacity={0.05} />
        <circle cx={1170} cy={90} r={50} fill="#c4c9ce" opacity={0.04} />
      </g>
      <rect width={VW} height={VH} fill="url(#fl-vignette)" pointerEvents="none" />
    </svg>
  );
}
