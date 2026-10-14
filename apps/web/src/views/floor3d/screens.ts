// Live screen faces for the 3D room, drawn on offscreen canvases. Every value comes from the floor
// state or the public market stream; a missing value is drawn as "—" or "no feed", never estimated.
import { type Sym } from "../../api";
import type { Shared } from "../../main";
import { pct, usd } from "../../ui";
import { changeOf, type FloorAction, type FloorState, isHalted, priceOf, STAGES, TONE_COLOR } from "../floorModel";

export type Ctx = OffscreenCanvasRenderingContext2D;
const MONO = '"JetBrains Mono", ui-monospace, monospace';
const INK = "#e6e9ec", DIM = "#a9b0b7", FAINT = "#7d858c", GRID = "#22272c";
const UP = TONE_COLOR.ok, DOWN = TONE_COLOR.alert;

export function canvas(w: number, h: number): { c: OffscreenCanvas; g: Ctx } {
  const c = new OffscreenCanvas(w, h);
  const g = c.getContext("2d");
  if (!g) throw new Error("2d canvas unavailable");
  return { c, g };
}

const font = (g: Ctx, size: number, weight = 500) => { g.font = `${weight} ${size}px ${MONO}`; };
const text = (g: Ctx, s: string, x: number, y: number, size: number, color = INK, weight = 500, align: CanvasTextAlign = "left") => {
  font(g, size, weight); g.fillStyle = color; g.textAlign = align; g.fillText(s, x, y);
};
const signed = (v: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : "-"}${pct(Math.abs(v))}`);
const toneOf = (v: number | null) => (v == null ? DIM : v >= 0 ? UP : DOWN);

function background(g: Ctx, w: number, h: number, lit = true) {
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, lit ? "#16191c" : "#101214");
  grad.addColorStop(1, "#08090a");
  g.fillStyle = grad; g.fillRect(0, 0, w, h);
}

function spark(g: Ctx, values: number[], x: number, y: number, w: number, h: number, color: string, width: number, fill = false) {
  if (values.length < 2) return false;
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  g.beginPath();
  values.forEach((v, i) => {
    const px = x + (i / (values.length - 1)) * w, py = y + h - ((v - lo) / span) * h;
    if (i) g.lineTo(px, py); else g.moveTo(px, py);
  });
  g.strokeStyle = color; g.lineWidth = width; g.lineJoin = "round"; g.stroke();
  if (fill) {
    g.lineTo(x + w, y + h); g.lineTo(x, y + h); g.closePath();
    g.fillStyle = "rgba(223,227,230,0.06)"; g.fill();
  }
  return true;
}

export function drawBtc(g: Ctx, w: number, h: number, market: Shared["market"]) {
  background(g, w, h);
  const series = market.series.BTC.slice(-240), mid = priceOf(market, "BTC"), change = changeOf(market, "BTC");
  text(g, `BTC/USD · ${market.connected ? "LIVE PUBLIC" : "LAST KNOWN / REST"}`, 40, 64, 30, DIM);
  text(g, mid != null ? usd(mid) : "no feed", 40, 160, 96, INK, 600);
  text(g, signed(change), 40, 214, 38, toneOf(change), 600);
  const cx = 40, cy = 250, cw = w - 240, ch = h - 300;
  g.strokeStyle = GRID; g.lineWidth = 2;
  for (let i = 0; i <= 3; i++) { g.beginPath(); g.moveTo(cx, cy + (i * ch) / 3); g.lineTo(cx + cw, cy + (i * ch) / 3); g.stroke(); }
  if (spark(g, series, cx, cy, cw, ch, "#e3e6e9", 4, true)) {
    text(g, usd(Math.max(...series)), cx + cw + 16, cy + 12, 24, FAINT);
    text(g, usd(Math.min(...series)), cx + cw + 16, cy + ch, 24, FAINT);
  } else text(g, "awaiting live feed", cx + cw / 2, cy + ch / 2, 30, DIM, 500, "center");
}

export function drawSymbol(g: Ctx, w: number, h: number, market: Shared["market"], s: Sym) {
  background(g, w, h);
  const series = market.series[s].slice(-160), mid = priceOf(market, s), change = changeOf(market, s);
  text(g, `${s}/USD`, 36, 64, 30, DIM);
  text(g, mid != null ? usd(mid) : "no feed", 36, 150, 70, INK, 600);
  text(g, signed(change), 36, 204, 36, toneOf(change), 600);
  if (!spark(g, series, 36, 250, w - 72, h - 300, "#e3e6e9", 4)) text(g, "awaiting live feed", 36, h - 60, 26, DIM);
}

export function drawEquity(g: Ctx, w: number, h: number, state: FloorState | null) {
  background(g, w, h);
  const usdt = state?.accounts?.USDT ?? null;
  const equity = usdt ? Number(usdt.equity) : null;
  const daily = equity != null && usdt && Number(usdt.start_of_day_equity) > 0 ? equity / Number(usdt.start_of_day_equity) - 1 : null;
  const drawdown = equity != null && usdt && Number(usdt.peak_equity) > 0 ? equity / Number(usdt.peak_equity) - 1 : null;
  const halted = isHalted(state);
  text(g, `EQUITY USDT · ${state == null ? "N/A" : state.data_mode === "TESTNET" ? "TESTNET" : "PAPER"}`, 34, 64, 28, DIM);
  text(g, equity == null || !Number.isFinite(equity) ? "—" : equity.toFixed(2), 34, 150, 66, INK, 600);
  text(g, "DAY / -1%", 34, 236, 26, DIM);
  text(g, pct(daily), 34, 284, 40, daily == null ? DIM : daily < 0 ? DOWN : UP, 600);
  text(g, "DD / -5%", 34, 356, 26, DIM);
  text(g, pct(drawdown), 34, 404, 40, drawdown != null && drawdown < 0 ? DOWN : INK, 600);
  text(g, "LOCKDOWN", w / 2 + 10, 236, 26, DIM);
  text(g, state == null ? "—" : state.controls.lockdown ?? "none", w / 2 + 10, 284, 40, state?.controls.lockdown ? DOWN : INK, 600);
  text(g, "STATUS", w / 2 + 10, 356, 26, DIM);
  text(g, state == null ? "UNAVAILABLE" : halted ? "HALTED" : "ARMED", w / 2 + 10, 404, 40, state == null ? DIM : halted ? DOWN : UP, 600);
  text(g, `PROTO · ${state == null ? "MODE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM"}`, 34, h - 50, 24, FAINT);
}

export function drawStrip(g: Ctx, w: number, h: number, state: FloorState | null, actions: FloorAction[]) {
  background(g, w, h);
  text(g, "ÚLTIMAS AÇÕES", 30, h / 2 + 10, 28, DIM);
  const recent = actions.slice(-3).reverse();
  recent.forEach((a, i) => {
    text(g, `● ${a.at.slice(11, 16)} ${a.station.slice(0, 6).toUpperCase()} ${a.headline.slice(0, 18)}`, 300 + i * 600, h / 2 + 10, 26, TONE_COLOR[a.tone]);
  });
  if (!recent.length) text(g, "no agent activity yet", 300, h / 2 + 10, 26, FAINT);
  const rank = (stage: string) => STAGES.indexOf(stage as (typeof STAGES)[number]);
  const lead = [...(state?.promotion ?? [])].sort((a, b) => rank(b.stage) - rank(a.stage))[0];
  const tail = state == null ? "" : lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE";
  text(g, tail, w - 30, h / 2 + 10, 26, "#c4ccd3", 500, "right");
}

export function drawMonitorSymbol(g: Ctx, w: number, h: number, market: Shared["market"], s: Sym, color: string, lit: boolean) {
  background(g, w, h, lit);
  const mid = priceOf(market, s), series = market.series[s].slice(-60);
  text(g, `${s}/USD`, 14, 30, 20, DIM);
  text(g, mid != null ? usd(mid) : "no feed", 14, 66, 30, INK, 600);
  if (!spark(g, series, 14, 84, w - 28, h - 100, color, 3)) { g.fillStyle = "#30363c"; g.fillRect(14, h - 24, w - 28, 2); }
}

export function drawMonitorLog(g: Ctx, w: number, h: number, recent: FloorAction[], lit: boolean) {
  background(g, w, h, lit);
  text(g, "JOURNAL", 14, 30, 20, DIM);
  const last = recent.slice(-4).reverse();
  last.forEach((a, i) => text(g, `${a.at.slice(11, 19)} ${a.headline.slice(0, 16)}`, 14, 60 + i * 26, 17, TONE_COLOR[a.tone]));
  if (!last.length) text(g, "no events yet", 14, 62, 18, FAINT);
}

export function drawPlate(g: Ctx, w: number, h: number, label: string, role: string, action: FloorAction | undefined, busy: boolean) {
  g.clearRect(0, 0, w, h);
  const color = TONE_COLOR[action ? action.tone : "idle"];
  g.fillStyle = "rgba(14,16,19,0.9)";
  g.beginPath(); g.roundRect(4, 4, w - 8, h - 8, 18); g.fill();
  g.lineWidth = busy ? 5 : 2.5; g.strokeStyle = busy ? color : "#4a5057"; g.stroke();
  g.strokeStyle = "#bfc5ca"; g.lineWidth = 3;
  g.beginPath(); g.roundRect(26, 30, 72, 72, 12); g.stroke();
  g.strokeStyle = busy ? color : "#bfc5ca"; g.lineWidth = 4;
  g.beginPath(); g.moveTo(40, 88); g.lineTo(56, 64); g.lineTo(68, 76); g.lineTo(84, 48); g.stroke();
  text(g, label, 120, 52, 34, INK, 600);
  text(g, role + (action?.symbol ? ` · ${action.symbol}` : ""), 120, 84, 22, DIM);
  const headline = action?.headline ?? "waiting for work";
  text(g, headline.length > 30 ? headline.slice(0, 29) + "…" : headline, 120, 116, 26, INK);
  g.fillStyle = busy ? color : "#4d555d";
  g.beginPath(); g.arc(w - 30, 30, 9, 0, Math.PI * 2); g.fill();
}

/** A night skyline for the windows: decoration, drawn once. */
export function drawCity(g: Ctx, w: number, h: number) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#050608"); sky.addColorStop(0.65, "#15171b"); sky.addColorStop(1, "#2a2a2c");
  g.fillStyle = sky; g.fillRect(0, 0, w, h);
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const base = h * 0.82;
  for (const [count, minH, maxH, color, lit] of [[70, 0.2, 0.6, "#20252c", 0.18], [55, 0.15, 0.5, "#151a20", 0.28], [40, 0.1, 0.35, "#0d1116", 0.36]] as const) {
    for (let i = 0; i < count; i++) {
      const bw = 24 + rnd() * 70, bh = (minH + rnd() * (maxH - minH)) * h, x = rnd() * w;
      g.fillStyle = color; g.fillRect(x, base - bh, bw, bh);
      for (let wy = base - bh + 8; wy < base - 6; wy += 13) {
        for (let wx = x + 5; wx < x + bw - 6; wx += 10) {
          if (rnd() < lit) { g.fillStyle = rnd() > 0.55 ? "rgba(214,207,193,0.75)" : "rgba(195,200,204,0.6)"; g.fillRect(wx, wy, 4, 6); }
        }
      }
    }
  }
  g.fillStyle = "#0b0d10"; g.fillRect(0, base, w, h - base);
}

/** Polished dark stone with faint tile joints. */
export function drawFloor(g: Ctx, w: number, h: number) {
  g.fillStyle = "#1b1d20"; g.fillRect(0, 0, w, h);
  let seed = 3;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let i = 0; i < 2500; i++) { g.fillStyle = `rgba(255,255,255,${rnd() * 0.025})`; g.fillRect(rnd() * w, rnd() * h, 2 + rnd() * 10, 1 + rnd() * 3); }
  g.strokeStyle = "rgba(0,0,0,0.55)"; g.lineWidth = 3;
  for (let i = 0; i <= 4; i++) {
    g.beginPath(); g.moveTo((i * w) / 4, 0); g.lineTo((i * w) / 4, h); g.stroke();
    g.beginPath(); g.moveTo(0, (i * h) / 4); g.lineTo(w, (i * h) / 4); g.stroke();
  }
}
