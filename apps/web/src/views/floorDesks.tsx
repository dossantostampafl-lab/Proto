// The desks of the trading floor, opened one at a time in a drawer. Every value comes from an
// existing read-only API surface and is labelled with its freshness (live / stale / unavailable)
// and its data mode; a missing value is "—", never an estimate. The drawer polls only while it is
// open and the tab is visible. Operator actions reuse the operator token of the System view and
// are disabled, with the reason, without it. Nothing here can release a halt or raise a limit.
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { get, operatorToken } from "../api";
import { Tag, useCommand } from "../ui";
import { type FloorAction, type FloorState, STATIONS, type StationId, type Tone, TONE_COLOR } from "./floorModel";

// ---------- selection kept in the URL hash: #floor/<desk> ----------
const HASH = /^#floor\/([a-z]+)$/;
export const deskFromHash = (hash: string): StationId | null => {
  const id = HASH.exec(hash)?.[1];
  return STATIONS.find(s => s.id === id)?.id ?? null;
};

export function useDeskSelection() {
  const [selected, setSelected] = useState<StationId | null>(() => deskFromHash(window.location.hash));
  useEffect(() => {
    const sync = () => setSelected(deskFromHash(window.location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const select = useCallback((id: StationId | null) => {
    setSelected(id);
    const url = `${window.location.pathname}${window.location.search}${id ? `#floor/${id}` : ""}`;
    try { window.history.replaceState(null, "", url); } catch { /* history blocked: the drawer still works */ }
  }, []);
  return { selected, select };
}

// ---------- polling that stops with the drawer and with a hidden tab ----------
const POLL_MS = 4000;
type Feed<T> = { data: T | null; status: number | null; at: number | null; ms: number; refresh: () => void };

function useDeskFeed<T>(path: string, ms = POLL_MS): Feed<T> {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      const result = await get<T>(path);
      if (!live) return;
      setStatus(result.status);
      if (result.ok) { setData(result.data); setAt(Date.now()); }
    };
    void load();
    const id = window.setInterval(() => void load(), ms);
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { live = false; window.clearInterval(id); document.removeEventListener("visibilitychange", visible); };
  }, [path, ms, tick]);
  const refresh = useCallback(() => setTick(t => t + 1), []);
  return { data, status, at, ms, refresh };
}

const clock = (value: string | number | null | undefined) => {
  if (value == null) return "—";
  const text = typeof value === "number" ? new Date(value).toISOString() : value;
  return text.slice(11, 19);
};

/** LIVE while reads succeed, STALE when the last good value is old, UNAVAILABLE when there is none. */
function Freshness({ feed }: { feed: Feed<unknown> }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 2000); return () => window.clearInterval(id); }, []);
  const { data, status, at, ms } = feed;
  if (data == null) {
    return <span className="fl-fresh unavailable">{status == null ? "LOADING…" : `UNAVAILABLE · ${status ? `HTTP ${status}` : "network"}`}</span>;
  }
  const old = at != null && now - at > ms * 3;
  return old
    ? <span className="fl-fresh stale">STALE · last good read {clock(at)} UTC</span>
    : <span className="fl-fresh live">LIVE · updated {clock(at)} UTC</span>;
}

function Section({ title, feed, children }: { title: string; feed?: Feed<unknown>; children: ReactNode }) {
  return (
    <section className="fl-sec">
      <header><h3>{title}</h3>{feed && <Freshness feed={feed} />}</header>
      {children}
    </section>
  );
}

const Row = ({ label, children, cls = "" }: { label: string; children: ReactNode; cls?: string }) => (
  <li><span>{label}</span><b className={cls}>{children}</b></li>
);
const Rows = ({ children }: { children: ReactNode }) => <ul className="fl-rows">{children}</ul>;
/** A decimal string without its trailing zeros ("1.000000000000000000" -> "1", "0E-18" -> "0"). */
const amt = (v: string | null | undefined) => {
  if (v == null || v === "") return "—";
  if (Number(v) === 0) return "0";
  return /^-?\d+\.\d+$/.test(v) ? v.replace(/0+$/, "").replace(/\.$/, "") : v;
};
/** A drill step detail as text: empty lists read "none" and null reads "—", never a bare label. */
const stepValue = (v: unknown) => (v == null ? "—" : Array.isArray(v) ? (v.length ? v.join(", ") : "none") : String(v));
const none = (v: unknown) => (v == null || v === "" ? "—" : String(v));
const short = (id: string | undefined) => (id ? id.replace(/^[A-Z_0-9]+:/, "") : "—");
const NoData = ({ children }: { children: ReactNode }) => <p className="empty">{children}</p>;

