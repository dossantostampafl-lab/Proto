import "./operational-runtime.css";

type RuntimeState = { mode?: string; running?: boolean; kill_switch?: string; replay_speed?: number };
type RiskState = { kill_switch?: string; simulation_allowed?: boolean; real_money_execution?: boolean; minimum_net_edge?: number; minimum_confidence?: number; max_notional?: number; max_daily_drawdown?: number };
type Reconciliation = { mode?: string; consistent?: boolean; issues?: string[]; journal_fill_count?: number; authoritative_fill_count?: number };
type Fill = { order_id?: string; asset?: string; side?: string; filled_quantity?: number; fill_price?: number; fee?: number; slippage_bps?: number; filled_at?: string };
type Fills = { mode?: string; count?: number; fills?: Fill[] };
type ApiResult<T> = { ok: boolean; status: number; data: T | null };

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const POLL_MS = 5000;
const REQUEST_TIMEOUT_MS = 2500;

async function requestJson<T>(path: string): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: controller.signal });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function money(value: number | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)}`;
}

function percent(value: number | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(2)}%`;
}

function metric(label: string, value: string, tone?: "ok" | "warn" | "bad") {
  const box = el("div", `opsMetric${tone ? ` ${tone}` : ""}`);
  box.append(el("span", "", label), el("b", "", value));
  return box;
}

function ensureSurface() {
  let surface = document.querySelector<HTMLElement>(".operationalSurface");
  if (surface) return surface;
  const footer = document.querySelector<HTMLElement>("footer[data-section='SYSTEM']");
  if (!footer) return null;
  surface = el("section", "operationalSurface");
  surface.setAttribute("aria-label", "Operational system telemetry");
  footer.before(surface);
  return surface;
}

function renderUnavailable(surface: HTMLElement) {
  surface.replaceChildren();
  const panel = el("article", "opsPanel unavailable");
  panel.append(el("header", "", "OPERATIONAL TELEMETRY"), el("p", "", "Runtime, risk, reconciliation and simulated-fill endpoints are temporarily unavailable."));
  surface.append(panel);
}

function render(surface: HTMLElement, runtime: RuntimeState | null, risk: RiskState | null, reconciliation: Reconciliation | null, fills: Fills | null) {
  surface.replaceChildren();

  const runtimePanel = el("article", "opsPanel");
  runtimePanel.append(el("header", "", "RUNTIME / RISK POLICY"));
  const runtimeGrid = el("div", "opsMetrics");
  const killSwitch = runtime?.kill_switch ?? risk?.kill_switch ?? "—";
  const simAllowed = risk?.simulation_allowed;
  runtimeGrid.append(
    metric("MODE", runtime?.mode ?? "—"),
    metric("RUNTIME", runtime?.running == null ? "—" : runtime.running ? "RUNNING" : "STOPPED", runtime?.running ? "ok" : "warn"),
    metric("KILL SWITCH", killSwitch, killSwitch === "ARMED" ? "ok" : "bad"),
    metric("SIMULATION", simAllowed == null ? "—" : simAllowed ? "ALLOWED" : "BLOCKED", simAllowed ? "ok" : "warn"),
    metric("MIN EDGE", percent(risk?.minimum_net_edge)),
    metric("MIN CONF", percent(risk?.minimum_confidence)),
    metric("MAX NOTIONAL", money(risk?.max_notional)),
    metric("MAX DRAWDOWN", money(risk?.max_daily_drawdown)),
  );
  runtimePanel.append(runtimeGrid);
  const boundary = el("p", "opsBoundary", risk?.real_money_execution === false ? "FINANCIAL CONNECTIVITY OFF · REAL-MONEY EXECUTION FALSE" : "FINANCIAL EXECUTION STATE UNAVAILABLE");
  runtimePanel.append(boundary);

  const reconcilePanel = el("article", "opsPanel");
  reconcilePanel.append(el("header", "", "PORTFOLIO RECONCILIATION"));
  const reconcileState = reconciliation?.consistent;
  const reconcileHero = el("div", `reconcileHero ${reconcileState === true ? "ok" : reconcileState === false ? "bad" : "warn"}`);
  reconcileHero.append(el("strong", "", reconcileState == null ? "UNKNOWN" : reconcileState ? "CONSISTENT" : "MISMATCH"));
  reconcileHero.append(el("span", "", `journal ${reconciliation?.journal_fill_count ?? "—"} · authoritative ${reconciliation?.authoritative_fill_count ?? "—"}`));
  reconcilePanel.append(reconcileHero);
  const issues = reconciliation?.issues ?? [];
  const issueBox = el("div", "opsIssues");
  if (!issues.length) issueBox.append(el("span", "", reconcileState === true ? "No reconciliation issues reported." : "No issue detail available."));
  else issues.slice(0, 5).forEach((issue) => issueBox.append(el("span", "", issue)));
  reconcilePanel.append(issueBox);

  const fillsPanel = el("article", "opsPanel fillsPanel");
  fillsPanel.append(el("header", "", "RECENT SIMULATED FILLS"));
  const rows = el("div", "fillRows");
  const fillList = fills?.fills ?? [];
  if (!fillList.length) {
    rows.append(el("div", "fillEmpty", "No simulated fills in the current journal window."));
  } else {
    fillList.slice(0, 8).forEach((fill) => {
      const row = el("div", "fillRow");
      const identity = el("span", "fillIdentity");
      identity.append(el("b", fill.side === "BUY" ? "ok" : fill.side === "SELL" ? "bad" : "", `${fill.side ?? "—"} ${fill.asset ?? "—"}`), el("small", "", fill.order_id?.slice(0, 8) ?? "—"));
      row.append(
        identity,
        el("span", "", `${fill.filled_quantity == null ? "—" : fill.filled_quantity.toFixed(6)} @ ${money(fill.fill_price)}`),
        el("span", "", `fee ${money(fill.fee)}`),
        el("span", "", `slip ${fill.slippage_bps == null ? "—" : `${fill.slippage_bps.toFixed(2)} bp`}`),
      );
      rows.append(row);
    });
  }
  fillsPanel.append(rows);
  fillsPanel.append(el("small", "fillCount", `${fills?.count ?? fillList.length} simulated fills reported by /v1/fills`));

  surface.append(runtimePanel, reconcilePanel, fillsPanel);
}

function startOperationalTelemetry() {
  let cancelled = false;
  let inFlight = false;

  async function refresh() {
    if (cancelled || inFlight) return;
    const surface = ensureSurface();
    if (!surface) return;
    inFlight = true;
    try {
      const [runtime, risk, reconciliation, fills] = await Promise.all([
        requestJson<RuntimeState>("/system/status"),
        requestJson<RiskState>("/risk"),
        requestJson<Reconciliation>("/v1/reconciliation"),
        requestJson<Fills>("/v1/fills?limit=8"),
      ]);
      if (cancelled) return;
      if (!runtime.ok && !risk.ok && !reconciliation.ok && !fills.ok) renderUnavailable(surface);
      else render(surface, runtime.data, risk.data, reconciliation.data, fills.data);
    } finally {
      inFlight = false;
    }
  }

  void refresh();
  const timer = window.setInterval(() => void refresh(), POLL_MS);
  return () => {
    cancelled = true;
    window.clearInterval(timer);
  };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startOperationalTelemetry, { once: true });
else startOperationalTelemetry();
