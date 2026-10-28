// The desks of the trading floor, opened one at a time in a drawer, and the XP / B3 and Alpaca US lane strips.
// Every value comes from an existing read-only API surface (see deskData.ts for the narrowing), is
// labelled with its freshness (live / stale / unavailable) and the data mode, and a value that is not
// measured (a frozen checkpoint) is never printed as if it were. The drawer polls only while it is open
// and the tab is visible. Operator actions reuse the System view's token and are inert, with the reason,
// without it. Nothing here can release a halt or raise a limit. XP is a DEMO account and Alpaca a PAPER
// account: never real money.
import { Component, createContext, type ErrorInfo, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { operatorToken } from "../api";
import { Tag, useCommand } from "../ui";
import {
  amt, assertNever, b3Session, type B3Session, dataOf, type Decision, deriveUsLane, deriveXpLane, fin, fmt2, type Feed, type FeedState, isB3, isUs, type Ledger,
  orDash, type Order, parseAgentStatus, parseDecisions, parseDrill, parseLedger, parseOrders, parseRisk, parseRiskDecisions, parseSignals,
  parseStrategy, parseWatchdog, pctOf, positive, short, subDecimal, type UsLane, type UsLaneState, usSession, useDeskFeed, type XpLane, type XpLaneState,
} from "./deskData";
import { clockUtc, type FloorAction, type FloorState, STATIONS, type StationId, TONE_COLOR, TONE_LABEL, type Tone } from "./floorModel";

export { finiteDecimal, fmt2, pctOf } from "./deskData";

// ---------- selection kept in the URL hash: #floor/<desk> ----------
const HASH = /^#floor\/([a-z]+)$/;
const FLOOR_HASH = /^#floor\//;
export const deskFromHash = (hash: string): StationId | null => {
  const id = HASH.exec(hash)?.[1];
  return STATIONS.find(s => s.id === id)?.id ?? null;
};
const urlFor = (id: StationId | null) => `${window.location.pathname}${window.location.search}${id ? `#floor/${id}` : ""}`;

/** Opening a desk pushes a history entry (Back closes it); an unknown #floor/… hash is cleared. */
export function useDeskSelection() {
  const [selected, setSelected] = useState<StationId | null>(() => deskFromHash(window.location.hash));
  useEffect(() => {
    if (FLOOR_HASH.test(window.location.hash) && !deskFromHash(window.location.hash)) {
      try { window.history.replaceState(null, "", urlFor(null)); } catch { /* history blocked */ }
    }
    const sync = () => setSelected(deskFromHash(window.location.hash));
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, []);
  const select = useCallback((id: StationId | null) => {
    try {
      const ours = Boolean((window.history.state as { floorDesk?: boolean } | null)?.floorDesk);
      if (id) {
        if (ours) window.history.replaceState({ floorDesk: true }, "", urlFor(id));
        else if (!deskFromHash(window.location.hash)) window.history.pushState({ floorDesk: true }, "", urlFor(id));
        else window.history.replaceState(window.history.state, "", urlFor(id));
      } else if (ours) window.history.back(); // pop our own entry so Back stays natural
      else if (FLOOR_HASH.test(window.location.hash)) window.history.replaceState(null, "", urlFor(null)); // only ever #floor/…
    } catch { /* history blocked: the drawer still works */ }
    setSelected(id);
  }, []);
  return { selected, select };
}

// ---------- what the drawer tells a screen reader, and freshness ----------
type DeskCtx = { announce: (text: string) => void; registerAction: (label: string) => void };
const Desk = createContext<DeskCtx>({ announce: () => undefined, registerAction: () => undefined });

/** LIVE only while the latest read succeeded and is recent; STALE keeps the last good value and says why. */
function Freshness({ name, state, override }: { name: string; state: FeedState<unknown>; override?: string | null }) {
  const { announce } = useContext(Desk);
  const effective = override ? "unmeasured" : state.kind;
  const before = useRef<string | null>(null);
  useEffect(() => {
    const was = before.current;
    before.current = effective;
    if (was === null || was === effective) return;
    if ((was === "loading" || was === "paused") && effective === "live") return; // the first answer is not news
    announce(`${name}: ${effective === "live" ? "live again" : effective}`);
  }, [effective, name, announce]);
  if (override) return <span className="fl-fresh unavailable">UNAVAILABLE · {override}</span>;
  switch (state.kind) {
    case "off": return null;
    case "loading": return <span className="fl-fresh">LOADING…</span>;
    case "paused": return <span className="fl-fresh stale">PAUSED · tab hidden</span>;
    case "unavailable": return <span className="fl-fresh unavailable">UNAVAILABLE · {state.reason}</span>;
    case "live": return <span className="fl-fresh live">LIVE · updated {clockUtc(state.at)}</span>;
    case "stale": return <span className="fl-fresh stale">STALE · last good read {clockUtc(state.at)}{state.failed ? ` · latest read failed: ${state.failed}` : ""}</span>;
    default: return assertNever(state);
  }
}

function Section({ title, state, override, children }: { title: string; state?: FeedState<unknown>; override?: string | null; children: ReactNode }) {
  return (
    <section className="fl-sec">
      <header><h3>{title}</h3>{state && <Freshness name={title} state={state} override={override} />}</header>
      {children}
    </section>
  );
}

const Row = ({ label, children, cls = "" }: { label: string; children: ReactNode; cls?: string }) => (
  <li><span>{label}</span><b className={cls}>{children}</b></li>
);
const Rows = ({ children }: { children: ReactNode }) => <ul className="fl-rows">{children}</ul>;
const NoData = ({ children }: { children: ReactNode }) => <p className="empty">{children}</p>;
const Missing = ({ what }: { what: string }) => <NoData>{what} unavailable.</NoData>;
const flag = (v: boolean | null, yes: string, no: string) => (v === null ? "—" : v ? yes : no);
const B3Tag = () => <span className="fl-b3" title="XP / B3 demo account">B3</span>;
const UsTag = () => <span className="fl-b3 fl-us" title="Alpaca US paper account">US</span>;
const VenueTag = ({ instrument }: { instrument: string | null }) => (isB3(instrument) ? <B3Tag /> : isUs(instrument) ? <UsTag /> : null);

// ---------- the XP / B3 lane ----------
/** Both executor lanes, read from the same feeds: XP / B3 (lane) and Alpaca US (us). */
export type XpState = { lane: XpLane; us: UsLane; instruments: string[] | null; killedVenues: string[] | null; ledger: Feed<Ledger> };
const LANE_LABEL: Record<XpLaneState, string> = { unknown: "UNKNOWN", "not-configured": "NOT CONFIGURED", "off-hours": "OFF HOURS", unavailable: "UNAVAILABLE", live: "LIVE", halted: "HALTED" };

/** XP and Alpaca routing as the API reports it: agent status (instruments), risk status (halted venues) and, only when either is on, the ledger. */
export function useXpLane(): XpState {
  const agents = useDeskFeed("/v1/agents/status", parseAgentStatus, 10000);
  const risk = useDeskFeed("/v1/risk/status", parseRisk, 10000);
  const instruments = dataOf(agents.state)?.instruments ?? null;
  const listed = instruments?.some(i => i.startsWith("XP_B3:") || i.startsWith("ALPACA_US:")) ?? false;
  const ledger = useDeskFeed(listed ? "/v1/oms/ledger" : null, parseLedger, 15000);
  const killedVenues = dataOf(risk.state)?.controls.venues ?? null;
  const failed = ledger.state.kind === "unavailable" || (ledger.state.kind === "stale" && ledger.state.failed !== null);
  const lane = deriveXpLane({ instruments, ledger: dataOf(ledger.state), ledgerFailed: failed, killedVenues });
  const us = deriveUsLane({ instruments, ledger: dataOf(ledger.state), ledgerFailed: failed, killedVenues });
  return { lane, us, instruments, killedVenues, ledger };
}

function useSession(at: (now: number) => B3Session = b3Session): B3Session {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => { if (document.visibilityState === "visible") setNow(Date.now()); }, 30000);
    return () => window.clearInterval(id);
  }, []);
  return at(now);
}
const sessionText = (s: B3Session, city = "São Paulo") => `${s.open ? "OPEN" : "CLOSED"} · ${s.weekday} ${s.local} in ${city}`;

