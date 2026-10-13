import { createContext, type ReactNode, useContext } from "react";

// ---------- formatting ----------
const fmt = (v: number, d: number) => {
  const text = new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
  return /[1-9]/.test(text) ? text : text.replace("-", ""); // no "-0.00"
};
const ok = (v: number | null | undefined): v is number => v != null && Number.isFinite(v);
export const num = (v: number | null | undefined, d = 2) => (ok(v) ? fmt(v, d) : "—");
export const usd = (v: number | null | undefined) => {
  if (!ok(v)) return "—";
  const text = fmt(Math.abs(v), Math.abs(v) >= 1000 ? 2 : 4);
  return v < 0 && /[1-9]/.test(text) ? `-$${text}` : `$${text}`;
};
export const pct = (v: number | null | undefined, d = 2) => (ok(v) ? `${fmt(v * 100, d)}%` : "—");
export const signed = (v: number | null | undefined, d = 3) => (ok(v) ? `${v >= 0 ? "+" : ""}${fmt(v, d)}` : "—");
export const tone = (v: number | null | undefined) => (!ok(v) || v === 0 ? "" : v > 0 ? "up" : "down");
export const clamp = (v: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));

// ---------- operator commands ----------
export type LogEntry = { at: string; label: string; ok: boolean; detail: string };
export type Commander = { busy: string | null; run: (label: string, path: string, body?: unknown) => Promise<boolean>; log: LogEntry[] };
export const CommandContext = createContext<Commander>({ busy: null, run: async () => false, log: [] });
export const useCommand = () => useContext(CommandContext);

// ---------- primitives ----------
type Source = "live" | "research" | "paper";
const SOURCE_LABEL: Record<Source, string> = { live: "LIVE PUBLIC", research: "SYNTHETIC RESEARCH", paper: "PAPER / SIM" };

export function Tag({ source }: { source: Source }) {
  return <span className={`tag ${source}`}>{SOURCE_LABEL[source]}</span>;
}

export function Panel({ title, source, className = "", children, actions }: { title: string; source?: Source; className?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className={`panel ${className}`}>
      <header><h2>{title}</h2>{source && <Tag source={source} />}{actions}</header>
      {children}
    </section>
  );
}

export function Stat({ label, value, className = "" }: { label: string; value: ReactNode; className?: string }) {
  return <div className="stat"><span>{label}</span><b className={className}>{value}</b></div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Dot({ on, warn }: { on: boolean; warn?: boolean }) {
  return <i className={`dot ${on ? "on" : warn ? "warn" : "off"}`} aria-hidden="true" />;
}

export function Button({ label, path, body, kind = "", disabled, children }: { label: string; path: string; body?: unknown; kind?: "" | "primary" | "danger" | "buy" | "sell"; disabled?: boolean; children: ReactNode }) {
  const { busy, run } = useCommand();
  return (
    <button type="button" className={`btn ${kind}`} disabled={disabled || busy !== null} onClick={() => void run(label, path, body)}>
      {busy === label ? "…" : children}
    </button>
  );
}

export function Spark({ values, height = 48, className = "", grid = false }: { values: number[]; height?: number; className?: string; grid?: boolean }) {
  if (values.length < 2) return <div className="spark empty-spark" style={{ height }}>acquiring signal</div>;
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, 1e-9);
  const y = (v: number) => height - 3 - ((v - lo) / span) * (height - 6);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${y(v)}`);
  const trend = values[values.length - 1] >= values[0] ? "up" : "down";
  const lastY = (y(values[values.length - 1]) / height) * 100;
  return (
    <div className={`spark ${trend} ${className}`} style={{ height }} aria-hidden="true">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id={`fill-${trend}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" className="fill-top" />
            <stop offset="1" className="fill-bottom" />
          </linearGradient>
        </defs>
        {grid && [0.2, 0.4, 0.6, 0.8].map(g => <line key={g} x1="0" x2="100" y1={g * height} y2={g * height} className="gridline" vectorEffect="non-scaling-stroke" />)}
        <polygon points={`0,${height} ${pts.join(" ")} 100,${height}`} fill={`url(#fill-${trend})`} />
        <polyline points={pts.join(" ")} vectorEffect="non-scaling-stroke" />
      </svg>
      <i className="spark-head" style={{ top: `${lastY}%` }} />
      {grid && <span className="spark-range"><b>{num(hi, hi >= 1000 ? 2 : 4)}</b><b>{num(lo, lo >= 1000 ? 2 : 4)}</b></span>}
    </div>
  );
}