// ---------- wire types (only what the panels read) ----------
type Signal = { signal_id: string; source: string; strategy_id: string; instrument_id: string; direction: string; strength: number; timeframe: string; observed_at: string };
type Decision = {
  decision_id: string; decided_at: string; instrument_id: string; outcome: string; abstain_reason: string | null;
  fusion?: { direction?: string; score?: number; reason?: string };
  candidate?: { side?: string; quantity?: string; limit_price?: string; order_type?: string } | null;
  sizing?: Record<string, string>;
  review?: { verdict?: string; size_factor?: number; rationale?: string; reviewer?: string };
};
type AgentStatus = { enabled: boolean; reviewer: string; model: string | null; stage: string; buffered_signals: number; decisions: number; outcomes: Record<string, number>; journal_failures: number };
type StrategyStatus = { enabled: boolean; running: boolean; strategy_id: string; market_source: string; books: Record<string, { bars: number; needs_bars: number; last_signal_at: string | null }>; ticks: number; signals: number };
type RiskStatus = {
  constitution: Record<string, string>; policy: Record<string, string | number>;
  controls: { global_kill?: boolean; killed_venues?: string[]; killed_strategies?: string[]; lockdown?: string | null; runtime_kill_switch?: string };
};
type RiskDecision = { intent_id: string; instrument_id: string; strategy_id: string; approved: boolean; rejections: string[]; checks: Record<string, string>; breach: string | null };
type Watchdog = { active: boolean; last_beat: string | null; coverage_hours: number; required_hours: number; beats: number; last_report: { trips: string[]; checks: Record<string, string>; api_halted: boolean; executor_halted: boolean } | null; recent_trips: { at: string; reasons: string[] }[] };
type Order = { client_order_id: string; instrument_id: string; side: string; quantity: string; limit_price: string | null; status: string; filled_quantity: string; average_price: string | null; fee: string; reason: string | null };
type Ledger = {
  account_id: string; venue: string; mode: string;
  balances: Record<string, { total: string; reserved: string; available: string }>;
  accounts: Record<string, { equity: string; gross_exposure: string }>;
  unavailable?: boolean;
  xp_b3?: { account_id?: string; balances?: Record<string, { total: string }>; venue_halted?: boolean | null; unavailable?: boolean };
};
type Drill = { verdict: string; instrument_id?: string; started_at?: string; finished_at?: string; venue_environment?: string; steps?: Record<string, unknown>[]; digest?: string };

// ---------- operator actions ----------
function Action({ label, path, body, refresh, kind = "", children }: { label: string; path: string; body?: unknown; refresh: () => void; kind?: "" | "primary"; children: ReactNode }) {
  const { busy, run } = useCommand();
  const token = Boolean(operatorToken());
  return (
    <button type="button" className={`btn ${kind}`} disabled={!token || busy !== null}
      title={token ? undefined : "Set the operator token in the System view to enable"}
      onClick={() => void run(label, path, body).then(refresh)}>
      {busy === label ? "…" : children}
    </button>
  );
}
const TokenHint = () => (operatorToken() ? null : <p className="hint">Actions need the operator token: set it in the System view.</p>);
const ORDER_ID = /^[A-Za-z0-9_-]{1,36}$/;

