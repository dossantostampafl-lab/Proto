// Live screen faces for the 3D room, drawn on offscreen canvases. Every value comes from the floor
// state or the public market stream; a missing value is drawn as "—" or "no feed", never estimated.
import { type Sym } from "../../api";
import type { Shared } from "../../main";
import { pct, usd } from "../../ui";
import type { XpLaneState } from "../deskData";
import { changeOf, type FloorAction, type Tone, type FloorState, hms, isHalted, priceOf, STAGES, TONE_COLOR, TONE_LABEL } from "../floorModel";

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

/** The LED ticker: one long line that scrolls under the wall. Only journal actions and labels. */
export function drawTicker(g: Ctx, w: number, h: number, state: FloorState | null, actions: FloorAction[], marketLive: boolean) {
  g.fillStyle = "#050607"; g.fillRect(0, 0, w, h);
  // LED dot grid
  g.fillStyle = "rgba(255,255,255,0.035)";
  for (let x = 2; x < w; x += 6) for (let y = 2; y < h; y += 6) g.fillRect(x, y, 2, 2);
  const y = h / 2 + 11, size = 30;
  let x = 24;
  const put = (s: string, color: string, weight = 600) => { text(g, s, x, y, size, color, weight); x += g.measureText(s).width + 36; };
  put("ÚLTIMAS AÇÕES", "#f0c27a");
  const recent = actions.slice(-6).reverse();
  for (const a of recent) put(`● ${hms(a.at).slice(0, 5)} ${a.station.toUpperCase()} ${a.headline.slice(0, 34)}`, TONE_COLOR[a.tone], 500);
  if (!recent.length) put("no agent activity yet", FAINT, 500);
  put(state == null ? "FLOOR STATE UNAVAILABLE" : state.data_mode === "TESTNET" ? "TESTNET · NO REAL MONEY" : "PAPER / SIM", state == null ? DIM : "#c4ccd3");
  put(marketLive ? "MARKET LIVE PUBLIC" : "MARKET LAST KNOWN / REST", marketLive ? UP : DIM);
  const rank = (stage: string) => STAGES.indexOf(stage as (typeof STAGES)[number]);
  const lead = [...(state?.promotion ?? [])].sort((p, q) => rank(q.stage) - rank(p.stage))[0];
  if (state != null) put(lead ? `${lead.stage} ${lead.clean_days.toFixed(1)}d${lead.next_stage ? ` → ${lead.next_stage}` : ""}` : "NO SCOPE", "#c4ccd3", 500);
}

/** A desk's main monitor: the station, its live headline and the tone in words. */
export function drawDeskScreen(g: Ctx, w: number, h: number, label: string, role: string, action: FloorAction | undefined, busy: boolean) {
  background(g, w, h, busy);
  const tone = action ? action.tone : "idle";
  const color = TONE_COLOR[tone];
  g.fillStyle = color; g.fillRect(0, 0, w, 8);
  text(g, label, 18, 44, 26, INK, 700);
  text(g, role, 18, 74, 18, DIM);
  const headline = action?.headline ?? "waiting for work";
  wrap(g, headline, 18, 116, w - 36, 24, 2, INK);
  g.fillStyle = color; g.beginPath(); g.arc(28, h - 30, 9, 0, Math.PI * 2); g.fill();
  text(g, `${TONE_LABEL[tone].toUpperCase()}${action ? ` · ${hms(action.at)}` : ""}${action?.symbol ? ` · ${action.symbol}` : ""}`, 46, h - 22, 19, color, 600);
}

/** The XP / B3 lane desk: its state in words, no number that was not measured. */
export function drawXpScreen(g: Ctx, w: number, h: number, lane: XpLaneState | null) {
  background(g, w, h, lane === "live");
  const color = XP_COLOR[lane ?? "unknown"];
  g.fillStyle = color; g.fillRect(0, 0, w, 8);
  text(g, "XP / B3", 18, 44, 26, INK, 700);
  text(g, "DEMO ACCOUNT · TESTNET", 18, 74, 18, DIM);
  text(g, XP_LABEL[lane ?? "unknown"], 18, 124, 34, color, 700);
  text(g, "NO REAL MONEY", 18, h - 22, 19, FAINT, 600);
}

export const XP_LABEL: Record<XpLaneState, string> = { unknown: "UNKNOWN", "not-configured": "NOT CONFIGURED", "off-hours": "OFF HOURS", unavailable: "UNAVAILABLE", live: "LIVE", halted: "HALTED" };
export const XP_COLOR: Record<XpLaneState, string> = { unknown: TONE_COLOR.idle, "not-configured": TONE_COLOR.idle, "off-hours": TONE_COLOR.idle, unavailable: TONE_COLOR.warn, live: TONE_COLOR.ok, halted: TONE_COLOR.alert };

