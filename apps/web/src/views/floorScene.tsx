// Photo trading room, shown while the live 3D room loads or where it cannot run. The room itself is a photograph with every screen blank; each screen, desk
// label and activity light is drawn live on top of it from /v1/floor/state, /ws/floor and the live
// public market stream. Nothing operational is baked into the picture. If the photo cannot be loaded
// the vector room is drawn instead.
import { Component, lazy, type ReactNode, Suspense, useState } from "react";
import { type Sym, SYMBOLS } from "../api";
import ROOM from "../assets/floor-room.webp";
import type { Shared } from "../main";
import { pct, usd } from "../ui";
import { changeOf, type FloorAction, type FloorState, isHalted, priceOf, sparkPath, STAGES, STATIONS, type StationId, type Tone, TONE_COLOR } from "./floorModel";
import { VectorScene } from "./floorSceneVector";

export { isHalted, STAGES, STATIONS, TONE_COLOR };
export type { Account, FloorAction, FloorState, PromotionScope, StationId, Tone } from "./floorModel";

// The photo is 1179 x 664; every coordinate below is a pixel of it.
const VW = 1179, VH = 664;
type P = [number, number];
/** A screen as its four corners in the photo: top-left, top-right, bottom-left, bottom-right. */
type Quad = [P, P, P, P];
const pts = (...p: P[]) => p.map(([a, b]) => `${a},${b}`).join(" ");
/** Maps a w x h drawing onto a screen; the vertical axis averages the two sides of the screen. */
const fit = ([tl, tr, bl, br]: Quad, w: number, h: number) => {
  const down: P = [(bl[0] - tl[0] + br[0] - tr[0]) / 2, (bl[1] - tl[1] + br[1] - tr[1]) / 2];
  const f = (n: number) => n.toFixed(4);
  return `matrix(${f((tr[0] - tl[0]) / w)} ${f((tr[1] - tl[1]) / w)} ${f(down[0] / h)} ${f(down[1] / h)} ${f(tl[0])} ${f(tl[1])})`;
};
const widthOf = ([tl, tr]: Quad) => Math.hypot(tr[0] - tl[0], tr[1] - tl[1]);
const heightOf = ([tl, tr, bl, br]: Quad) => (bl[1] - tl[1] + br[1] - tr[1]) / 2;

const WALL: Record<"btc" | "eth" | "sol" | "equity" | "strip", Quad> = {
  btc: [[223, 47], [478, 65], [223, 203], [478, 203]],
  eth: [[484, 66], [639, 67], [484, 202], [639, 202]],
  sol: [[644, 67], [796, 61], [644, 204], [796, 204]],
  equity: [[801, 60], [960, 46], [801, 206], [960, 206]],
  strip: [[223, 213], [960, 214], [223, 248], [960, 247]],
};

