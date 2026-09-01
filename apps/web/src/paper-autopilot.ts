import "./paper-autopilot.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Side = "BUY" | "SELL";
type PaperStatus = {
  mode: string;
  running: boolean;
  paper_execution_enabled: boolean;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};
type LiveFrame = {
  timestamp: string;
  received_at?: string | null;
  symbol: SymbolName;
  bid: number;
  ask: number;
  bid_size: number;
  ask_size: number;
};
type LiveAnalytics = {
  realized_volatility: number;
  current_imbalance: number;
};
type SimulationResult = {
  accepted: boolean;
  reason: string;
  fill?: { fill_price: number; filled_quantity: number; fee: number; slippage_bps: number } | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const TICK_MS = 3000;
const REQUEST_TIMEOUT_MS = 3500;
const DEFAULT_THRESHOLD = 0.65;
const RESET_THRESHOLD_FACTOR = 0.5;
const DEFAULT_COOLDOWN_SECONDS = 20;
const DEFAULT_QUANTITY = 0.001;
const MAX_QUANTITY = 1000;
const MAX_SPREAD_BPS = 20;

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
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
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

function mountPaperAutopilot() {
  const host = document.querySelector<HTMLElement>(".automation .automationBody");
  if (!host || host.querySelector(".paperAutopilot")) return false;

  const section = document.createElement("section");
  section.className = "paperAutopilot";
  section.setAttribute("aria-label", "Rule based paper autopilot");

  const head = document.createElement("div");
  head.className = "paperAutoHead";
  const title = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = "RULE-BASED PAPER AUTOPILOT";
  const small = document.createElement("small");
  small.textContent = "Runs only while this dashboard session is open";
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

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "paperAutoToggle";
  toggle.textContent = "START AUTOPILOT";
  controls.append(thresholdLabel, cooldownLabel, quantityLabel, toggle);

  const telemetry = document.createElement("div");
  telemetry.className = "paperAutoTelemetry";
  const signal = document.createElement("span");
  const lastAction = document.createElement("span");
  const guard = document.createElement("span");
  signal.textContent = "signal —";
  lastAction.textContent = "last action —";
  guard.textContent = `guard spread ≤ ${MAX_SPREAD_BPS} bp`;
  telemetry.append(signal, lastAction, guard);

  const status = document.createElement("div");
  status.className = "paperAutoStatus idle";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = "Autopilot is off. Enable PAPER_TRADING first, then start this rule-based simulator.";

  const boundary = document.createElement("small");
  boundary.className = "paperAutoBoundary";
  boundary.textContent = "Simulation only · no exchange account · no financial connectivity · no real-money execution";

  section.append(head, controls, telemetry, status, boundary);
  host.append(section);

  let enabled = false;
  let cycleInFlight = false;
  let lastActionAt = 0;
  let armedDirection: Side | null = null;

  const renderToggle = () => {
    stateBadge.className = `paperAutoBadge ${enabled ? "on" : "off"}`;
    stateBadge.textContent = enabled ? "ACTIVE" : "OFF";
    toggle.textContent = enabled ? "STOP AUTOPILOT" : "START AUTOPILOT";
  };

  const validInputs = () => {
    const thresholdValue = Number(threshold.value);
    const cooldownSeconds = Number(cooldown.value);
    const quantityValue = Number(quantity.value);
    return {
      thresholdValue,
      cooldownSeconds,
      quantityValue,
      valid: Number.isFinite(thresholdValue)
        && thresholdValue >= 0.1
        && thresholdValue <= 0.95
        && Number.isFinite(cooldownSeconds)
        && cooldownSeconds >= 5
        && cooldownSeconds <= 300
        && Number.isFinite(quantityValue)
        && quantityValue > 0
        && quantityValue <= MAX_QUANTITY,
    };
  };

  const executeCycle = async () => {
    if (!enabled || cycleInFlight || document.hidden) return;
    cycleInFlight = true;
    try {
      const config = validInputs();
      if (!config.valid) {
        enabled = false;
        renderToggle();
        status.className = "paperAutoStatus error";
        status.textContent = "Autopilot stopped: trigger, cooldown or quantity is outside allowed bounds.";
        return;
      }

      const paper = await requestJson<PaperStatus>("/paper/status");
      const safePaper = paper.ok
        && paper.data?.paper_execution_enabled === true
        && paper.data.financial_connectivity === false
        && paper.data.real_money_execution === false;
      if (!safePaper) {
        status.className = "paperAutoStatus locked";
        status.textContent = "Autopilot waiting: authoritative PAPER_TRADING runtime is not enabled.";
        return;
      }

      const symbol = selectedSymbol();
      const [frameResult, analyticsResult] = await Promise.all([
        requestJson<LiveFrame>(`/live/market-data/${symbol}`),
        requestJson<LiveAnalytics>(`/live/analytics/${symbol}`),
      ]);
      if (!frameResult.ok || !frameResult.data || !analyticsResult.ok || !analyticsResult.data) {
        status.className = "paperAutoStatus error";
        status.textContent = "Autopilot skipped cycle: canonical live quote or analytics unavailable.";
        return;
      }

      const frame = frameResult.data;
      const imbalance = analyticsResult.data.current_imbalance;
      const volatility = analyticsResult.data.realized_volatility;
      if (!Number.isFinite(imbalance) || !Number.isFinite(volatility)) {
        status.className = "paperAutoStatus error";
        status.textContent = "Autopilot skipped cycle: non-finite analytics were rejected.";
        return;
      }
      const spreadBps = ((frame.ask - frame.bid) / Math.max(frame.ask, 1e-9)) * 10_000;
      signal.textContent = `${symbol} imbalance ${imbalance.toFixed(3)} · vol ${(volatility * 100).toFixed(3)}% · spread ${spreadBps.toFixed(2)} bp`;

      if (Math.abs(imbalance) < config.thresholdValue * RESET_THRESHOLD_FACTOR) {
        armedDirection = null;
      }
      if (Math.abs(imbalance) < config.thresholdValue) {
        status.className = "paperAutoStatus watching";
        status.textContent = `WATCHING ${symbol} · |imbalance| ${Math.abs(imbalance).toFixed(3)} below trigger ${config.thresholdValue.toFixed(2)}.`;
        return;
      }
      if (!Number.isFinite(spreadBps) || spreadBps > MAX_SPREAD_BPS) {
        status.className = "paperAutoStatus guarded";
        status.textContent = `GUARD HOLD · spread ${spreadBps.toFixed(2)} bp exceeds ${MAX_SPREAD_BPS} bp.`;
        return;
      }

      const side: Side = imbalance > 0 ? "BUY" : "SELL";
      if (armedDirection === side) {
        status.className = "paperAutoStatus watching";
        status.textContent = `SIGNAL ALREADY CONSUMED · waiting for imbalance to neutralize before another ${side}.`;
        return;
      }
      const cooldownMs = config.cooldownSeconds * 1000;
      if (Date.now() - lastActionAt < cooldownMs) {
        const remaining = Math.ceil((cooldownMs - (Date.now() - lastActionAt)) / 1000);
        status.className = "paperAutoStatus guarded";
        status.textContent = `COOLDOWN · ${remaining}s remaining before another simulated action.`;
        return;
      }

      const topSize = side === "BUY" ? frame.ask_size : frame.bid_size;
      if (!Number.isFinite(topSize) || topSize <= 0 || config.quantityValue > topSize) {
        status.className = "paperAutoStatus guarded";
        status.textContent = `LIQUIDITY GUARD · requested ${config.quantityValue} exceeds current ${side.toLowerCase()} top size ${Number.isFinite(topSize) ? topSize : 0}.`;
        return;
      }

      const marketId = `autopilot-${symbol.toLowerCase()}-usd`;
      const limitPrice = side === "BUY" ? frame.ask : frame.bid;
      const payload = {
        order: {
          market_id: marketId,
          asset: symbol,
          side,
          quantity: config.quantityValue,
          limit_price: limitPrice,
        },
        snapshot: {
          symbol,
          market_id: marketId,
          bid: frame.bid,
          ask: frame.ask,
          bid_size: frame.bid_size,
          ask_size: frame.ask_size,
          volatility: Math.max(volatility, 0),
          imbalance: Math.max(-1, Math.min(1, imbalance)),
          observed_at: frame.received_at || frame.timestamp,
        },
      };

      status.className = "paperAutoStatus working";
      status.textContent = `Submitting ${side} ${config.quantityValue} ${symbol} to the server-authoritative simulator…`;
      const result = await requestJson<SimulationResult>("/v1/simulate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      lastActionAt = Date.now();
      armedDirection = side;
      if (!result.ok || !result.data) {
        status.className = "paperAutoStatus error";
        status.textContent = result.status === 0
          ? "Autopilot simulation request failed before a server response."
          : `Autopilot simulation endpoint returned HTTP ${result.status}.`;
        return;
      }
      if (result.data.accepted && result.data.fill) {
        status.className = "paperAutoStatus accepted";
        status.textContent = `AUTO SIMULATED FILL · ${side} ${result.data.fill.filled_quantity} ${symbol} @ ${result.data.fill.fill_price} · slippage ${result.data.fill.slippage_bps.toFixed(2)} bp`;
        lastAction.textContent = `${new Date().toISOString().slice(11, 19)} UTC · ${side} ${symbol}`;
        window.dispatchEvent(new CustomEvent("proto:paper-fill", { detail: { symbol, source: "autopilot" } }));
      } else {
        status.className = "paperAutoStatus rejected";
        status.textContent = `AUTOPILOT REJECTED BY RISK GATE · ${result.data.reason}`;
        lastAction.textContent = `${new Date().toISOString().slice(11, 19)} UTC · rejected ${side} ${symbol}`;
      }
    } finally {
      cycleInFlight = false;
    }
  };

  toggle.addEventListener("click", () => {
    enabled = !enabled;
    armedDirection = null;
    renderToggle();
    status.className = enabled ? "paperAutoStatus watching" : "paperAutoStatus idle";
    status.textContent = enabled
      ? "Autopilot active. Waiting for a qualifying canonical imbalance signal."
      : "Autopilot stopped. No automatic simulation requests will be sent.";
    if (enabled) void executeCycle();
  });

  renderToggle();
  const timer = window.setInterval(() => void executeCycle(), TICK_MS);
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
