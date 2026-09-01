import "./operational-runtime.css";

type RuntimeState = { mode?: string; running?: boolean; kill_switch?: string; replay_speed?: number };
type RiskState = { kill_switch?: string; simulation_allowed?: boolean; real_money_execution?: boolean; minimum_net_edge?: number; minimum_confidence?: number; max_notional?: number; max_daily_drawdown?: number };
type Reconciliation = { mode?: string; consistent?: boolean; issues?: string[]; journal_fill_count?: number; authoritative_fill_count?: number };
type Fill = { order_id?: string; asset?: string; side?: string; filled_quantity?: number; fill_price?: number; fee?: number; slippage_bps?: number; filled_at?: string };
type Fills = { mode?: string; count?: number; fills?: Fill[] };
type ApiResult<T> = { ok: boolean; status: number; data: T | null };

type Snapshot = {
  runtime: ApiResult<RuntimeState>;
  risk: ApiResult<RiskState>;
  reconciliation: ApiResult<Reconciliation>;
  fills: ApiResult<Fills>;
  receivedAt: number;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const POLL_MS = 5000;
const REQUEST_TIMEOUT_MS = 2500;
const SNAPSHOT_STALE_MS = POLL_MS * 2.5;

async function requestJson<T>(path: string, parentSignal: AbortSignal): Promise<ApiResult<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const abort = () => controller.abort();
  parentSignal.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: controller.signal });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
    parentSignal.removeEventListener("abort", abort);
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

function endpointState(result: ApiResult<unknown>, label: string) {
  const badge = el("span", `opsEndpoint ${result.ok ? "ok" : result.status === 0 ? "warn" : "bad"}`);
  badge.textContent = result.ok ? `${label} OK` : result.status === 0 ? `${label} TIMEOUT` : `${label} HTTP ${result.status}`;
  return badge;
}

function ensureSurface() {
  let surface = document.querySelector<HTMLElement>(".operationalSurface");
  if (surface) return surface;
  const footer = document.querySelector<HTMLElement>("footer[data-section='SYSTEM']");
  if (!footer) return null;
  surface = el("section", "operationalSurface");
  surface.dataset.section = "SYSTEM";
  surface.setAttribute("aria-label", "Operational system telemetry");
  footer.before(surface);
  return surface;
}

function renderUnavailable(surface: HTMLElement, snapshot: Snapshot | null) {
  surface.replaceChildren();
  const panel = el("article", "opsPanel unavailable");
  panel.append(el("header", "", "OPERATIONAL TELEMETRY"));
  const text = snapshot
    ? "Runtime, risk, reconciliation and simulated-fill endpoints are currently unavailable. Last successful surface state has been discarded rather than presented as live."
    : "Operational telemetry is waiting for its first backend snapshot.";
  panel.append(el("p", "", text));
  surface.append(panel);
}