export function Meter({ value, label }: { value: number; label: string }) {
  return <div className="meter" role="meter" aria-label={label} aria-valuenow={Math.round(clamp(value) * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${clamp(value) * 100}%` }} /></div>;
}

export type OrbitMarket = { id: string; symbol: string; market: number; model: number; horizon: number; edge: number };

/**
 * Signature element. Outer arc = market-implied probability, inner arc = model probability of the
 * selected market (the gap between them is the edge). Every research market orbits at a radius
 * proportional to its expiry horizon and an angle equal to its model probability.
 */
export function Orbit({ markets, selected }: { markets: OrbitMarket[]; selected: string }) {
  const c = 150, outer = 128, inner = 112;
  const lead = markets.find(m => m.symbol === selected);
  const longest = Math.max(...markets.map(m => m.horizon), 1);
  const arc = (r: number, p: number) => `${clamp(p) * 2 * Math.PI * r} ${2 * Math.PI * r}`;
  return (
    <svg className="orbit" viewBox="0 0 300 300" role="img" aria-label={`Probability orbit: market ${pct(lead?.market, 1)}, model ${pct(lead?.model, 1)}`}>
      <defs>
        <radialGradient id="radar-fade" cx="150" cy="150" r="140" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="currentColor" stopOpacity="0" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0.35" />
        </radialGradient>
      </defs>
      {Array.from({ length: 60 }, (_, i) => {
        const a = (i / 60) * Math.PI * 2, r1 = 140, r2 = i % 5 ? 136 : 131;
        return <line key={i} className="tick" x1={c + Math.cos(a) * r1} y1={c + Math.sin(a) * r1} x2={c + Math.cos(a) * r2} y2={c + Math.sin(a) * r2} />;
      })}
      <g className="radar"><path d={`M${c} ${c} L${c} ${c - 138} A138 138 0 0 1 ${c + 138 * Math.sin(Math.PI / 5)} ${c - 138 * Math.cos(Math.PI / 5)} Z`} fill="url(#radar-fade)" /></g>
      {[0.42, 0.62, 0.82].map(r => <circle key={r} cx={c} cy={c} r={r * outer} className="ring" />)}
      <circle cx={c} cy={c} r={outer} className="track" />
      <circle cx={c} cy={c} r={inner} className="track" />
      {lead && <circle cx={c} cy={c} r={outer} className="arc market" strokeDasharray={arc(outer, lead.market)} transform={`rotate(-90 ${c} ${c})`} />}
      {lead && <circle cx={c} cy={c} r={inner} className="arc model" strokeDasharray={arc(inner, lead.model)} transform={`rotate(-90 ${c} ${c})`} />}
      {markets.map(m => {
        const angle = clamp(m.model) * Math.PI * 2 - Math.PI / 2;
        const radius = 40 + (m.horizon / longest) * 56;
        const x = c + Math.cos(angle) * radius, y = c + Math.sin(angle) * radius;
        const active = m.symbol === selected;
        return (
          <g key={m.id} className={`sat ${m.edge >= 0 ? "up" : "down"} ${active ? "active" : ""}`}>
            <line x1={c} y1={c} x2={x} y2={y} />
            <circle cx={x} cy={y} r={active ? 5 : 3.5} />
            <text x={x} y={y - 9}>{m.symbol}</text>
          </g>
        );
      })}
      <circle cx={c} cy={c} r={30} className="core" />
      <text x={c} y={c + 2} className={`core-value ${tone(lead?.edge)}`}>{lead ? signed(lead.edge * 100, 1) : "—"}</text>
      <text x={c} y={c + 14} className="core-label">EDGE %</text>
    </svg>
  );
}