type Desk = {
  monitors: [Quad, Quad, Quad]; // left, centre, right
  head: P; // the operator's head
  base: P; // centre of the desk's floor glow
  baseR: [number, number];
  plate: P; // bottom centre of the floating label
  plateScale: number;
  /** The operator's head and shoulders as ellipses [cx, cy, rx, ry]: they stand in front of the monitors. */
  occluder: [number, number, number, number][];
};
const DESKS: Record<StationId, Desk> = {
  tradingview: {
    monitors: [[[29, 323], [82, 315], [29, 373], [82, 367]], [[83, 313], [150, 307], [83, 367], [150, 363]], [[151, 307], [210, 305], [151, 363], [210, 358]]],
    head: [140, 342], base: [132, 497], baseR: [138, 22], plate: [118, 300], plateScale: 1,
    occluder: [[139, 350, 18, 22], [140, 373, 31, 14]],
  },
  fusion: {
    monitors: [[[286, 299], [335, 293], [286, 335], [335, 334]], [[336, 292], [409, 292], [336, 333], [409, 333]], [[410, 292], [457, 293], [410, 333], [457, 332]]],
    head: [375, 305], base: [372, 412], baseR: [112, 15], plate: [372, 287], plateScale: 0.85,
    occluder: [[376, 311, 11, 12.5], [376, 333, 28, 12]],
  },
  reviewer: {
    monitors: [[[422, 383], [518, 371], [422, 460], [518, 443]], [[522, 370], [656, 370], [522, 442], [656, 442]], [[660, 371], [757, 383], [660, 443], [757, 460]]],
    head: [583, 428], base: [587, 616], baseR: [250, 34], plate: [589, 367], plateScale: 1,
    occluder: [[582, 424, 23.5, 27.5], [583, 450, 15, 10]],
  },
  risk: {
    monitors: [[[721, 294], [771, 293], [721, 334], [771, 334]], [[772, 293], [843, 293], [772, 334], [843, 334]], [[845, 293], [894, 300], [845, 333], [894, 334]]],
    head: [807, 305], base: [804, 412], baseR: [112, 15], plate: [808, 287], plateScale: 0.85,
    occluder: [[802, 311, 11, 12.5], [802, 333, 28, 12]],
  },
  oms: {
    monitors: [[[969, 305], [1029, 307], [969, 357], [1029, 360]], [[1030, 307], [1097, 313], [1030, 360], [1097, 365]], [[1098, 313], [1151, 323], [1098, 365], [1151, 372]]],
    head: [1045, 342], base: [1050, 497], baseR: [138, 22], plate: [1062, 300], plateScale: 1,
    occluder: [[1044, 346, 15.5, 17.5], [1045, 368, 33, 12]],
  },
  reconciliation: {
    monitors: [[[510, 255], [551, 255], [510, 291], [551, 291]], [[552, 255], [620, 255], [552, 291], [620, 291]], [[622, 255], [660, 255], [622, 291], [660, 291]]],
    head: [586, 271], base: [587, 343], baseR: [100, 11], plate: [589, 331], plateScale: 0.8,
    occluder: [[587, 271, 8.5, 10.5], [588, 292, 22, 9]],
  },
};