/** A small status strip: what the XP / B3 lane is right now, in words, with no number that was not measured. */
export function XpStrip({ xp }: { xp: XpState }) {
  const { lane } = xp;
  const session = useSession();
  const brl = lane.ledger?.xp?.balances.find(b => b.asset === "BRL");
  const [said, setSaid] = useState("");
  const before = useRef<XpLaneState | null>(null);
  useEffect(() => {
    // the first answer (from "unknown") is not news; a later change is
    if (before.current !== null && before.current !== "unknown" && before.current !== lane.state) setSaid(`XP / B3 lane: ${LANE_LABEL[lane.state]}`);
    before.current = lane.state;
  }, [lane.state]);
  return (
    <section className={`fl-xp ${lane.state}`} aria-label="XP / B3 lane">
      <b>XP / B3</b>
      <span className="fl-xp-env">DEMO ACCOUNT · TESTNET · NO REAL MONEY</span>
      <span className={`fl-xp-state ${lane.state}`}>{LANE_LABEL[lane.state]}</span>
      <span className="fl-xp-why">{lane.reason}</span>
      {lane.state !== "not-configured" && lane.state !== "unknown" && <span>B3 session {sessionText(session)} (no holiday calendar)</span>}
      {lane.state === "live" && brl && <span>BRL {fmt2(brl.total)} (XP ledger)</span>}
      <span className="sr-only" role="status">{said}</span>
    </section>
  );
}

const US_LABEL: Record<UsLaneState, string> = LANE_LABEL;

/** The Alpaca US lane, like the XP one: paper account, no number that was not measured. Hidden while Alpaca routing is off. */
export function UsStrip({ xp }: { xp: XpState }) {
  const { us } = xp;
  const session = useSession(usSession);
  const usd = us.ledger?.us?.balances.find(b => b.asset === "USD");
  const [said, setSaid] = useState("");
  const before = useRef<UsLaneState | null>(null);
  useEffect(() => {
    if (before.current !== null && before.current !== "unknown" && before.current !== us.state) setSaid(`Alpaca US lane: ${US_LABEL[us.state]}`);
    before.current = us.state;
  }, [us.state]);
  if (us.state === "not-configured") return null;
  return (
    <section className={`fl-xp fl-us ${us.state}`} aria-label="Alpaca US lane">
      <b>US · Alpaca</b>
      <span className="fl-xp-env">PAPER ACCOUNT · SIMULATED FILLS · NO REAL MONEY</span>
      <span className={`fl-xp-state ${us.state}`}>{US_LABEL[us.state]}</span>
      <span className="fl-xp-why">{us.reason}</span>
      {us.state !== "unknown" && <span>US session {sessionText(session, "New York")} (no holiday calendar)</span>}
      {us.state === "live" && usd && <span>USD {fmt2(usd.total)} (Alpaca ledger)</span>}
      <span className="sr-only" role="status">{said}</span>
    </section>
  );
}

// ---------- operator actions ----------
const TOKEN_HINT = "fl-token-hint";
const TokenHint = () => (operatorToken() ? null : <p className="hint" id={TOKEN_HINT}>Actions need the operator token: set it in the System view.</p>);
const ORDER_ID = /^[A-Za-z0-9_-]{1,36}$/;

