import "./paper-autopilot.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type PaperStatus = {
  mode: string;
  running: boolean;
  paper_execution_enabled: boolean;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};
type AutopilotConfig = {
  symbol: SymbolName;
  imbalance_trigger: number;
  cooldown_seconds: number;
  quantity: number;
  max_spread_bps: number;
};
type AutopilotStatus = {
  mode: string;
  running: boolean;
  paper_runtime_ready: boolean;
  kill_switch: string;
  config: AutopilotConfig;
  started_at?: string | null;
  last_cycle_at?: string | null;
  last_action_at?: string | null;
  last_reason: string;
  last_signal?: {
    symbol?: string;
    imbalance?: number;
    realized_volatility?: number;
    spread_bps?: number;
    observed_at?: string | null;
  } | null;
  last_result?: {
    accepted?: boolean;
    reason?: string;
    fill?: {
      asset?: string;
      side?: string;
      filled_quantity?: number;
      fill_price?: number;
      slippage_bps?: number;
    } | null;
  } | null;
  counters: {
    cycles: number;
    signals: number;
    submissions: number;
    accepted: number;
    rejected: number;
    errors: number;
  };
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const REQUEST_TIMEOUT_MS = 3500;
const STATUS_MS = 2000;
const DEFAULT_THRESHOLD = 0.65;
const DEFAULT_COOLDOWN_SECONDS = 20;
const DEFAULT_QUANTITY = 0.001;
const DEFAULT_MAX_SPREAD_BPS = 20;
const MAX_QUANTITY = 1000;

async function requestJson<T>(path: string, init?: RequestInit) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      signal: controller.signal,
      headers,
    });
    let data: T | null = null;
    if (response.ok) data = await response.json() as T;
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: null as T | null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function selectedSymbol(): SymbolName {
  const label = document.querySelector<HTMLElement>(".marketTile.active b")?.textContent ?? "BTC/USD";
  const symbol = label.split("/")[0]?.trim().toUpperCase();
  return symbol === "ETH" || symbol === "SOL" ? symbol : "BTC";
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function mountPaperAutopilot() {
  const host = document.querySelector<HTMLElement>(".automation .automationBody");
  if (!host || host.querySelector(".paperAutopilot")) return false;

  const section = document.createElement("section");
  section.className = "paperAutopilot";
  section.setAttribute("aria-label", "Server paper autopilot");

  const head = document.createElement("div");
  head.className = "paperAutoHead";
  const title = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = "SERVER PAPER AUTOPILOT";
  const small = document.createElement("small");
  small.textContent = "Continues on the server after this dashboard tab is closed";
  title.append(strong, small);
  const stateBadge = document.createElement("span");
  stateBadge.className = "paperAutoBadge off";
  stateBadge.textContent = "OFF";
  head.append(title, stateBadge);

  const controls = document.createElement("div");
  controls.className = "paperAutoControls";
  const thresholdLabel = document.createElement("label");
  thresholdLabel.textContent = "IMBALANCE TRIGGER";
  const threshold = document.createElement("input");
  threshold.type = "number";
  threshold.min = "0.10";
  threshold.max = "0.95";
  threshold.step = "0.05";
  threshold.value = String(DEFAULT_THRESHOLD);
  thresholdLabel.append(threshold);

  const cooldownLabel = document.createElement("label");
  cooldownLabel.textContent = "COOLDOWN (S)";
  const cooldown = document.createElement("input");
  cooldown.type = "number";
  cooldown.min = "5";
  cooldown.max = "300";
  cooldown.step = "5";
  cooldown.value = String(DEFAULT_COOLDOWN_SECONDS);
  cooldownLabel.append(cooldown);

  const quantityLabel = document.createElement("label");
  quantityLabel.textContent = "QUANTITY";
  const quantity = document.createElement("input");
  quantity.type = "number";
  quantity.min = "0.000001";
  quantity.max = String(MAX_QUANTITY);
  quantity.step = "0.000001";
  quantity.value = String(DEFAULT_QUANTITY);
  quantityLabel.append(quantity);

  const spreadLabel = document.createElement("label");
  spreadLabel.textContent = "MAX SPREAD (BP)";
  const maxSpread = document.createElement("input");
  maxSpread.type = "number";
  maxSpread.min = "0.01";
  maxSpread.max = "75";
  maxSpread.step = "0.5";
  maxSpread.value = String(DEFAULT_MAX_SPREAD_BPS);
  spreadLabel.append(maxSpread);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "paperAutoToggle";
  toggle.textContent = "START SERVER AUTOPILOT";
  controls.append(thresholdLabel, cooldownLabel, quantityLabel, spreadLabel, toggle);

  const telemetry = document.createElement("div");
  telemetry.className = "paperAutoTelemetry";
  const signal = document.createElement("span");
  const lastAction = document.createElement("span");
  const counters = document.createElement("span");
  signal.textContent = "signal —";
  lastAction.textContent = "last action —";
  counters.textContent = "cycles 0 · fills 0 · rejected 0";
  telemetry.append(signal, lastAction, counters);

  const status = document.createElement("div");
  status.className = "paperAutoStatus idle";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = "Server autopilot is off.";

  const boundary = document.createElement("small");
  boundary.className = "paperAutoBoundary";
  boundary.textContent = "Persistent server task · simulation only · no exchange account · no financial connectivity · no real-money execution";
  section.append(head, controls, telemetry, status, boundary);
  host.append(section);

  let busy = false;
  let active = false;
  let hydratedFromServer = false;

  const setInputsDisabled = (disabled: boolean) => {
    threshold.disabled = disabled;
    cooldown.disabled = disabled;
    quantity.disabled = disabled;
    maxSpread.disabled = disabled;
  };

  const configFromInputs = (): AutopilotConfig | null => {
    const imbalance_trigger = Number(threshold.value);
    const cooldown_seconds = Number(cooldown.value);
    const quantityValue = Number(quantity.value);
    const max_spread_bps = Number(maxSpread.value);
    if (
      !Number.isFinite(imbalance_trigger) || imbalance_trigger < 0.1 || imbalance_trigger > 0.95
      || !Number.isFinite(cooldown_seconds) || cooldown_seconds < 5 || cooldown_seconds > 300
      || !Number.isFinite(quantityValue) || quantityValue <= 0 || quantityValue > MAX_QUANTITY
      || !Number.isFinite(max_spread_bps) || max_spread_bps < 0.01 || max_spread_bps > 75
    ) return null;
    return {
      symbol: selectedSymbol(),
      imbalance_trigger,
      cooldown_seconds,
      quantity: quantityValue,
      max_spread_bps,
    };
  };

  const hydrateInputs = (server: AutopilotStatus) => {
    if (hydratedFromServer || !server.config) return;
    threshold.value = String(server.config.imbalance_trigger);
    cooldown.value = String(server.config.cooldown_seconds);
    quantity.value = String(server.config.quantity);
    maxSpread.value = String(server.config.max_spread_bps);
    hydratedFromServer = true;
  };

  const render = (server: AutopilotStatus | null, error?: string) => {
    if (!server) {
      active = false;
      stateBadge.className = "paperAutoBadge off";
      stateBadge.textContent = "UNKNOWN";
      toggle.textContent = "START SERVER AUTOPILOT";
      toggle.disabled = busy;
      setInputsDisabled(busy);
      status.className = "paperAutoStatus error";
      status.textContent = error ?? "Server autopilot status unavailable.";
      return;
    }
    hydrateInputs(server);
    const safe = server.financial_connectivity === false && server.real_money_execution === false;
    active = safe && server.running;
    stateBadge.className = `paperAutoBadge ${active ? "on" : "off"}`;
    stateBadge.textContent = active ? "SERVER ACTIVE" : "OFF";
    toggle.textContent = active ? "STOP SERVER AUTOPILOT" : "START SERVER AUTOPILOT";
    toggle.disabled = busy || !safe;
    setInputsDisabled(active || busy);

    const s = server.last_signal;
    signal.textContent = s && finite(s.imbalance) && finite(s.spread_bps)
      ? `${s.symbol ?? server.config.symbol} imbalance ${s.imbalance.toFixed(3)} · spread ${s.spread_bps.toFixed(2)} bp`
      : "signal —";
    const fill = server.last_result?.fill;
    lastAction.textContent = fill
      ? `${fill.side ?? "—"} ${fill.asset ?? server.config.symbol} ${fill.filled_quantity ?? "—"} @ ${fill.fill_price ?? "—"}`
      : server.last_action_at
        ? `last action ${server.last_action_at.slice(11, 19)} UTC`
        : "last action —";
    counters.textContent = `cycles ${server.counters.cycles} · fills ${server.counters.accepted} · rejected ${server.counters.rejected}`;

    const reason = server.last_reason.replaceAll("_", " ");
    if (active && server.paper_runtime_ready) {
      status.className = server.last_reason.startsWith("RISK_REJECTED") ? "paperAutoStatus rejected" : server.last_reason === "SIMULATED_FILL" ? "paperAutoStatus accepted" : "paperAutoStatus watching";
      status.textContent = `SERVER AUTOPILOT ACTIVE · ${server.config.symbol} · ${reason}`;
    } else if (active) {
      status.className = "paperAutoStatus locked";
      status.textContent = `SERVER AUTOPILOT PAUSED · ${reason} · enable PAPER_TRADING to resume decisions.`;
    } else {
      status.className = "paperAutoStatus idle";
      status.textContent = `Server autopilot off · ${reason}`;
    }
  };

  const refresh = async () => {
    if (busy) return;
    const result = await requestJson<AutopilotStatus>("/paper/automation/status");
    render(result.ok ? result.data : null, result.status === 0 ? "Server autopilot endpoint unavailable." : `Autopilot status HTTP ${result.status}`);
  };

  const ensurePaperRuntime = async () => {
    const current = await requestJson<PaperStatus>("/paper/status");
    if (!current.ok || !current.data) return { ok: false, message: current.status === 0 ? "Paper runtime endpoint unavailable." : `Paper runtime HTTP ${current.status}` };
    if (current.data.financial_connectivity !== false || current.data.real_money_execution !== false) return { ok: false, message: "Paper runtime safety boundary is not satisfied." };
    if (current.data.paper_execution_enabled) return { ok: true, message: "ready" };
    const started = await requestJson<PaperStatus>("/paper/start", { method: "POST" });
    if (!started.ok || !started.data?.paper_execution_enabled) return { ok: false, message: started.status === 0 ? "Could not enable PAPER_TRADING runtime." : `Paper start HTTP ${started.status}` };
    return { ok: true, message: "ready" };
  };

  toggle.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    toggle.disabled = true;
    setInputsDisabled(true);
    if (active) {
      status.className = "paperAutoStatus working";
      status.textContent = "Stopping persistent server autopilot…";
      const stopped = await requestJson<AutopilotStatus>("/paper/automation/stop", { method: "POST" });
      busy = false;
      render(stopped.ok ? stopped.data : null, stopped.status === 0 ? "Could not reach server autopilot." : `Autopilot stop HTTP ${stopped.status}`);
      return;
    }

    const config = configFromInputs();
    if (!config) {
      busy = false;
      setInputsDisabled(false);
      status.className = "paperAutoStatus error";
      status.textContent = "Autopilot configuration is outside the allowed trigger, cooldown, quantity or spread bounds.";
      toggle.disabled = false;
      return;
    }

    status.className = "paperAutoStatus working";
    status.textContent = "Enabling PAPER_TRADING and starting the persistent server autopilot…";
    const paper = await ensurePaperRuntime();
    if (!paper.ok) {
      busy = false;
      setInputsDisabled(false);
      status.className = "paperAutoStatus error";
      status.textContent = paper.message;
      toggle.disabled = false;
      return;
    }

    const started = await requestJson<AutopilotStatus>("/paper/automation/start", {
      method: "POST",
      body: JSON.stringify(config),
    });
    busy = false;
    render(started.ok ? started.data : null, started.status === 409 ? "PAPER_TRADING did not become authoritative before autopilot start; try again." : started.status === 0 ? "Could not reach server autopilot." : `Autopilot start HTTP ${started.status}`);
  });

  const timer = window.setInterval(() => void refresh(), STATUS_MS);
  void refresh();
  window.addEventListener("beforeunload", () => window.clearInterval(timer), { once: true });
  return true;
}

function startPaperAutopilot() {
  if (mountPaperAutopilot()) return;
  const observer = new MutationObserver(() => {
    if (mountPaperAutopilot()) observer.disconnect();
  });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startPaperAutopilot, { once: true });
else startPaperAutopilot();
