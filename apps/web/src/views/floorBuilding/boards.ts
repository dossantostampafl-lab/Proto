// Canvas faces of the building view: the PROTO logo, the roof sign and the two wall boards. The
// boards draw only what they are handed (real roster and ledger values); a missing value is "—", and
// a curve is drawn only from points actually read during this session.
export type Ctx = OffscreenCanvasRenderingContext2D;
export type Feed = "live" | "stale" | "unavailable" | "loading";

const SANS = '"Chakra Petch", ui-sans-serif, system-ui, sans-serif';
const MONO = '"JetBrains Mono", ui-monospace, monospace';
export const UP = "#4ade80", DOWN = "#f87171", AMBER = "#ffb21e", INK = "#eef2f8", DIM = "#93a0b8";

export function canvas(w: number, h: number): { c: OffscreenCanvas; g: Ctx } {
  const c = new OffscreenCanvas(w, h);
  const g = c.getContext("2d");
  if (!g) throw new Error("2d canvas unavailable");
  return { c, g };
}

const text = (g: Ctx, s: string, x: number, y: number, size: number, color = INK, weight = 600, align: CanvasTextAlign = "left", family = SANS) => {
  g.font = `${weight} ${size}px ${family}`; g.fillStyle = color; g.textAlign = align; g.fillText(s, x, y);
};

function panel(g: Ctx, w: number, h: number, edge: string) {
  g.fillStyle = "#0c1222"; g.fillRect(0, 0, w, h);
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(255,255,255,0.05)"); grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad; g.fillRect(0, 0, w, h);
  g.strokeStyle = edge; g.lineWidth = 10; g.strokeRect(5, 5, w - 10, h - 10);
}

const FEED_TEXT: Record<Feed, [string, string]> = {
  live: ["AO VIVO", UP], stale: ["DESATUALIZADO", AMBER], unavailable: ["INDISPONÍVEL", DOWN], loading: ["CARREGANDO", DIM],
};
function feedChip(g: Ctx, feed: Feed, x: number, y: number) {
  const [label, color] = FEED_TEXT[feed];
  g.font = `700 26px ${MONO}`;
  const w = g.measureText(label).width + 28;
  g.fillStyle = "rgba(0,0,0,0.35)"; g.fillRect(x - w, y - 30, w, 40);
  g.strokeStyle = color; g.lineWidth = 3; g.strokeRect(x - w, y - 30, w, 40);
  text(g, label, x - w / 2, y - 1, 26, color, 700, "center", MONO);
}

export function drawLogo(g: Ctx, w: number, h: number) {
  g.clearRect(0, 0, w, h);
  g.fillStyle = "#121a2e"; g.fillRect(0, 0, w, h);
  // The mark: a hexagon outline with a P inside, then the word.
  const cx = h * 0.5, cy = h * 0.5, r = h * 0.32;
  g.beginPath();
  for (let i = 0; i < 6; i++) { const a = Math.PI / 6 + (i * Math.PI) / 3; g.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a)); }
  g.closePath(); g.strokeStyle = AMBER; g.lineWidth = h * 0.05; g.stroke();
  text(g, "P", cx, cy + h * 0.13, h * 0.38, AMBER, 700, "center");
  text(g, "PROTO", h * 0.95, cy + h * 0.06, h * 0.3, INK, 700);
  text(g, "TRADING FLOOR", h * 0.98, cy + h * 0.24, h * 0.09, DIM, 600, "left", MONO);
}

export function drawSign(g: Ctx, w: number, h: number) {
  g.clearRect(0, 0, w, h);
  g.fillStyle = "#0a0f1d"; g.fillRect(0, 0, w, h);
  g.shadowColor = AMBER; g.shadowBlur = h * 0.18;
  text(g, "PROTO", w / 2, h * 0.73, h * 0.62, AMBER, 700, "center");
  g.shadowBlur = 0;
}

/** Soft radial spot used for group glows and fake contact shadows. */
export function radial(size: number, inner: string, outer: string) {
  const { c, g } = canvas(size, size);
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grad.addColorStop(0, inner); grad.addColorStop(1, outer);
  g.fillStyle = grad; g.fillRect(0, 0, size, size);
  return c;
}

export type ResultBoard = {
  feed: Feed; floor: string; capital: string;
  pnl: number | null; pnlText: string; agents: number;
  history: number[]; accountDay: string | null;
};