// ---------- the desks ----------
function TradingViewDesk({ actions }: { actions: FloorAction[] }) {
  const signals = useDeskFeed<{ signals: Signal[] }>("/v1/signals/recent");
  const list = signals.data?.signals ?? [];
  const accepted = actions.filter(a => a.event_type === "TRADINGVIEW_SIGNAL_ACCEPTED").length;
  const bySource = list.reduce<Record<string, number>>((m, s) => ({ ...m, [s.source]: (m[s.source] ?? 0) + 1 }), {});
  return (
    <Section title="Recent signals" feed={signals}>
      <Rows>
        <Row label="In the buffer">{signals.data ? list.length : "—"}</Row>
        <Row label="By source">{Object.keys(bySource).length ? Object.entries(bySource).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</Row>
        <Row label="TradingView alerts accepted since this page opened">{accepted}</Row>
      </Rows>
      {signals.data && !list.length && <NoData>No signal received yet. A TradingView alert or the own strategy starts the chain.</NoData>}
      <ol className="fl-list">
        {list.slice(0, 8).map(s => (
          <li key={s.signal_id}>
            <time>{clock(s.observed_at)}</time><b>{short(s.instrument_id)}</b>
            <span className={s.direction === "LONG" ? "up" : s.direction === "SHORT" ? "down" : ""}>{s.direction} · strength {s.strength.toFixed(2)}</span>
            <small>{s.source} · {s.strategy_id} · {s.timeframe}</small>
          </li>
        ))}
      </ol>
      <p className="hint">The webhook secret and raw payloads are never shown here.</p>
    </Section>
  );
}

function DecisionList({ decisions }: { decisions: Decision[] }) {
  if (!decisions.length) return <NoData>No decision yet.</NoData>;
  return (
    <ol className="fl-list">
      {decisions.slice(0, 6).map(d => (
        <li key={d.decision_id}>
          <time>{clock(d.decided_at)}</time><b>{short(d.instrument_id)}</b>
          <span className={d.outcome === "APPROVED" ? "up" : d.outcome === "ABSTAINED" || d.outcome === "BLOCKED" ? "warn" : ""}>{d.outcome}</span>
          <small>
            {d.fusion ? `${none(d.fusion.direction)} · score ${d.fusion.score?.toFixed(2) ?? "—"} · ${none(d.fusion.reason)}` : "no fusion data"}
            {d.abstain_reason ? ` · abstained: ${d.abstain_reason}` : ""}
            {d.candidate ? ` · ${none(d.candidate.side)} ${none(d.candidate.quantity)} @ ${none(d.candidate.limit_price)}` : ""}
          </small>
        </li>
      ))}
    </ol>
  );
}

function FusionDesk() {
  const status = useDeskFeed<AgentStatus>("/v1/agents/status");
  const decisions = useDeskFeed<{ decisions: Decision[] }>("/v1/agents/decisions/recent");
  const strategy = useDeskFeed<StrategyStatus>("/v1/strategy/status");
  const s = status.data, st = strategy.data;
  return (
    <>
      <Section title="Pipeline" feed={status}>
        <Rows>
          <Row label="Agents" cls={s?.enabled ? "up" : ""}>{s ? (s.enabled ? "on" : "off") : "—"}</Row>
          <Row label="Stage">{none(s?.stage)}</Row>
          <Row label="Decisions">{none(s?.decisions)}</Row>
          <Row label="Outcomes">{s && Object.keys(s.outcomes).length ? Object.entries(s.outcomes).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</Row>
          <Row label="Buffered signals">{none(s?.buffered_signals)}</Row>
          <Row label="Journal failures" cls={s?.journal_failures ? "down" : ""}>{none(s?.journal_failures)}</Row>
        </Rows>
      </Section>
      <Section title="Latest decisions" feed={decisions}>
        {decisions.data ? <DecisionList decisions={decisions.data.decisions} /> : <NoData>Decisions unavailable.</NoData>}
      </Section>
      <Section title="Own strategy" feed={strategy}>
        {st ? (
          <>
            <Rows>
              <Row label="Strategy">{st.strategy_id}</Row>
              <Row label="State" cls={st.running ? "up" : "warn"}>{st.enabled ? (st.running ? "running" : "stopped") : "disabled"}</Row>
              <Row label="Market source">{st.market_source}</Row>
              <Row label="Ticks / signals">{st.ticks} / {st.signals}</Row>
            </Rows>
            <ul className="fl-bars">
              {Object.entries(st.books).map(([id, b]) => (
                <li key={id}>
                  <span>{short(id)}</span>
                  <div role="progressbar" aria-label={`${short(id)} warm-up`} aria-valuemin={0} aria-valuemax={b.needs_bars} aria-valuenow={Math.min(b.bars, b.needs_bars)}><i style={{ width: `${Math.min(1, b.bars / Math.max(1, b.needs_bars)) * 100}%` }} /></div>
                  <small>{b.bars}/{b.needs_bars} bars · last signal {clock(b.last_signal_at)}</small>
                </li>
              ))}
            </ul>
            <p className="hint">The strategy emits opinions only; it has no order authority.</p>
          </>
        ) : <NoData>Strategy status unavailable.</NoData>}
      </Section>
    </>
  );
}

function ReviewerDesk() {
  const status = useDeskFeed<AgentStatus>("/v1/agents/status");
  const decisions = useDeskFeed<{ decisions: Decision[] }>("/v1/agents/decisions/recent");
  const s = status.data;
  const reviewed = (decisions.data?.decisions ?? []).filter(d => d.review).slice(0, 6);
  return (
    <>
      <Section title="Reviewer" feed={status}>
        <Rows>
          <Row label="Mode">{none(s?.reviewer)}</Row>
          <Row label="Model">{s ? (s.model ?? "none (deterministic)") : "—"}</Row>
          <Row label="Stage">{none(s?.stage)}</Row>
        </Rows>
        <p className="hint">The reviewer can only shrink or block a candidate, never enlarge it or place an order.</p>
      </Section>
      <Section title="Latest reviews" feed={decisions}>
        {decisions.data == null ? <NoData>Reviews unavailable.</NoData> : !reviewed.length ? <NoData>No review yet.</NoData> : (
          <ol className="fl-list">
            {reviewed.map(d => (
              <li key={d.decision_id}>
                <time>{clock(d.decided_at)}</time><b>{short(d.instrument_id)}</b>
                <span className={d.review?.verdict === "APPROVE" ? "up" : "warn"}>{none(d.review?.verdict)} · size ×{d.review?.size_factor ?? "—"}</span>
                <small>{none(d.review?.rationale)}</small>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </>
  );
}

function RiskDesk() {
  const risk = useDeskFeed<RiskStatus>("/v1/risk/status");
  const decisions = useDeskFeed<{ decisions: RiskDecision[] }>("/v1/risk/decisions/recent");
  const watchdog = useDeskFeed<Watchdog>("/v1/watchdog/status");
  const c = risk.data?.controls, k = risk.data?.constitution, w = watchdog.data;
  const checks = Object.entries(w?.last_report?.checks ?? {});
  return (
    <>
      <Section title="Controls" feed={risk}>
        <Rows>
          <Row label="Global kill switch" cls={c?.global_kill ? "down" : "up"}>{c ? (c.global_kill ? "ON" : "armed") : "—"}</Row>
          <Row label="Runtime kill switch" cls={c && c.runtime_kill_switch !== "ARMED" ? "down" : ""}>{none(c?.runtime_kill_switch)}</Row>
          <Row label="Halted venues" cls={c?.killed_venues?.length ? "down" : ""}>{c ? c.killed_venues?.join(", ") || "none" : "—"}</Row>
          <Row label="Halted strategies" cls={c?.killed_strategies?.length ? "down" : ""}>{c ? c.killed_strategies?.join(", ") || "none" : "—"}</Row>
          <Row label="Lockdown" cls={c?.lockdown ? "down" : ""}>{c ? c.lockdown ?? "none" : "—"}</Row>
        </Rows>
        <p className="hint">Releasing a halt or a lockdown stays a deliberate operator action outside this view.</p>
      </Section>
      <Section title="Constitutional limits" feed={risk}>
        <Rows>
          <Row label="Reference capital">{none(k?.reference_capital)}</Row>
          <Row label="Risk per position">{k ? `${(Number(k.max_risk_per_position) * 100).toFixed(2)}%` : "—"}</Row>
          <Row label="Daily loss">{k ? `${(Number(k.max_daily_loss) * 100).toFixed(2)}%` : "—"}</Row>
          <Row label="Drawdown">{k ? `${(Number(k.max_drawdown) * 100).toFixed(2)}%` : "—"}</Row>
          <Row label="Gross leverage">{k ? `${k.max_gross_leverage}x` : "—"}</Row>
          <Row label="Quote max age">{risk.data ? `${risk.data.policy.max_quote_age_seconds} s` : "—"}</Row>
          <Row label="Permit TTL">{risk.data ? `${risk.data.policy.permit_ttl_seconds} s` : "—"}</Row>
        </Rows>
      </Section>
      <Section title="Latest permits and rejections" feed={decisions}>
        {decisions.data == null ? <NoData>Risk decisions unavailable.</NoData> : !decisions.data.decisions.length ? <NoData>No risk decision yet.</NoData> : (
          <ol className="fl-list">
            {decisions.data.decisions.slice(0, 6).map(d => (
              <li key={d.intent_id}>
                <b>{short(d.instrument_id)}</b>
                <span className={d.approved ? "up" : "down"}>{d.approved ? "permit issued" : "rejected"}</span>
                <small>{d.approved ? `daily loss ${none(d.checks.daily_loss)} · drawdown ${none(d.checks.drawdown)}` : d.rejections.join(", ") || "no reason given"}{d.breach ? ` · breach ${d.breach}` : ""}</small>
              </li>
            ))}
          </ol>
        )}
      </Section>
      <Section title="Independent watchdog" feed={watchdog}>
        {w ? (
          <>
            <Rows>
              <Row label="Heartbeat" cls={w.active ? "up" : "down"}>{w.active ? "active" : w.last_beat ? "stale" : "never seen"}</Row>
              <Row label="Last beat">{clock(w.last_beat)}</Row>
              <Row label="Coverage">{w.coverage_hours.toFixed(2)} h / {w.required_hours} h</Row>
              <Row label="Halts" cls={w.recent_trips.length ? "down" : ""}>{w.recent_trips.length}</Row>
            </Rows>
            {checks.length > 0 && (
              <ul className="fl-checks" aria-label="Watchdog checks">
                {checks.map(([name, value]) => <li key={name} className={value === "ok" ? "ok" : "bad"}>{name}: {value}</li>)}
              </ul>
            )}
            <p className="hint">The watchdog only halts. It never releases.</p>
          </>
        ) : <NoData>Watchdog status unavailable.</NoData>}
      </Section>
    </>
  );
}

const OPEN = new Set(["NEW", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"]);

function OmsDesk({ state }: { state: FloorState | null }) {
  const orders = useDeskFeed<{ orders: Order[]; venue?: string }>("/v1/oms/orders?limit=20");
  const ledger = useDeskFeed<Ledger>("/v1/oms/ledger", 10000);
  const l = ledger.data;
  const key = ["USDT", "BTC", "ETH", "SOL", "BNB", "BRL"];
  const held = l ? Object.entries(l.balances).filter(([a, b]) => key.includes(a) && Number(b.total) > 0) : [];
  const halted = state?.controls.killed_venues ?? [];
  const list = orders.data?.orders ?? [];
  return (
    <>
      <Section title="Executor" feed={ledger}>
        <Rows>
          <Row label="Account">{none(l?.account_id)}</Row>
          <Row label="Mode">{none(l?.mode)}</Row>
          <Row label="Venue">{none(l?.venue)}</Row>
          <Row label="Halted venues" cls={halted.length ? "down" : ""}>{state ? halted.join(", ") || "none" : "—"}</Row>
          {l?.unavailable && <Row label="Executor" cls="down">unavailable</Row>}
        </Rows>
        {l && Object.entries(l.accounts).map(([q, a]) => (
          <Rows key={q}><Row label={`${q} equity`}>{Number(a.equity).toFixed(2)}</Row><Row label={`${q} gross exposure`}>{a.gross_exposure === "Infinity" ? "unknown (blocks new risk)" : Number(a.gross_exposure).toFixed(2)}</Row></Rows>
        ))}
        {l && <p className="hint">{Object.keys(l.balances).length} assets in the ledger{held.length ? "; main balances:" : ""}</p>}
        {held.length > 0 && <Rows>{held.map(([a, b]) => <Row key={a} label={a}>{amt(b.total)} (reserved {amt(b.reserved)})</Row>)}</Rows>}
        {l?.xp_b3 && <p className="hint">XP (B3) account {none(l.xp_b3.account_id)}: {l.xp_b3.unavailable ? "unavailable" : `${Object.keys(l.xp_b3.balances ?? {}).length} assets${l.xp_b3.venue_halted ? " · HALTED" : ""}`}</p>}
      </Section>
      <Section title="Orders" feed={orders}>
        {orders.data == null ? <NoData>Orders unavailable.</NoData> : !list.length ? <NoData>No order yet.</NoData> : (
          <ol className="fl-list">
            {list.map(o => (
              <li key={o.client_order_id}>
                <b>{short(o.instrument_id)}</b>
                <span className={o.status === "FILLED" ? "up" : o.status === "REJECTED" || o.status === "UNKNOWN" ? "down" : "warn"}>{o.side} {o.quantity} · {o.status}</span>
                <small>limit {none(o.limit_price)} · filled {amt(o.filled_quantity)}{o.average_price ? ` @ ${amt(o.average_price)}` : ""} · fee {amt(o.fee)}{o.reason ? ` · ${o.reason}` : ""}</small>
                {ORDER_ID.test(o.client_order_id) && (OPEN.has(o.status) || o.status === "UNKNOWN") && (
                  <span className="fl-actions">
                    {OPEN.has(o.status) && <Action label={`Cancel ${o.client_order_id}`} path={`/v1/oms/orders/${o.client_order_id}/cancel`} refresh={orders.refresh}>Cancel</Action>}
                    {o.status === "UNKNOWN" && <Action label={`Resolve ${o.client_order_id}`} path={`/v1/oms/orders/${o.client_order_id}/resolve`} refresh={orders.refresh}>Resolve</Action>}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
        <TokenHint />
      </Section>
    </>
  );
}

function ReconciliationDesk() {
  const drill = useDeskFeed<Drill>("/v1/drills/e2e/latest");
  const watchdog = useDeskFeed<Watchdog>("/v1/watchdog/status");
  const d = drill.data, checks = watchdog.data?.last_report?.checks ?? {};
  const mine = Object.entries(checks).filter(([k]) => k.startsWith("executor_"));
  return (
    <>
      <Section title="Closed test" feed={drill}>
        {d ? (
          <>
            <Rows>
              <Row label="Verdict" cls={d.verdict === "PASSED" ? "up" : d.verdict === "NOT_RUN" ? "" : "down"}>{d.verdict}</Row>
              <Row label="Environment">{none(d.venue_environment)}</Row>
              <Row label="Instrument">{none(d.instrument_id)}</Row>
              <Row label="Started">{clock(d.started_at)} UTC</Row>
            </Rows>
            <ol className="fl-steps-list">
              {(d.steps ?? []).map((s, i) => (
                <li key={i} className={s.ok ? "ok" : "bad"}>
                  <b>{s.ok ? "✓" : "✗"} {String(s.step)}</b>
                  <small>{Object.entries(s).filter(([k]) => k !== "step" && k !== "ok").map(([k, v]) => `${k} ${stepValue(v)}`).join(" · ")}</small>
                </li>
              ))}
            </ol>
            {d.digest && <p className="hint">Digest {d.digest.slice(0, 16)}…</p>}
          </>
        ) : <NoData>Closed test unavailable.</NoData>}
        <div className="row">
          <Action label="Run closed test" path="/v1/drills/e2e/run" refresh={drill.refresh}>Run closed test</Action>
          <Action label="Reconcile now" path="/v1/oms/reconcile" refresh={watchdog.refresh}>Reconcile now</Action>
        </div>
        <TokenHint />
      </Section>
      <Section title="Executor reconciliation (watchdog view)" feed={watchdog}>
        {watchdog.data ? (
          mine.length ? <ul className="fl-checks">{mine.map(([k, v]) => <li key={k} className={v === "ok" ? "ok" : "bad"}>{k}: {v}</li>)}</ul> : <NoData>No reconciliation check reported yet.</NoData>
        ) : <NoData>Watchdog status unavailable.</NoData>}
        <p className="hint">A divergence between the ledger and the venue halts new orders until an operator releases it.</p>
      </Section>
    </>
  );
}

// ---------- the drawer ----------
type DrawerProps = { id: StationId; state: FloorState | null; actions: FloorAction[]; latest: Partial<Record<StationId, FloorAction>>; onClose: () => void };

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,summary,[tabindex]:not([tabindex="-1"])';

export function DeskDrawer({ id, state, actions, latest, onClose }: DrawerProps) {
  const station = STATIONS.find(s => s.id === id)!;
  const title = useId();
  const box = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);
  const last = latest[id] ?? state?.stations?.[id] ?? null;
  const tone: Tone | "idle" = last?.tone ?? "idle";
  const mine = actions.filter(a => a.station === id).slice(-8).reverse();

  // Modal behaviour: focus moves in, Tab stays inside, Escape closes, focus returns to the desk.
  useEffect(() => {
    // A desk is an SVG element over the photo, so "focusable" means HTML or SVG, not only HTMLElement.
    const opener = document.activeElement instanceof HTMLElement || document.activeElement instanceof SVGElement ? document.activeElement : null;
    box.current?.querySelector<HTMLElement>("[data-close]")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(el => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], end = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); end.focus(); }
      else if (!event.shiftKey && document.activeElement === end) { event.preventDefault(); first.focus(); }
      else if (!box.current.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); opener?.focus?.(); };
  }, [onClose]);

  const copy = () => {
    void navigator.clipboard?.writeText(`${window.location.origin}${window.location.pathname}#floor/${id}`).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }, () => undefined);
  };
  const mode = state == null ? null : state.data_mode === "TESTNET" ? "testnet" : "paper";

  return (
    <div className="fl-drawer-wrap">
      <div className="fl-backdrop" onClick={onClose} aria-hidden="true" />
      <aside ref={box} className="fl-drawer" role="dialog" aria-modal="true" aria-labelledby={title} data-desk={id}>
        <header>
          <div>
            <h2 id={title}><i className="fl-dot" style={{ background: TONE_COLOR[tone] }} aria-hidden="true" /> {station.label}</h2>
            <p>{station.role}</p>
          </div>
          <div className="fl-drawer-tools">
            {mode ? <Tag source={mode} /> : <span className="fl-fresh unavailable">mode unavailable</span>}
            <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy link"}</button>
            <button type="button" className="btn" data-close onClick={onClose} aria-label={`Close ${station.label} desk`}>Close ✕</button>
          </div>
        </header>
        <div className="fl-drawer-body">
          <p className="fl-last" style={{ borderColor: TONE_COLOR[tone] }}>
            {last ? <><time>{clock(last.at)} UTC</time> {last.headline}{last.symbol ? ` · ${last.symbol}` : ""}</> : "Waiting for work: no action from this desk yet."}
          </p>
          {id === "tradingview" && <TradingViewDesk actions={actions} />}
          {id === "fusion" && <FusionDesk />}
          {id === "reviewer" && <ReviewerDesk />}
          {id === "risk" && <RiskDesk />}
          {id === "oms" && <OmsDesk state={state} />}
          {id === "reconciliation" && <ReconciliationDesk />}
          <Section title="This desk's recent actions">
            {mine.length ? (
              <ol className="fl-list">
                {mine.map(a => (
                  <li key={`${a.seq}:${a.station}`} style={{ borderColor: TONE_COLOR[a.tone] }}>
                    <time>{clock(a.at)}</time><b>{a.event_type}</b><span>{a.headline}</span>
                    {a.why && a.why.length > 0 && <details><summary>why</summary><ul>{a.why.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
                  </li>
                ))}
              </ol>
            ) : <NoData>No action since this page opened.</NoData>}
          </Section>
        </div>
      </aside>
    </div>
  );
}