const signed = (v: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : "-"}${pct(Math.abs(v))}`);
const toneClass = (v: number | null) => (v == null ? "" : v >= 0 ? "fl-up" : "fl-down");

/** A screen of the photo, switched on and showing a live drawing laid out in its own w x h units. */
function Screen({ quad, w, h, lit = false, children }: { quad: Quad; w: number; h: number; lit?: boolean; children: ReactNode }) {
  const id = `fl-clip-${quad[0][0]}-${quad[0][1]}`;
  return (
    <g>
      <clipPath id={id}><polygon points={pts(quad[0], quad[1], quad[3], quad[2])} /></clipPath>
      <polygon points={pts(quad[0], quad[1], quad[3], quad[2])} fill="url(#fl-screen)" opacity={lit ? 1 : 0.92} />
      <g clipPath={`url(#${id})`}><g transform={fit(quad, w, h)}>{children}</g></g>
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
  const lo = btc.length ? Math.min(...btc) : null, hi = btc.length ? Math.max(...btc) : null;
  const btcMid = priceOf(market, "BTC");
  const symbolPanel = (s: Sym, quad: Quad) => {
    const w = 150, h = 132, series = market.series[s].slice(-160), mid = priceOf(market, s), change = changeOf(market, s);
    return (
      <Screen quad={quad} w={w} h={h} lit>
        <text x={9} y={15} className="fl-p-label">{s}/USD</text>
        <text x={9} y={36} className="fl-p-big">{mid != null ? usd(mid) : "no feed"}</text>
        <text x={9} y={49} className={`fl-p-num ${toneClass(change)}`}>{signed(change)}</text>
        {series.length > 1
          ? <path d={sparkPath(series, 9, 60, w - 18, h - 72)} fill="none" stroke="#dfe3e6" strokeWidth={1.2} />
          : <text x={9} y={h - 14} className="fl-p-axis">awaiting live feed</text>}
      </Screen>
    );
  };
  return (
    <g>
      <Screen quad={WALL.btc} w={250} h={150} lit>
        <text x={10} y={15} className="fl-p-label">BTC/USD · {market.connected ? "LIVE PUBLIC" : "LAST KNOWN / REST"}</text>
        <text x={10} y={42} className="fl-p-huge">{btcMid != null ? usd(btcMid) : "no feed"}</text>
        <text x={10} y={56} className={`fl-p-num ${toneClass(changeOf(market, "BTC"))}`}>{signed(changeOf(market, "BTC"))}</text>
        {[0, 1, 2, 3].map(g => <line key={g} x1={10} x2={206} y1={66 + g * 24} y2={66 + g * 24} stroke="#22272c" strokeWidth={0.6} />)}
        {btc.length > 1 ? (
          <g>
            <path d={`${sparkPath(btc, 10, 66, 194, 72)} L204,138 L10,138 Z`} fill="#dfe3e6" opacity={0.05} />
            <path d={sparkPath(btc, 10, 66, 194, 72)} fill="none" stroke="#e6e9ec" strokeWidth={1.3} />
            <text x={210} y={70} className="fl-p-axis">{hi != null ? usd(hi) : ""}</text>
            <text x={210} y={138} className="fl-p-axis">{lo != null ? usd(lo) : ""}</text>
          </g>
        ) : <text x={125} y={105} textAnchor="middle" className="fl-p-label">awaiting live feed</text>}
      </Screen>
      {symbolPanel("ETH", WALL.eth)}
      {symbolPanel("SOL", WALL.sol)}
      <Screen quad={WALL.equity} w={156} h={146} lit>
        <text x={9} y={15} className="fl-p-label">EQUITY USDT · {state == null ? "N/A" : state.data_mode === "TESTNET" ? "TESTNET" : "PAPER"}</text>
        <text x={9} y={37} className="fl-p-big">{equity == null || !Number.isFinite(equity) ? "—" : equity.toFixed(2)}</text>
        <text x={9} y={58} className="fl-p-label">DAY / -1%</text>
        <text x={9} y={71} className={`fl-p-num ${daily == null ? "" : daily < 0 ? "fl-down" : "fl-up"}`}>{pct(daily)}</text>
        <text x={9} y={90} className="fl-p-label">DD / -5%</text>
        <text x={9} y={103} className={`fl-p-num ${drawdown != null && drawdown < 0 ? "fl-down" : ""}`}>{pct(drawdown)}</text>
        <text x={82} y={58} className="fl-p-label">LOCKDOWN</text>
        <text x={82} y={71} className={`fl-p-num ${state?.controls.lockdown ? "fl-down" : ""}`}>{state == null ? "—" : state.controls.lockdown ?? "none"}</text>
        <text x={82} y={90} className="fl-p-label">STATUS</text>
        <text x={82} y={103} className={`fl-p-num ${state == null ? "" : halted ? "fl-down" : "fl-up"}`}>{state == null ? "UNAVAILABLE" : halted ? "HALTED" : "ARMED"}</text>
        <text x={9} y={128} className="fl-p-axis">PROTO · {state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM"}</text>
      </Screen>
      <Screen quad={WALL.strip} w={737} h={34}>
        <text x={12} y={21} className="fl-p-label">ÚLTIMAS AÇÕES</text>
        {actions.slice(-3).reverse().map((a, i) => (
          <text key={`${a.seq}:${a.station}`} x={92 + i * 182} y={21} className="fl-p-tape" fill={TONE_COLOR[a.tone]}>
            {`● ${a.at.slice(11, 16)} ${a.station.slice(0, 6).toUpperCase()} ${a.headline.slice(0, 18)}`}
          </text>
        ))}
        {!actions.length && <text x={92} y={21} className="fl-p-tape" fill="#838b94">no agent activity yet</text>}
        <text x={725} y={21} textAnchor="end" className="fl-p-tape" fill="#c4ccd3">
          {state == null ? "" : lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE"}
        </text>
      </Screen>
    </g>
  );
}

function MonitorSymbol({ quad, s, market, color, lit }: { quad: Quad; s: Sym; market: Shared["market"]; color: string; lit: boolean }) {
  const w = 100, h = (100 * heightOf(quad)) / widthOf(quad);
  const mid = priceOf(market, s), series = market.series[s].slice(-60);
  return (
    <Screen quad={quad} w={w} h={h} lit={lit}>
      <text x={7} y={14} className="fl-m-sym">{s}/USD</text>
      <text x={7} y={31} className="fl-m-num">{mid != null ? usd(mid) : "no feed"}</text>
      {series.length > 1 ? <path d={sparkPath(series, 7, 38, w - 14, h - 46)} fill="none" stroke={color} strokeWidth={1.8} /> : <line x1={7} x2={w - 7} y1={h - 10} y2={h - 10} stroke="#30363c" strokeWidth={1.2} />}
    </Screen>
  );
}

function MonitorLog({ quad, recent, lit }: { quad: Quad; recent: FloorAction[]; lit: boolean }) {
  const w = 100, h = (100 * heightOf(quad)) / widthOf(quad);
  return (
    <Screen quad={quad} w={w} h={h} lit={lit}>
      <text x={7} y={14} className="fl-m-sym">JOURNAL</text>
      {recent.slice(-3).reverse().map((a, i) => (
        <text key={a.seq} x={7} y={27 + i * 11} className="fl-m-log" fill={TONE_COLOR[a.tone]}>{`${a.at.slice(11, 19)} ${a.headline.slice(0, 16)}`}</text>
      ))}
      {!recent.length && <text x={7} y={28} className="fl-m-log" fill="#77818b">no events yet</text>}
    </Screen>
  );
}

/** The photographed operator's live state: a soft light on the shoulders while the desk works. */
function AgentOperator({ head, busy, color }: { head: P; busy: boolean; color: string }) {
  const [x, y] = head;
  return (
    <g data-activity={busy ? "working" : "idle"} className={busy ? "fl-person busy" : "fl-person"} pointerEvents="none">
      <ellipse cx={x} cy={y + 14} rx={30} ry={20} fill={color} opacity={busy ? 0.16 : 0} filter="url(#fl-soft)" className="fl-pool" />
      <circle cx={x + 14} cy={y - 12} r={2.4} fill={busy ? color : "#5b6269"} opacity={busy ? 1 : 0.7} />
    </g>
  );
}

function Station({ id, label, role, action, busy, recent, market, index }: {
  id: StationId; label: string; role: string;
  action: FloorAction | undefined; busy: boolean; recent: FloorAction[]; market: Shared["market"]; index: number;
}) {
  const desk = DESKS[id];
  const color = TONE_COLOR[action ? action.tone : "idle"];
  const headline = action?.headline ?? "waiting for work";
  const symbols = [SYMBOLS[index % SYMBOLS.length], SYMBOLS[(index + 1) % SYMBOLS.length]];
  const [bx, by] = desk.base;
  const [px, py] = desk.plate;
  return (
    <g className={busy ? "fl-station busy" : "fl-station"} data-station={id}>
      <ellipse cx={bx} cy={by} rx={desk.baseR[0]} ry={desk.baseR[1]} fill={color} opacity={busy ? 0.22 : 0} filter="url(#fl-soft)" className="fl-pool" />
      <MonitorSymbol quad={desk.monitors[0]} s={symbols[0]} market={market} color={busy ? color : "#cfd4d8"} lit={busy} />
      <MonitorLog quad={desk.monitors[1]} recent={recent} lit={busy} />
      <MonitorSymbol quad={desk.monitors[2]} s={symbols[1]} market={market} color={busy ? color : "#cfd4d8"} lit={busy} />
      <clipPath id={`fl-head-${id}`}>{desk.occluder.map(([cx, cy, rx, ry], i) => <ellipse key={i} cx={cx} cy={cy} rx={rx} ry={ry} />)}</clipPath>
      <use href="#fl-room" clipPath={`url(#fl-head-${id})`} />
      <AgentOperator head={desk.head} busy={busy} color={color} />
      <g transform={`translate(${px} ${py}) scale(${desk.plateScale})`}>
        <g className="fl-holo" transform="translate(-78 -38)">
          <rect width={156} height={38} rx={5} fill="#0e1013" fillOpacity={0.88} stroke={busy ? color : "#4a5057"} strokeWidth={busy ? 1.4 : 0.8} />
          <rect x={8} y={8} width={22} height={22} rx={4} fill="none" stroke="#bfc5ca" strokeWidth={1.1} />
          <path d="M13 25 L18 18 L22 22 L26 14" fill="none" stroke={busy ? color : "#bfc5ca"} strokeWidth={1.4} />
          <text x={37} y={16} className="fl-holo-title" fill="#e6e9ec">{label}</text>
          <text x={37} y={25} className="fl-holo-role">{role}{action?.symbol ? " · " + action.symbol : ""}</text>
          <text x={37} y={34} className="fl-holo-text">{headline.length > 26 ? headline.slice(0, 25) + "…" : headline}</text>
          <circle cx={148} cy={8} r={2.6} fill={busy ? color : "#4d555d"} />
        </g>
      </g>
      <title>{label + ": " + headline}</title>
    </g>
  );
}

type SceneProps = {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  active: (s: StationId) => boolean;
};

// The live 3D room is the floor; it loads on demand and needs WebGL and OffscreenCanvas. While it
// loads, or if it cannot run, the photo room is shown.
const Floor3D = lazy(() => import("./floor3d/Floor3D"));
const can3d = () => typeof OffscreenCanvas !== "undefined" && typeof WebGL2RenderingContext !== "undefined";

class Fallback extends Component<{ onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onError(); }
  render() { return this.state.failed ? null : this.props.children; }
}

export function Scene(props: SceneProps) {
  const [mode, setMode] = useState<"3d" | "photo">(() => (can3d() ? "3d" : "photo"));
  if (mode === "photo") return <PhotoScene {...props} />;
  return (
    <Fallback onError={() => setMode("photo")}>
      <Suspense fallback={<PhotoScene {...props} />}>
        <Floor3D {...props} onFail={() => setMode("photo")} />
      </Suspense>
    </Fallback>
  );
}

function PhotoScene(props: SceneProps) {
  const { market, state, actions, latest, active } = props;
  const [photo, setPhoto] = useState(true);
  if (!photo) return <VectorScene {...props} />;
  const summary = STATIONS.map(s => `${s.label}: ${latest[s.id]?.headline ?? "idle"}`).join("; ");
  // Paint back to front so the nearer desks' labels sit over the farther ones.
  const order: StationId[] = ["reconciliation", "fusion", "risk", "tradingview", "oms", "reviewer"];
  return (
    <svg className="fl-scene" viewBox={`0 0 ${VW} ${VH}`} role="img" aria-label={`Trading floor seen from behind the central desk. ${summary}`}>
      <defs>
        <filter id="fl-soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="9" /></filter>
        <linearGradient id="fl-screen" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#15181b" /><stop offset="0.55" stopColor="#0e1012" /><stop offset="1" stopColor="#0a0b0d" />
        </linearGradient>
        <radialGradient id="fl-vignette" cx="0.5" cy="0.45" r="0.8">
          <stop offset="0.6" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity="0.45" />
        </radialGradient>
      </defs>
      <rect width={VW} height={VH} fill="#0b0d10" />
      <image id="fl-room" href={ROOM} width={VW} height={VH} preserveAspectRatio="xMidYMid slice" onError={() => setPhoto(false)} />
      <WallScreens market={market} state={state} actions={actions} />
      {order.map(id => {
        const s = STATIONS.find(st => st.id === id)!;
        return (
          <Station key={id} id={id} label={s.label} role={s.role} index={STATIONS.indexOf(s)} action={latest[id]} busy={active(id)}
            recent={actions.filter(a => a.station === id)} market={market} />
        );
      })}
      <rect width={VW} height={VH} fill="url(#fl-vignette)" pointerEvents="none" />
    </svg>
  );
}