function wrap(g: Ctx, s: string, x: number, y: number, maxW: number, size: number, lines: number, color: string) {
  font(g, size, 500); g.fillStyle = color; g.textAlign = "left";
  const words = s.split(/\s+/);
  let line = "", row = 0;
  for (let i = 0; i < words.length; i++) {
    const next = line ? `${line} ${words[i]}` : words[i];
    if (g.measureText(next).width > maxW && line) {
      if (row === lines - 1) { g.fillText(`${line}…`, x, y + row * (size + 6)); return; }
      g.fillText(line, x, y + row * (size + 6)); row++; line = words[i];
    } else line = next;
  }
  if (line) g.fillText(line, x, y + row * (size + 6));
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
  last.forEach((a, i) => text(g, `${hms(a.at)} ${a.headline.slice(0, 16)}`, 14, 60 + i * 26, 17, TONE_COLOR[a.tone]));
  if (!last.length) text(g, "no events yet", 14, 62, 18, FAINT);
}

/** The floating name plate over a desk: name, tone in words, and a light in the tone colour. */
export function drawPlate(g: Ctx, w: number, h: number, label: string, tone: Tone | "idle", busy: boolean, selected: boolean) {
  g.clearRect(0, 0, w, h);
  const color = TONE_COLOR[tone];
  g.fillStyle = "rgba(12,14,17,0.86)";
  g.beginPath(); g.roundRect(4, 4, w - 8, h - 8, 22); g.fill();
  g.lineWidth = selected ? 7 : busy ? 5 : 2.5; g.strokeStyle = selected ? "#ffffff" : busy ? color : "#4a5057"; g.stroke();
  g.fillStyle = color;
  g.beginPath(); g.arc(40, h / 2, 15, 0, Math.PI * 2); g.fill();
  text(g, label, 68, h / 2 - 2, 42, INK, 700);
  text(g, TONE_LABEL[tone].toUpperCase(), 68, h / 2 + 38, 30, color, 600);
}

/** A soft round contact shadow, baked once and laid under desks and people. */
export function drawBlob(g: Ctx, w: number, h: number) {
  const grad = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.45, "rgba(255,255,255,0.55)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.clearRect(0, 0, w, h); g.fillStyle = grad; g.fillRect(0, 0, w, h);
}

/** An agent's name tag: code and virtual equity, and what that capital is. */
export function drawTag(g: Ctx, w: number, h: number, code: string, equity: string, capital: string) {
  g.clearRect(0, 0, w, h);
  g.fillStyle = "rgba(14,16,19,0.84)";
  g.beginPath(); g.roundRect(3, 3, w - 6, h - 6, 16); g.fill();
  g.strokeStyle = "rgba(240,194,122,0.75)"; g.lineWidth = 3; g.stroke();
  text(g, `${code} · ${equity}`, w / 2, h / 2 - 4, 34, INK, 700, "center");
  text(g, capital, w / 2, h / 2 + 30, 22, "#f0c27a", 600, "center");
}

/** A roster agent's own monitor: code, slot and virtual equity, from /v1/agents/roster only. */
export function drawAgentScreen(g: Ctx, w: number, h: number, code: string | null, slot: string, equity: string, capital: string) {
  background(g, w, h, code !== null);
  g.fillStyle = "#f0c27a"; g.fillRect(0, 0, w, 8);
  text(g, code ?? "SEAT EMPTY", 18, 46, 28, INK, 700);
  text(g, slot, 18, 80, 20, DIM);
  text(g, code ? equity : "—", 18, 150, 44, INK, 600);
  text(g, capital, 18, h - 24, 19, "#f0c27a", 600);
}

/** A dusk skyline for the windows: decoration, drawn once. */
export function drawCity(g: Ctx, w: number, h: number) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#1a2233"); sky.addColorStop(0.45, "#3b3a48"); sky.addColorStop(0.72, "#a5714f"); sky.addColorStop(0.82, "#d49a66");
  g.fillStyle = sky; g.fillRect(0, 0, w, h);
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const base = h * 0.86;
  for (const [count, minH, maxH, color, lit] of [[60, 0.18, 0.5, "#3a3d48", 0.12], [50, 0.14, 0.42, "#2a2c35", 0.22], [36, 0.08, 0.3, "#1c1e25", 0.3]] as const) {
    for (let i = 0; i < count; i++) {
      const bw = 24 + rnd() * 70, bh = (minH + rnd() * (maxH - minH)) * h, x = rnd() * w;
      g.fillStyle = color; g.fillRect(x, base - bh, bw, bh);
      for (let wy = base - bh + 8; wy < base - 6; wy += 13) {
        for (let wx = x + 5; wx < x + bw - 6; wx += 10) {
          if (rnd() < lit) { g.fillStyle = rnd() > 0.5 ? "rgba(255,214,160,0.8)" : "rgba(220,226,232,0.6)"; g.fillRect(wx, wy, 4, 6); }
        }
      }
    }
  }
  g.fillStyle = "#15161b"; g.fillRect(0, base, w, h - base);
}