/** Inert without the token (aria-disabled, still focusable, says why); a risky one needs a second click within 4 s. */
function Action({ label, path, verb, target, refresh, confirm = false }: { label: string; path: string; verb: string; target?: string; refresh: () => void; confirm?: boolean }) {
  const { busy, run } = useCommand();
  const { announce, registerAction } = useContext(Desk);
  const token = Boolean(operatorToken());
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);
  const onClick = () => {
    if (!token || busy !== null) return;
    if (confirm && !armed) { setArmed(true); announce(`Press again within 4 seconds to ${verb.toLowerCase()}${target ? ` order ${target}` : ""}.`); return; }
    setArmed(false);
    registerAction(label);
    void run(label, path).then(refresh);
  };
  const name = `${armed ? "Confirm " : ""}${verb}${target ? ` order ${target}` : ""}`;
  return (
    <button type="button" className="btn" aria-label={armed ? `Confirm ${verb.toLowerCase()}${target ? ` order ${target}` : ""}` : name}
      aria-disabled={token ? undefined : true} aria-busy={busy === label} aria-describedby={token ? undefined : TOKEN_HINT} onClick={onClick}>
      {armed ? `Confirm ${verb}` : verb}
    </button>
  );
}

// ---------- the desks ----------
function TradingViewDesk({ actions }: { actions: FloorAction[] }) {
  const feed = useDeskFeed("/v1/signals/recent", parseSignals);
  const list = dataOf(feed.state);
  const accepted = actions.filter(a => a.event_type === "TRADINGVIEW_SIGNAL_ACCEPTED").length;
  const bySource = (list ?? []).reduce<Record<string, number>>((m, s) => ({ ...m, [s.source ?? "unknown"]: (m[s.source ?? "unknown"] ?? 0) + 1 }), {});
  return (
    <Section title="Recent signals" state={feed.state}>
      <Rows>
        <Row label="In the buffer">{list ? list.length : "—"}</Row>
        <Row label="By source">{Object.keys(bySource).length ? Object.entries(bySource).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</Row>
        <Row label={`TradingView alerts in the last ${actions.length} floor actions`}>{accepted}</Row>
      </Rows>
      {list && !list.length && <NoData>No signal received yet. A TradingView alert or the own strategy starts the chain.</NoData>}
      <ol className="fl-list">
        {(list ?? []).slice(0, 8).map(s => (
          <li key={s.key}>
            <time>{clockUtc(s.at)}</time><b>{short(s.instrument)} <VenueTag instrument={s.instrument} /></b>
            <span className={s.direction === "LONG" ? "up" : s.direction === "SHORT" ? "down" : ""}>{orDash(s.direction)} · strength {fin(s.strength)}</span>
            <small>{orDash(s.source)} · {orDash(s.strategy)} · {orDash(s.timeframe)}</small>
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
        <li key={d.key}>
          <time>{clockUtc(d.at)}</time><b>{short(d.instrument)} <VenueTag instrument={d.instrument} /></b>
          <span className={d.outcome === "APPROVED" ? "up" : d.outcome === "ABSTAINED" || d.outcome === "BLOCKED" ? "warn" : ""}>{orDash(d.outcome)}</span>
          <small>
            {d.hasFusion ? `${orDash(d.direction)} · score ${fin(d.score)} · ${orDash(d.reason)}` : "no fusion data"}
            {d.abstain ? ` · abstained: ${d.abstain}` : ""}
            {d.candidate ? ` · ${orDash(d.candidate.side)} ${orDash(d.candidate.quantity)} @ ${orDash(d.candidate.limit)}` : ""}
          </small>
        </li>
      ))}
    </ol>
  );
}

function XpFusionSection({ xp }: { xp: XpState }) {
  const session = useSession();
  const b3 = (xp.instruments ?? []).filter(i => i.startsWith("XP_B3:")).map(short);
  return (
    <Section title="B3 / XP">
      <Rows>
        <Row label="Lane" cls={xp.lane.state === "live" ? "up" : xp.lane.state === "halted" || xp.lane.state === "unavailable" ? "down" : "warn"}>{LANE_LABEL[xp.lane.state]}</Row>
        <Row label="B3 session hours (no holiday calendar)" cls={session.open ? "up" : ""}>{sessionText(session)}</Row>
        <Row label="B3 instruments in the pipeline">{xp.instruments === null ? "—" : b3.length ? b3.join(", ") : "none"}</Row>
      </Rows>
      <p className="hint">{xp.lane.reason} B3 trades Monday to Friday, 10:00 to 17:55 São Paulo time; outside it the Risk Engine refuses and quotes age out. The account is a DEMO account.</p>
    </Section>
  );
}

function UsFusionSection({ xp }: { xp: XpState }) {
  const session = useSession(usSession);
  const us = (xp.instruments ?? []).filter(i => i.startsWith("ALPACA_US:")).map(short);
  if (xp.us.state === "not-configured") return null;
  return (
    <Section title="US / Alpaca">
      <Rows>
        <Row label="Lane" cls={xp.us.state === "live" ? "up" : xp.us.state === "halted" || xp.us.state === "unavailable" ? "down" : "warn"}>{US_LABEL[xp.us.state]}</Row>
        <Row label="US session hours (no holiday calendar)" cls={session.open ? "up" : ""}>{sessionText(session, "New York")}</Row>
        <Row label="US instruments in the pipeline">{xp.instruments === null ? "—" : us.length ? us.join(", ") : "none"}</Row>
      </Rows>
      <p className="hint">{xp.us.reason} US stocks trade Monday to Friday, 09:30 to 16:00 New York time; outside it the Risk Engine refuses and quotes age out. The account is a PAPER account.</p>
    </Section>
  );
}

function FusionDesk({ xp }: { xp: XpState }) {
  const status = useDeskFeed("/v1/agents/status", parseAgentStatus);
  const decisions = useDeskFeed("/v1/agents/decisions/recent", parseDecisions);
  const strategy = useDeskFeed("/v1/strategy/status", parseStrategy);
  const s = dataOf(status.state), d = dataOf(decisions.state), st = dataOf(strategy.state);
  return (
    <>
      <Section title="Pipeline" state={status.state}>
        <Rows>
          <Row label="Agents" cls={s?.enabled ? "up" : ""}>{s ? flag(s.enabled, "on", "off") : "—"}</Row>
          <Row label="Stage">{orDash(s?.stage)}</Row>
          <Row label="Decisions">{orDash(s?.decisions)}</Row>
          <Row label="Outcomes">{s && s.outcomes.length ? s.outcomes.map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</Row>
          <Row label="Buffered signals">{orDash(s?.buffered)}</Row>
          <Row label="Journal failures" cls={s?.journalFailures ? "down" : ""}>{orDash(s?.journalFailures)}</Row>
        </Rows>
      </Section>
      <XpFusionSection xp={xp} />
      <UsFusionSection xp={xp} />
      <Section title="Latest decisions" state={decisions.state}>
        {d ? <DecisionList decisions={d} /> : <Missing what="Decisions" />}
      </Section>
      <Section title="Own strategy" state={strategy.state}>
        {st ? (
          <>
            <Rows>
              <Row label="Strategy">{orDash(st.id)}</Row>
              <Row label="State" cls={st.running ? "up" : "warn"}>{st.enabled === null || st.running === null ? "—" : st.enabled ? (st.running ? "running" : "stopped") : "disabled"}</Row>
              <Row label="Market source">{orDash(st.source)}</Row>
              <Row label="Ticks / signals">{orDash(st.ticks)} / {orDash(st.signals)}</Row>
            </Rows>
            <ul className="fl-bars">
              {st.books.map(b => {
                const ready = b.bars !== null && b.needs !== null && b.needs > 0;
                return (
                  <li key={b.id}>
                    <span>{short(b.id)}</span>
                    <div role="progressbar" aria-label={`${short(b.id)} warm-up`} aria-valuemin={0} aria-valuemax={b.needs ?? 0} aria-valuenow={ready ? Math.min(b.bars!, b.needs!) : 0}><i style={{ width: ready ? `${Math.min(1, b.bars! / b.needs!) * 100}%` : "0%" }} /></div>
                    <small>{orDash(b.bars)}/{orDash(b.needs)} bars · last signal {clockUtc(b.last)}</small>
                  </li>
                );
              })}
            </ul>
            <p className="hint">The strategy emits opinions only; it has no order authority.</p>
          </>
        ) : <Missing what="Strategy status" />}
      </Section>
    </>
  );
}

function ReviewerDesk() {
  const status = useDeskFeed("/v1/agents/status", parseAgentStatus);
  const decisions = useDeskFeed("/v1/agents/decisions/recent", parseDecisions);
  const s = dataOf(status.state), d = dataOf(decisions.state);
  const reviewed = (d ?? []).filter(x => x.review).slice(0, 6);
  return (
    <>
      <Section title="Reviewer" state={status.state}>
        <Rows>
          <Row label="Mode">{orDash(s?.reviewer)}</Row>
          <Row label="Model">{s ? (s.modelKnown ? s.model ?? "none (deterministic)" : "—") : "—"}</Row>
          <Row label="Stage">{orDash(s?.stage)}</Row>
        </Rows>
        <p className="hint">The reviewer can only shrink or block a candidate, never enlarge it or place an order.</p>
      </Section>
      <Section title="Latest reviews" state={decisions.state}>
        {d === null ? <Missing what="Reviews" /> : !reviewed.length ? <NoData>No review yet.</NoData> : (
          <ol className="fl-list">
            {reviewed.map(x => (
              <li key={x.key}>
                <time>{clockUtc(x.at)}</time><b>{short(x.instrument)} <VenueTag instrument={x.instrument} /></b>
                <span className={x.review?.verdict === "APPROVE" ? "up" : "warn"}>{orDash(x.review?.verdict)} · size ×{fin(x.review?.sizeFactor)}</span>
                <small>{orDash(x.review?.rationale)}</small>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </>
  );
}

/** The equity of an account is a measurement only while its executor is readable and its exposure could be marked. */
function accountRows(l: Ledger) {
  return l.accounts.map(a => ({ ...a, measured: !l.unavailable && a.exposure !== null, source: a.quote === "BRL" && l.xp ? "XP ledger" : a.quote === "USDT" ? "Binance executor ledger" : "executor / paper ledger" }));
}

function RiskDesk({ xp }: { xp: XpState }) {
  const { announce } = useContext(Desk);
  const risk = useDeskFeed("/v1/risk/status", parseRisk);
  const decisions = useDeskFeed("/v1/risk/decisions/recent", parseRiskDecisions);
  const watchdog = useDeskFeed("/v1/watchdog/status", parseWatchdog);
  const ledger = useDeskFeed("/v1/oms/ledger", parseLedger, 10000);
  const r = dataOf(risk.state), d = dataOf(decisions.state), w = dataOf(watchdog.state), l = dataOf(ledger.state);
  const c = r?.controls, k = r?.limits;
  const signature = c ? JSON.stringify([c.globalKill, c.venues, c.lockdown.value]) : null;
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (signature === null) return;
    if (seen.current !== null && seen.current !== signature) announce("Risk controls changed: kill switch, halted venues or lockdown differ from the last read.");
    seen.current = signature;
  }, [signature, announce]);
  const accounts = l ? accountRows(l) : [];
  const unmeasured = accounts.filter(a => !a.measured).map(a => a.quote);
  return (
    <>
      <Section title="Controls" state={risk.state}>
        <Rows>
          <Row label="Global kill switch" cls={c?.globalKill ? "down" : c?.globalKill === false ? "up" : ""}>{c ? flag(c.globalKill, "ON", "armed") : "—"}</Row>
          <Row label="Runtime kill switch" cls={c && c.runtime !== null && c.runtime !== "ARMED" ? "down" : ""}>{orDash(c?.runtime)}</Row>
          <Row label="Halted venues">
            {c?.venues ? (c.venues.length ? c.venues.map(v => <span key={v} className={`fl-venue down${v === "XP_B3" ? " xp" : v === "ALPACA_US" ? " us" : ""}`}>{v}{v === "XP_B3" ? " (XP / B3 demo)" : v === "ALPACA_US" ? " (Alpaca US paper)" : ""}</span>) : "none") : "—"}
          </Row>
          <Row label="XP / B3 venue" cls={xp.lane.state === "halted" ? "down" : ""}>{LANE_LABEL[xp.lane.state]}</Row>
          <Row label="Alpaca US venue" cls={xp.us.state === "halted" ? "down" : ""}>{US_LABEL[xp.us.state]}</Row>
          <Row label="Halted strategies" cls={c?.strategies?.length ? "down" : ""}>{c?.strategies ? c.strategies.join(", ") || "none" : "—"}</Row>
          <Row label="Lockdown" cls={c?.lockdown.value ? "down" : ""}>{c ? (c.lockdown.known ? c.lockdown.value ?? "none" : "—") : "—"}</Row>
        </Rows>
        <p className="hint">Releasing a halt or a lockdown stays a deliberate operator action outside this view.</p>
      </Section>
      <Section title="Accounts the limits are measured on" state={ledger.state} override={l && unmeasured.length ? `equity not measured for ${unmeasured.join(", ")}` : null}>
        {l ? (
          <Rows>
            {accounts.map(a => (
              <Row key={a.quote} label={`${a.quote} equity (${a.source})`}>{a.measured ? fmt2(a.equity) : "— last checkpoint, executor unavailable or no live quote"}</Row>
            ))}
          </Rows>
        ) : <Missing what="Accounts" />}
        {l?.xp && <p className="hint">The BRL account is read from the XP ledger (a DEMO account), not from the Binance executor.</p>}
      </Section>
      <Section title="Constitutional limits" state={risk.state}>
        <Rows>
          <Row label="Reference capital">{orDash(k?.capital)}</Row>
          <Row label="Risk per position">{pctOf(k?.perPosition)}</Row>
          <Row label="Daily loss">{pctOf(k?.dailyLoss)}</Row>
          <Row label="Drawdown">{pctOf(k?.drawdown)}</Row>
          <Row label="Gross leverage">{k?.leverage ? `${k.leverage}x` : "—"}</Row>
          <Row label="Quote max age">{k?.quoteAge ? `${k.quoteAge} s` : "—"}</Row>
          <Row label="Permit TTL">{k?.permitTtl ? `${k.permitTtl} s` : "—"}</Row>
        </Rows>
      </Section>
      <Section title="Latest permits and rejections" state={decisions.state}>
        {d === null ? <Missing what="Risk decisions" /> : !d.length ? <NoData>No risk decision yet.</NoData> : (
          <ol className="fl-list">
            {d.slice(0, 6).map(x => (
              <li key={x.key}>
                <b>{short(x.instrument)} <VenueTag instrument={x.instrument} /></b>
                <span className={x.approved ? "up" : x.approved === false ? "down" : ""}>{x.approved === null ? "—" : x.approved ? "permit issued" : "rejected"}</span>
                <small>{x.approved ? `daily loss ${orDash(x.dailyLoss)} · drawdown ${orDash(x.drawdown)}` : x.rejections.join(", ") || "no reason given"}{x.breach ? ` · breach ${x.breach}` : ""}</small>
              </li>
            ))}
          </ol>
        )}
      </Section>
      <Section title="Independent watchdog" state={watchdog.state}>
        {w ? (
          <>
            <Rows>
              <Row label="Heartbeat" cls={w.active ? "up" : w.active === false ? "down" : ""}>{w.active === null ? "—" : w.active ? "active" : w.lastBeat ? "stale" : "never seen"}</Row>
              <Row label="Last beat">{clockUtc(w.lastBeat)}</Row>
              <Row label="Coverage">{fin(w.coverage)} h / {orDash(w.required)} h</Row>
              <Row label="Halts" cls={w.halts ? "down" : ""}>{orDash(w.halts)}</Row>
            </Rows>
            {w.checks.length > 0 && (
              <ul className="fl-checks" aria-label="Watchdog checks">
                {w.checks.map(([name, value]) => <li key={name} className={value === "ok" ? "ok" : "bad"}>{name}: {value}</li>)}
              </ul>
            )}
            <p className="hint">The watchdog only halts. It never releases. It does not probe the XP executor yet.</p>
          </>
        ) : <Missing what="Watchdog status" />}
      </Section>
    </>
  );
}

const OPEN = new Set(["NEW", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"]);
const MAIN_ASSETS = ["USDT", "BTC", "ETH", "SOL", "BNB", "BRL"];

const venueWord = (suffix: string) => (suffix.includes("ALPACA_US") ? " (Alpaca)" : suffix ? " (XP)" : "");

/** Orders with their cancel / resolve actions; `suffix` carries ?venue=XP_B3 or ?venue=ALPACA_US for those lists. */
function OrderList({ orders, suffix, refresh, tag }: { orders: Order[]; suffix: string; refresh: () => void; tag: ReactNode }) {
  return (
    <ol className="fl-list">
      {orders.map(o => {
        const open = o.status !== null && OPEN.has(o.status);
        const actionable = o.id !== null && ORDER_ID.test(o.id) && (open || o.status === "UNKNOWN");
        return (
          <li key={o.key}>
            <b>{short(o.instrument)} {tag}</b>
            <span className={o.status === "FILLED" ? "up" : o.status === "REJECTED" || o.status === "UNKNOWN" ? "down" : "warn"}>{orDash(o.side)} {orDash(o.quantity)} · {orDash(o.status)}</span>
            <small>limit {orDash(o.limit)} · filled {amt(o.filled)}{o.average ? ` @ ${amt(o.average)}` : ""} · fee {amt(o.fee)}{o.reason ? ` · ${o.reason}` : ""}</small>
            {actionable && o.id && (
              <span className="fl-actions">
                {open && <Action label={`Cancel ${o.id}${venueWord(suffix)}`} path={`/v1/oms/orders/${o.id}/cancel${suffix}`} verb="Cancel" target={o.id} refresh={refresh} />}
                {o.status === "UNKNOWN" && <Action label={`Resolve ${o.id}${venueWord(suffix)}`} path={`/v1/oms/orders/${o.id}/resolve${suffix}`} verb="Resolve" target={o.id} refresh={refresh} confirm />}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** The XP / B3 demo account: BRL, PROTO's own positions, orders and fills, all read from the XP executor through the API. */
function XpOmsSection({ xp, ledgerFeed }: { xp: XpState; ledgerFeed: Feed<Ledger> }) {
  const l = dataOf(ledgerFeed.state);
  const failed = ledgerFeed.state.kind === "unavailable" || (ledgerFeed.state.kind === "stale" && ledgerFeed.state.failed !== null);
  const lane = deriveXpLane({ instruments: xp.instruments, ledger: l, ledgerFailed: failed, killedVenues: xp.killedVenues });
  const off = lane.state === "not-configured" || lane.state === "unknown";
  const orders = useDeskFeed(off ? null : "/v1/oms/orders?venue=XP_B3&limit=20", parseOrders);
  const list = dataOf(orders.state);
  const block = l?.xp ?? null;
  const measured = lane.state === "live" || lane.state === "halted";
  const brl = block?.balances.find(b => b.asset === "BRL");
  const positions = (block?.balances ?? []).filter(b => b.asset !== "BRL" && positive(b.total));
  return (
    <Section title="XP / B3 (demo account)" state={off ? undefined : ledgerFeed.state} override={!off && lane.state === "unavailable" ? "XP executor unavailable, nothing measured" : !off && lane.state === "off-hours" ? "XP node off outside its schedule (09:30-18:10 São Paulo, Mon-Fri), nothing measured" : null}>
      <Rows>
        <Row label="Lane" cls={lane.state === "live" ? "up" : lane.state === "halted" || lane.state === "unavailable" ? "down" : "warn"}>{LANE_LABEL[lane.state]}</Row>
        <Row label="Account">{orDash(measured ? block?.accountId : null)}</Row>
        <Row label="Environment">{measured ? "TESTNET (XP DEMO account)" : "—"}</Row>
      </Rows>
      <p className="hint">{lane.reason}</p>
      {measured && block && (
        <>
          <Rows>
            <Row label="BRL balance">{brl ? fmt2(brl.total) : "—"}</Row>
            <Row label="BRL opening balance">{brl?.opening != null ? fmt2(brl.opening) : "not reported"}</Row>
          </Rows>
          {positions.length ? (
            <Rows>
              {positions.map(p => {
                const own = p.opening === null ? null : subDecimal(p.total, p.opening);
                return <Row key={p.asset} label={p.asset}>{amt(p.total)} total · {own === null ? "own position unknown (opening not reported)" : `${amt(own)} PROTO's own`}</Row>;
              })}
            </Rows>
          ) : <NoData>No B3 position held.</NoData>}
          <p className="hint">PROTO's own position is the total minus the opening balance when the executor reports it: what the demo account already held is outside PROTO's book.</p>
        </>
      )}
      {!off && (
        <>
          <h4 className="fl-sub">Orders and fills (XP)</h4>
          <Freshness name="XP orders" state={orders.state} />
          {list === null ? <Missing what="XP orders" /> : !list.length ? <NoData>No XP order yet.</NoData> : <OrderList orders={list} suffix="?venue=XP_B3" refresh={orders.refresh} tag={<B3Tag />} />}
          <div className="row"><Action label="Reconcile now (XP and Binance)" path="/v1/oms/reconcile" verb="Reconcile now" refresh={() => { orders.refresh(); ledgerFeed.refresh(); }} /></div>
          <p className="hint">The reconcile covers both venues; its outcome (consistent or not, per venue) appears in the status line above.</p>
          <TokenHint />
        </>
      )}
    </Section>
  );
}

function OmsDesk({ state, stateFresh, xp }: { state: FloorState | null; stateFresh: boolean; xp: XpState }) {
  const orders = useDeskFeed("/v1/oms/orders?limit=20", parseOrders);
  const ledger = useDeskFeed("/v1/oms/ledger", parseLedger, 10000);
  const l = dataOf(ledger.state), list = dataOf(orders.state);
  // An executor that cannot be read, or an account whose exposure cannot be marked, freezes equity at a
  // checkpoint: that number is not a measurement and is never printed as one.
  const rows = l ? accountRows(l) : [];
  const unmeasured = rows.filter(a => !a.measured).map(a => a.quote);
  const override = l && (l.unavailable ? "executor unavailable, equity not measured" : unmeasured.length ? `equity not measured for ${unmeasured.join(", ")}` : null);
  const held = l && !l.unavailable ? l.balances.filter(b => positive(b.total)) : [];
  const main = MAIN_ASSETS.flatMap(a => held.filter(b => b.asset === a));
  const killed = state?.controls?.killed_venues;
  return (
    <>
      <Section title="Executor (Binance testnet)" state={ledger.state} override={override}>
        <Rows>
          <Row label="Account">{orDash(l?.accountId)}</Row>
          <Row label="Mode">{orDash(l?.mode)}</Row>
          <Row label="Venue">{orDash(l?.venue)}</Row>
          <Row label={`Halted venues${stateFresh ? "" : " (last known)"}`} cls={Array.isArray(killed) && killed.length ? "down" : ""}>{Array.isArray(killed) ? killed.join(", ") || "none" : "—"}</Row>
        </Rows>
        {l && rows.map(a => (
          <Rows key={a.quote}>
            <Row label={`${a.quote} equity${a.quote === "BRL" && l.xp ? " (read from the XP ledger)" : ""}`}>{a.measured ? fmt2(a.equity) : "—"}</Row>
            <Row label={`${a.quote} gross exposure`}>{a.measured ? fmt2(a.exposure) : "—"}</Row>
            {!a.measured && <Row label={`${a.quote} state`} cls="warn">last checkpoint, executor unavailable or no live quote</Row>}
          </Rows>
        ))}
        {l && !l.unavailable && <p className="hint">{l.balances.length} assets in the ledger; {held.length} with a balance{main.length ? ". Main balances:" : "."}</p>}
        {main.length > 0 && <Rows>{main.map(b => <Row key={b.asset} label={b.asset}>{amt(b.total)} (reserved {amt(b.reserved)})</Row>)}</Rows>}
        {l && !l.unavailable && held.length > main.length && <p className="hint">{held.length - main.length} other assets with a balance are not listed.</p>}
      </Section>
      <Section title="Orders (Binance)" state={orders.state}>
        {list === null ? <Missing what="Orders" /> : !list.length ? <NoData>No order yet.</NoData> : <OrderList orders={list} suffix="" refresh={orders.refresh} tag={null} />}
        <TokenHint />
      </Section>
      <XpOmsSection xp={xp} ledgerFeed={ledger} />
      <UsOmsSection xp={xp} ledgerFeed={ledger} />
    </>
  );
}

/** The Alpaca US paper account: USD, PROTO's own positions, orders and fills, all read from the Alpaca executor through the API. */
function UsOmsSection({ xp, ledgerFeed }: { xp: XpState; ledgerFeed: Feed<Ledger> }) {
  const l = dataOf(ledgerFeed.state);
  const failed = ledgerFeed.state.kind === "unavailable" || (ledgerFeed.state.kind === "stale" && ledgerFeed.state.failed !== null);
  const lane = deriveUsLane({ instruments: xp.instruments, ledger: l, ledgerFailed: failed, killedVenues: xp.killedVenues });
  const off = lane.state === "not-configured" || lane.state === "unknown";
  const orders = useDeskFeed(off ? null : "/v1/oms/orders?venue=ALPACA_US&limit=20", parseOrders);
  if (lane.state === "not-configured") return null;
  const list = dataOf(orders.state);
  const block = l?.us ?? null;
  const measured = lane.state === "live" || lane.state === "halted";
  const usd = block?.balances.find(b => b.asset === "USD");
  const positions = (block?.balances ?? []).filter(b => b.asset !== "USD" && positive(b.total));
  return (
    <Section title="US / Alpaca (paper account)" state={off ? undefined : ledgerFeed.state} override={!off && lane.state === "unavailable" ? "Alpaca executor unavailable, nothing measured" : null}>
      <Rows>
        <Row label="Lane" cls={lane.state === "live" ? "up" : lane.state === "halted" || lane.state === "unavailable" ? "down" : "warn"}>{US_LABEL[lane.state]}</Row>
        <Row label="Account">{orDash(measured ? block?.accountId : null)}</Row>
        <Row label="Environment">{measured ? "PAPER (Alpaca paper account, simulated fills)" : "—"}</Row>
      </Rows>
      <p className="hint">{lane.reason}</p>
      {measured && block && (
        <>
          <Rows>
            <Row label="USD balance">{usd ? fmt2(usd.total) : "—"}</Row>
            <Row label="USD opening balance">{usd?.opening != null ? fmt2(usd.opening) : "not reported"}</Row>
          </Rows>
          {positions.length ? (
            <Rows>
              {positions.map(p => {
                const own = p.opening === null ? null : subDecimal(p.total, p.opening);
                return <Row key={p.asset} label={p.asset}>{amt(p.total)} total · {own === null ? "own position unknown (opening not reported)" : `${amt(own)} PROTO's own`}</Row>;
              })}
            </Rows>
          ) : <NoData>No US position held.</NoData>}
        </>
      )}
      {!off && (
        <>
          <h4 className="fl-sub">Orders and fills (Alpaca)</h4>
          <Freshness name="Alpaca orders" state={orders.state} />
          {list === null ? <Missing what="Alpaca orders" /> : !list.length ? <NoData>No Alpaca order yet.</NoData> : <OrderList orders={list} suffix="?venue=ALPACA_US" refresh={orders.refresh} tag={<UsTag />} />}
          <TokenHint />
        </>
      )}
    </Section>
  );
}

function ReconciliationDesk({ xp }: { xp: XpState }) {
  const drill = useDeskFeed("/v1/drills/e2e/latest", parseDrill);
  const watchdog = useDeskFeed("/v1/watchdog/status", parseWatchdog);
  const d = dataOf(drill.state), w = dataOf(watchdog.state);
  const mine = (w?.checks ?? []).filter(([k]) => k.startsWith("executor_"));
  return (
    <>
      <Section title="Closed test (Binance testnet)" state={drill.state}>
        {d ? (
          <>
            <Rows>
              <Row label="Verdict" cls={d.verdict === "PASSED" ? "up" : d.verdict === "NOT_RUN" || d.verdict === null ? "" : "down"}>{orDash(d.verdict)}</Row>
              <Row label="Environment">{orDash(d.env)}</Row>
              <Row label="Instrument">{orDash(d.instrument)}</Row>
              <Row label="Started">{clockUtc(d.started)}</Row>
            </Rows>
            <ol className="fl-steps-list">
              {d.steps.map(s => (
                <li key={s.key} className={s.ok ? "ok" : "bad"}>
                  <b>{s.ok === null ? "?" : s.ok ? "✓" : "✗"} {s.name}</b>
                  <small>{s.detail}</small>
                </li>
              ))}
            </ol>
            {d.digest && <p className="hint">Digest {d.digest.slice(0, 16)}…</p>}
          </>
        ) : <Missing what="Closed test" />}
        <div className="row">
          <Action label="Run closed test" path="/v1/drills/e2e/run" verb="Run closed test" refresh={drill.refresh} confirm />
          <Action label="Reconcile now" path="/v1/oms/reconcile" verb="Reconcile now" refresh={watchdog.refresh} />
        </div>
        <TokenHint />
      </Section>
      <Section title="Executor reconciliation (watchdog view)" state={watchdog.state}>
        {w ? (mine.length ? <ul className="fl-checks">{mine.map(([k, v]) => <li key={k} className={v === "ok" ? "ok" : "bad"}>{k}: {v}</li>)}</ul> : <NoData>No reconciliation check reported yet.</NoData>) : <Missing what="Watchdog status" />}
        <p className="hint">A divergence between the ledger and the venue halts new orders until an operator releases it. XP / B3: {LANE_LABEL[xp.lane.state]}; the watchdog does not probe the XP executor yet.</p>
      </Section>
    </>
  );
}

// ---------- failures stay inside the desk ----------
export class DeskBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown, info: ErrorInfo) { console.error("desk view failed", error, info.componentStack); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
const PanelFailed = () => <p className="fl-fail" role="alert">Desk view failed - reload the page.</p>;
/** The whole drawer failed: a bare panel that can still be closed. */
export function DeskFailed({ onClose }: { onClose: () => void }) {
  return createPortal(
    <div className="fl-drawer-wrap">
      <div className="fl-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="fl-drawer" role="alertdialog" aria-label="Desk view failed">
        <div className="fl-drawer-body"><p className="fl-fail">Desk view failed - reload the page.</p><button type="button" className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>,
    document.body,
  );
}

// ---------- the drawer ----------
type DrawerProps = { id: StationId; state: FloorState | null; stateFresh: boolean; actions: FloorAction[]; latest: Partial<Record<StationId, FloorAction>>; xp: XpState; onClose: () => void };
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,summary,[tabindex]:not([tabindex="-1"])';

export function DeskDrawer({ id, state, stateFresh, actions, latest, xp, onClose }: DrawerProps) {
  const station = STATIONS.find(s => s.id === id)!;
  const box = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const flip = useRef(false);
  const labels = useRef(new Set<string>());
  const { log } = useCommand();
  const last = latest[id] ?? state?.stations?.[id] ?? null;
  const tone: Tone | "idle" = last?.tone ?? "idle";
  const mine = actions.filter(a => a.station === id).slice(-8).reverse();

  const announce = useCallback((text: string) => { flip.current = !flip.current; setAnnouncement(flip.current ? text : `${text}​`); }, []);
  const ctx = useMemo<DeskCtx>(() => ({ announce, registerAction: label => { labels.current.add(label); } }), [announce]);
  // The result of an action started here (ok, failed with its status, or the token is missing) shows in the drawer.
  useEffect(() => {
    const entry = log[0];
    if (entry && labels.current.has(entry.label)) setOutcome({ ok: entry.ok, text: `${entry.ok ? "OK" : "FAILED"} · ${entry.label} · ${entry.detail}` });
  }, [log]);

  // Modal behaviour: the rest of the page is inert, focus moves in and stays inside, Escape closes,
  // and focus goes back to where it came from (the desk itself when the drawer was opened by a link).
  useEffect(() => {
    const active = document.activeElement;
    const opener = active instanceof HTMLElement || active instanceof SVGElement ? active : null;
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
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
    return () => {
      document.removeEventListener("keydown", onKey);
      root?.removeAttribute("inert");
      const back = opener && opener !== document.body && document.contains(opener)
        ? opener
        : document.querySelector<HTMLElement | SVGElement>(`.fl-hit[data-desk="${id}"], .fl-desks button[data-desk="${id}"]`);
      back?.focus?.();
    };
  }, [id, onClose]);

  const copy = () => {
    void navigator.clipboard?.writeText(`${window.location.origin}${window.location.pathname}#floor/${id}`).then(() => { setCopied(true); announce("Link copied."); window.setTimeout(() => setCopied(false), 1500); }, () => undefined);
  };
  const mode = state?.data_mode === "TESTNET" ? "testnet" : state?.data_mode === "PAPER" ? "paper" : null;

  return createPortal(
    <Desk.Provider value={ctx}>
      <div className="fl-drawer-wrap">
        <div className="fl-backdrop" onClick={onClose} aria-hidden="true" />
        <div ref={box} className="fl-drawer" role="dialog" aria-modal="true" aria-label={`${station.label} desk`} data-desk={id}>
          <header>
            <div>
              <h2><i className="fl-dot" style={{ background: TONE_COLOR[tone] }} aria-hidden="true" /> {station.label} <span className="fl-tone">{TONE_LABEL[tone]}</span></h2>
              <p>{station.role}</p>
            </div>
            <div className="fl-drawer-tools">
              {mode ? <><Tag source={mode} />{!stateFresh && <span className="fl-fresh stale">last known</span>}</> : <span className="fl-fresh unavailable">mode unavailable</span>}
              <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy link"}</button>
              <button type="button" className="btn" data-close onClick={onClose} aria-label={`Close ${station.label} desk`}>Close ✕</button>
            </div>
          </header>
          <div className="fl-status" role="status" aria-live="polite" aria-atomic="true">
            <span className="sr-only">{announcement}</span>
            {outcome && <span className={`fl-outcome ${outcome.ok ? "ok" : "bad"}`}>{outcome.text}</span>}
          </div>
          <div className="fl-drawer-body" tabIndex={0} role="region" aria-label={`${station.label} desk details`}>
            <p className="fl-last" style={{ borderColor: TONE_COLOR[tone] }}>
              {last ? <><time>{clockUtc(last.at)}</time> {last.headline}{last.symbol ? ` · ${last.symbol}` : ""}</> : "Waiting for work: no action from this desk yet."}
            </p>
            <DeskBoundary fallback={<PanelFailed />}>
              {id === "tradingview" && <TradingViewDesk actions={actions} />}
              {id === "fusion" && <FusionDesk xp={xp} />}
              {id === "reviewer" && <ReviewerDesk />}
              {id === "risk" && <RiskDesk xp={xp} />}
              {id === "oms" && <OmsDesk state={state} stateFresh={stateFresh} xp={xp} />}
              {id === "reconciliation" && <ReconciliationDesk xp={xp} />}
            </DeskBoundary>
            <Section title="This desk's recent actions">
              {mine.length ? (
                <ol className="fl-list">
                  {mine.map(a => (
                    <li key={`${a.seq}:${a.station}`} style={{ borderColor: TONE_COLOR[a.tone] }}>
                      <time>{clockUtc(a.at)}</time><b>{a.event_type}</b><span>{a.headline} <em className="fl-tone">{TONE_LABEL[a.tone]}</em></span>
                      {a.why && a.why.length > 0 && <details><summary>why</summary><ul>{a.why.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
                    </li>
                  ))}
                </ol>
              ) : <NoData>No action in the floor stream window since this page opened.</NoData>}
            </Section>
          </div>
        </div>
      </div>
    </Desk.Provider>,
    document.body,
  );
}