function render(surface: HTMLElement, snapshot: Snapshot) {
  const { runtime, risk, reconciliation, fills, receivedAt } = snapshot;
  surface.replaceChildren();

  const summary = el("div", "opsSummary");
  const now = Date.now();
  const stale = now - receivedAt > SNAPSHOT_STALE_MS;
  summary.append(
    el("strong", stale ? "warn" : "ok", stale ? "TELEMETRY STALE" : "TELEMETRY CURRENT"),
    el("span", "", `updated ${new Date(receivedAt).toISOString().slice(11, 19)} UTC`),
  );
  const endpointGroup = el("div", "opsEndpointGroup");
  endpointGroup.append(
    endpointState(runtime, "runtime"),
    endpointState(risk, "risk"),
    endpointState(reconciliation, "reconcile"),
    endpointState(fills, "fills"),
  );
  summary.append(endpointGroup);
  surface.append(summary);

  const runtimePanel = el("article", "opsPanel");
  runtimePanel.append(el("header", "", "RUNTIME / RISK POLICY"));
  const runtimeGrid = el("div", "opsMetrics");
  const runtimeData = runtime.data;
  const riskData = risk.data;
  const killSwitch = runtimeData?.kill_switch ?? riskData?.kill_switch ?? "—";
  const simAllowed = riskData?.simulation_allowed;
  runtimeGrid.append(
    metric("MODE", runtimeData?.mode ?? "—"),
    metric("RUNTIME", runtimeData?.running == null ? "—" : runtimeData.running ? "RUNNING" : "STOPPED", runtimeData?.running ? "ok" : "warn"),
    metric("KILL SWITCH", killSwitch, killSwitch === "ARMED" ? "ok" : killSwitch === "—" ? "warn" : "bad"),
    metric("SIMULATION", simAllowed == null ? "—" : simAllowed ? "ALLOWED" : "BLOCKED", simAllowed ? "ok" : "warn"),
    metric("MIN EDGE", percent(riskData?.minimum_net_edge)),
    metric("MIN CONF", percent(riskData?.minimum_confidence)),
    metric("MAX NOTIONAL", money(riskData?.max_notional)),
    metric("MAX DRAWDOWN", money(riskData?.max_daily_drawdown)),
  );
  runtimePanel.append(runtimeGrid);
  const boundaryText = riskData?.real_money_execution === false
    ? "REAL-MONEY EXECUTION FALSE · SIMULATION / PAPER BOUNDARY ENFORCED"
    : "FINANCIAL EXECUTION STATE UNAVAILABLE";
  runtimePanel.append(el("p", "opsBoundary", boundaryText));

  const reconcilePanel = el("article", "opsPanel");
  reconcilePanel.append(el("header", "", "PORTFOLIO RECONCILIATION"));
  const reconcileData = reconciliation.data;
  const reconcileState = reconcileData?.consistent;
  const reconcileHero = el("div", `reconcileHero ${reconcileState === true ? "ok" : reconcileState === false ? "bad" : "warn"}`);
  reconcileHero.append(el("strong", "", reconcileState == null ? "UNKNOWN" : reconcileState ? "CONSISTENT" : "MISMATCH"));
  reconcileHero.append(el("span", "", `journal ${reconcileData?.journal_fill_count ?? "—"} · authoritative ${reconcileData?.authoritative_fill_count ?? "—"}`));
  reconcilePanel.append(reconcileHero);
  const issues = reconcileData?.issues ?? [];
  const issueBox = el("div", "opsIssues");
  if (!reconciliation.ok) issueBox.append(el("span", "", "Reconciliation endpoint unavailable; no consistency claim is being made."));
  else if (!issues.length) issueBox.append(el("span", "", reconcileState === true ? "No reconciliation issues reported." : "No issue detail available."));
  else issues.slice(0, 5).forEach((issue) => issueBox.append(el("span", "", issue)));
  reconcilePanel.append(issueBox);

  const fillsPanel = el("article", "opsPanel fillsPanel");
  fillsPanel.append(el("header", "", "RECENT SIMULATED FILLS"));
  const rows = el("div", "fillRows");
  const fillData = fills.data;
  const fillList = fillData?.fills ?? [];
  if (!fills.ok) {
    rows.append(el("div", "fillEmpty", "Simulated fill journal endpoint unavailable."));
  } else if (!fillList.length) {
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
  fillsPanel.append(el("small", "fillCount", `${fillData?.count ?? fillList.length} simulated fills reported by /v1/fills`));

  surface.append(runtimePanel, reconcilePanel, fillsPanel);
}

function startOperationalTelemetry() {
  let cancelled = false;
  let inFlight = false;
  let refreshController: AbortController | null = null;
  let lastSnapshot: Snapshot | null = null;

  async function refresh() {
    if (cancelled || inFlight) return;
    const surface = ensureSurface();
    if (!surface) return;
    inFlight = true;
    refreshController = new AbortController();
    const signal = refreshController.signal;
    try {
      const [runtime, risk, reconciliation, fills] = await Promise.all([
        requestJson<RuntimeState>("/system/status", signal),
        requestJson<RiskState>("/risk", signal),
        requestJson<Reconciliation>("/v1/reconciliation", signal),
        requestJson<Fills>("/v1/fills?limit=8", signal),
      ]);
      if (cancelled || signal.aborted) return;
      const snapshot: Snapshot = { runtime, risk, reconciliation, fills, receivedAt: Date.now() };
      const anyOk = runtime.ok || risk.ok || reconciliation.ok || fills.ok;
      if (!anyOk) {
        lastSnapshot = null;
        renderUnavailable(surface, snapshot);
        return;
      }
      lastSnapshot = snapshot;
      render(surface, snapshot);
    } finally {
      if (refreshController?.signal === signal) refreshController = null;
      inFlight = false;
    }
  }

  void refresh();
  const timer = window.setInterval(() => void refresh(), POLL_MS);
  const staleTimer = window.setInterval(() => {
    if (cancelled || !lastSnapshot) return;
    const surface = ensureSurface();
    if (surface && Date.now() - lastSnapshot.receivedAt > SNAPSHOT_STALE_MS) render(surface, lastSnapshot);
  }, 1000);
  return () => {
    cancelled = true;
    refreshController?.abort();
    window.clearInterval(timer);
    window.clearInterval(staleTimer);
  };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startOperationalTelemetry, { once: true });
else startOperationalTelemetry();