export function drawResult(g: Ctx, w: number, h: number, b: ResultBoard) {
  panel(g, w, h, "#2a3a5c");
  text(g, "RESULTADO", 40, 76, 50, INK, 700);
  text(g, `${b.floor} · P&L acumulado dos agentes · ${b.capital}`, 40, 116, 24, DIM, 500, "left", MONO);
  feedChip(g, b.feed, w - 36, 74);
  const color = b.pnl === null ? DIM : b.pnl > 0 ? UP : b.pnl < 0 ? DOWN : INK;
  text(g, b.pnlText, 40, 222, 92, color, 700);
  text(g, b.pnl === null ? (b.agents ? "sem avaliação completa: valor não medido" : "nenhum agente neste andar") : `${b.agents} agente${b.agents === 1 ? "" : "s"} ativo${b.agents === 1 ? "" : "s"}`, 40, 262, 24, DIM, 500, "left", MONO);
  if (b.accountDay) text(g, b.accountDay, w - 40, 262, 24, DIM, 500, "right", MONO);
  // The curve: only the values read since this page was opened.
  const x = 40, y = 300, cw = w - 80, ch = h - 360;
  g.strokeStyle = "rgba(147,160,184,0.18)"; g.lineWidth = 2;
  for (let i = 0; i <= 3; i++) { g.beginPath(); g.moveTo(x, y + (i * ch) / 3); g.lineTo(x + cw, y + (i * ch) / 3); g.stroke(); }
  const v = b.history;
  if (v.length >= 2) {
    const lo = Math.min(...v, 0), hi = Math.max(...v, 0), span = Math.max(hi - lo, 0.01);
    const py = (n: number) => y + ch - ((n - lo) / span) * ch;
    g.setLineDash([8, 8]); g.strokeStyle = "rgba(238,242,248,0.25)"; g.beginPath(); g.moveTo(x, py(0)); g.lineTo(x + cw, py(0)); g.stroke(); g.setLineDash([]);
    g.beginPath();
    v.forEach((n, i) => { const px = x + (i / (v.length - 1)) * cw; if (i) g.lineTo(px, py(n)); else g.moveTo(px, py(n)); });
    g.strokeStyle = color; g.lineWidth = 6; g.lineJoin = "round"; g.stroke();
  } else text(g, "aguardando leituras", x + cw / 2, y + ch / 2 + 10, 28, DIM, 500, "center", MONO);
  text(g, `${v.length} leitura${v.length === 1 ? "" : "s"} · desde que você abriu`, 40, h - 22, 22, DIM, 500, "left", MONO);
}

export type LimitBoard = {
  feed: Feed; quote: string; measured: boolean;
  daily: { used: number | null; text: string }; drawdown: { used: number | null; text: string };
  lockdown: string | null; halted: string | null;
};

function bar(g: Ctx, label: string, used: number | null, usedText: string, limit: number, limitText: string, x: number, y: number, w: number) {
  text(g, label, x, y, 30, INK, 700);
  text(g, `${usedText} / ${limitText}`, x + w, y, 30, used === null ? DIM : used >= limit ? DOWN : INK, 700, "right", MONO);
  g.fillStyle = "rgba(255,255,255,0.08)"; g.fillRect(x, y + 18, w, 26);
  if (used !== null) {
    const f = Math.min(1, used / limit);
    g.fillStyle = f >= 1 ? DOWN : f >= 0.6 ? AMBER : "#f43f5e";
    g.fillRect(x, y + 18, Math.max(4, f * w), 26);
  }
}

export function drawLimits(g: Ctx, w: number, h: number, b: LimitBoard) {
  panel(g, w, h, "#7f1d1d");
  g.fillStyle = "#b91c1c"; g.fillRect(10, 10, w - 20, 92);
  text(g, "DRAWDOWN / LIMITE", 36, 74, 44, "#fff1f1", 700);
  feedChip(g, b.feed, w - 30, 74);
  text(g, `conta ${b.quote} · ledger do OMS${b.measured ? "" : " · não medido"}`, 36, 144, 24, DIM, 500, "left", MONO);
  bar(g, "Perda do dia", b.daily.used, b.daily.text, 0.01, "1%", 36, 206, w - 72);
  bar(g, "Drawdown", b.drawdown.used, b.drawdown.text, 0.05, "5%", 36, 300, w - 72);
  text(g, `Lockdown: ${b.lockdown ?? "—"}`, 36, h - 70, 26, b.lockdown && b.lockdown !== "nenhum" ? DOWN : INK, 600, "left", MONO);
  text(g, `Venues parados: ${b.halted ?? "—"}`, 36, h - 30, 26, b.halted && b.halted !== "nenhum" ? DOWN : INK, 600, "left", MONO);
}
