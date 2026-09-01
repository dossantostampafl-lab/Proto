import "./paper-order-runtime.css";

type SymbolName = "BTC" | "ETH" | "SOL";
type Side = "BUY" | "SELL";
type LiveFrame = {
  timestamp: string;
  received_at?: string | null;
  symbol: SymbolName;
  bid: number;
  ask: number;
  mid: number;
  bid_size: number;
  ask_size: number;
};
type LiveAnalytics = { realized_volatility: number; current_imbalance: number };
type RiskState = { simulation_allowed: boolean; real_money_execution: boolean };
type RuntimeState = { mode: string; running: boolean };
type LiveBoundary = {
  running: boolean;
  receiving_data: boolean;
  all_symbols_fresh: boolean;
  fresh_symbols: string[];
  last_receipt_age_seconds: number | null;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};
type SimulationResult = {
  accepted: boolean;
  reason: string;
  fill?: { fill_price: number; filled_quantity: number; fee: number; slippage_bps: number } | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const REQUEST_TIMEOUT_MS = 3500;
const LIVE_FRAME_TTL_MS = 7500;
const MAX_QUANTITY = 1000;
const SIMULATION_MODES = new Set(["SIMULATION", "PAPER_TRADING"]);

function selectedSymbol(): SymbolName {
  const label = document.querySelector<HTMLElement>(".marketTile.active b")?.textContent ?? "BTC/USD";
  const symbol = label.split("/")[0]?.trim().toUpperCase();
  return symbol === "ETH" || symbol === "SOL" ? symbol : "BTC";
}

function frameObservedAt(frame: LiveFrame | null) {
  if (!frame) return Number.NaN;
  return Date.parse(frame.received_at || frame.timestamp);
}

function frameIsFresh(frame: LiveFrame | null) {
  const observedAt = frameObservedAt(frame);
  return Number.isFinite(observedAt) && Date.now() - observedAt >= 0 && Date.now() - observedAt <= LIVE_FRAME_TTL_MS;
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T | null }> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const response = await fetch(`${API_BASE}${path}`, { ...init, cache: "no-store", signal: controller.signal, headers });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function text(node: HTMLElement, value: string) { node.textContent = value; }
function createButton(label: string, side: Side) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `paperSide ${side.toLowerCase()}`;
  button.dataset.side = side;
  button.textContent = label;
  return button;
}

function mountPaperOrderConsole() {
  const automation = document.querySelector<HTMLElement>(".automation .automationBody");
  if (!automation || automation.querySelector(".paperOrderConsole")) return false;

  const form = document.createElement("form");
  form.className = "paperOrderConsole";
  form.setAttribute("aria-label", "Paper simulation order console");

  const head = document.createElement("div");
  head.className = "paperOrderHead";
  const title = document.createElement("div");
  const titleStrong = document.createElement("strong");
  titleStrong.textContent = "PAPER ORDER CONSOLE";
  const titleSmall = document.createElement("small");
  titleSmall.textContent = "Backend-authoritative simulation only";
  title.append(titleStrong, titleSmall);
  const symbolBadge = document.createElement("span");
  symbolBadge.className = "paperSymbol";
  head.append(title, symbolBadge);

  const sideRow = document.createElement("div");
  sideRow.className = "paperSides";
  const buy = createButton("BUY", "BUY");
  const sell = createButton("SELL", "SELL");
  sideRow.append(buy, sell);

  const fields = document.createElement("div");
  fields.className = "paperFields";
  const quantityLabel = document.createElement("label");
  quantityLabel.textContent = "QUANTITY";
  const quantity = document.createElement("input");
  quantity.type = "number";
  quantity.min = "0.000001";
  quantity.max = String(MAX_QUANTITY);
  quantity.step = "0.000001";
  quantity.value = "0.001";
  quantity.inputMode = "decimal";
  quantityLabel.append(quantity);
  const limitLabel = document.createElement("label");
  limitLabel.textContent = "LIMIT PRICE";
  const limit = document.createElement("input");
  limit.type = "number";
  limit.min = "0.00000001";
  limit.step = "0.01";
  limit.inputMode = "decimal";
  limitLabel.append(limit);
  fields.append(quantityLabel, limitLabel);

  const quote = document.createElement("div");
  quote.className = "paperQuote";
  const quoteBid = document.createElement("span");
  const quoteAsk = document.createElement("span");
  const quoteBook = document.createElement("span");
  const modeState = document.createElement("span");
  modeState.className = "paperModeState";
  quote.append(quoteBid, quoteAsk, quoteBook, modeState);

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "paperSubmit";
  submit.textContent = "SIMULATE PAPER ORDER";
  submit.disabled = true;

  const status = document.createElement("div");
  status.className = "paperResult idle";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = "Checking public quote and authoritative simulation mode.";
  const boundary = document.createElement("small");
  boundary.className = "paperBoundary";
  boundary.textContent = "No exchange credentials · no financial connectivity · no real-money execution";
  form.append(head, sideRow, fields, quote, submit, status, boundary);
  automation.append(form);

  let side: Side = "BUY";
  let lastFrame: LiveFrame | null = null;
  let riskState: RiskState | null = null;
  let runtimeState: RuntimeState | null = null;
  let liveBoundary: LiveBoundary | null = null;
  let refreshInFlight = false;
  let submitInFlight = false;

  const liveSymbolFresh = () => Boolean(
    lastFrame
      && liveBoundary?.running
      && liveBoundary.receiving_data
      && (liveBoundary.all_symbols_fresh || liveBoundary.fresh_symbols?.includes(lastFrame.symbol))
      && frameIsFresh(lastFrame),
  );
  const simulationPermitted = () => Boolean(
    riskState?.simulation_allowed
      && riskState.real_money_execution === false
      && runtimeState?.running
      && SIMULATION_MODES.has(runtimeState.mode)
      && liveBoundary?.financial_connectivity === false
      && liveBoundary.real_money_execution === false
      && liveSymbolFresh(),
  );
  const updateSubmitState = () => { submit.disabled = submitInFlight || !simulationPermitted(); };
  const updateBookForSide = () => {
    if (!lastFrame) {
      text(quoteBook, "top size —");
      return;
    }
    const topSize = side === "BUY" ? lastFrame.ask_size : lastFrame.bid_size;
    text(quoteBook, `${side.toLowerCase()} top ${topSize.toLocaleString("en-US", { maximumFractionDigits: 6 })}`);
  };
  const setSide = (next: Side) => {
    side = next;
    buy.classList.toggle("active", side === "BUY");
    sell.classList.toggle("active", side === "SELL");
    buy.setAttribute("aria-pressed", String(side === "BUY"));
    sell.setAttribute("aria-pressed", String(side === "SELL"));
    if (lastFrame) limit.value = String(side === "BUY" ? lastFrame.ask : lastFrame.bid);
    updateBookForSide();
  };
  buy.addEventListener("click", () => setSide("BUY"));
  sell.addEventListener("click", () => setSide("SELL"));
  setSide("BUY");

  const refreshContext = async () => {
    if (refreshInFlight || submitInFlight) return;
    refreshInFlight = true;
    try {
      const symbol = selectedSymbol();
      text(symbolBadge, `${symbol}/USD · PAPER`);
      const [frameResult, riskResult, runtimeResult, liveResult] = await Promise.all([
        jsonRequest<LiveFrame>(`/live/market-data/${symbol}`),
        jsonRequest<RiskState>("/risk"),
        jsonRequest<RuntimeState>("/system/status"),
        jsonRequest<LiveBoundary>("/live/status"),
      ]);
      riskState = riskResult.ok ? riskResult.data : null;
      runtimeState = runtimeResult.ok ? runtimeResult.data : null;
      liveBoundary = liveResult.ok ? liveResult.data : null;
      text(modeState, runtimeState ? `exec ${runtimeState.mode}` : "exec mode unavailable");

      if (!frameResult.ok || !frameResult.data) {
        lastFrame = null;
        text(quoteBid, "bid —"); text(quoteAsk, "ask —"); updateBookForSide();
        status.className = "paperResult error";
        text(status, frameResult.status === 0 ? "Public quote request unavailable." : `Public quote unavailable (${frameResult.status}).`);
        updateSubmitState();
        return;
      }
      lastFrame = frameResult.data;
      text(quoteBid, `bid ${frameResult.data.bid.toLocaleString("en-US", { maximumFractionDigits: 8 })}`);
      text(quoteAsk, `ask ${frameResult.data.ask.toLocaleString("en-US", { maximumFractionDigits: 8 })}`);
      updateBookForSide();
      if (!limit.matches(":focus")) limit.value = String(side === "BUY" ? frameResult.data.ask : frameResult.data.bid);

      if (!riskState || !runtimeState || !liveBoundary) {
        status.className = "paperResult error";
        text(status, "Authoritative runtime or safety state is unavailable; simulation is locked.");
      } else if (!liveSymbolFresh()) {
        status.className = "paperResult locked";
        const age = liveBoundary.last_receipt_age_seconds;
        text(status, age != null && Number.isFinite(age)
          ? `PUBLIC QUOTE STALE · last receipt ${age.toFixed(2)}s ago · simulation locked until a fresh ${symbol} quote arrives.`
          : `PUBLIC QUOTE STALE · simulation locked until a fresh ${symbol} quote arrives.`);
      } else if (!simulationPermitted()) {
        status.className = "paperResult locked";
        text(status, `SIMULATOR LOCKED IN ${runtimeState.mode} · server/safety policy does not permit a simulated fill.`);
      } else if (!status.classList.contains("accepted") && !status.classList.contains("rejected")) {
        status.className = "paperResult ready";
        text(status, "Fresh public quote loaded. Backend simulation/risk gate is enabled for this execution runtime.");
      }
      updateSubmitState();
    } finally { refreshInFlight = false; }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitInFlight) return;
    if (!liveSymbolFresh()) {
      status.className = "paperResult locked";
      text(status, "PUBLIC QUOTE STALE · no order was submitted. Waiting for a fresh public market frame.");
      updateSubmitState();
      return;
    }
    if (!simulationPermitted()) {
      status.className = "paperResult locked";
      text(status, `SIMULATOR LOCKED IN ${runtimeState?.mode ?? "UNKNOWN"} · no order was submitted.`);
      updateSubmitState();
      return;
    }
    const symbol = selectedSymbol();
    const quantityValue = Number(quantity.value);
    const limitValue = Number(limit.value);
    if (!lastFrame || lastFrame.symbol !== symbol) {
      status.className = "paperResult error";
      text(status, "Selected market changed. Refreshing quote before simulation.");
      await refreshContext();
      return;
    }
    if (!Number.isFinite(quantityValue) || quantityValue <= 0 || quantityValue > MAX_QUANTITY) {
      status.className = "paperResult error";
      text(status, `Quantity must be greater than 0 and at most ${MAX_QUANTITY}.`);
      quantity.focus();
      return;
    }
    if (!Number.isFinite(limitValue) || limitValue <= 0) {
      status.className = "paperResult error";
      text(status, "Limit price must be a positive finite number.");
      limit.focus();
      return;
    }

    submitInFlight = true;
    updateSubmitState();
    status.className = "paperResult working";
    text(status, "Submitting to backend risk gate and execution simulator…");
    try {
      const analytics = await jsonRequest<LiveAnalytics>(`/live/analytics/${symbol}`);
      if (!analytics.ok || !analytics.data || !Number.isFinite(analytics.data.realized_volatility) || !Number.isFinite(analytics.data.current_imbalance)) {
        status.className = "paperResult error";
        text(status, "Canonical live analytics are unavailable; simulation was not submitted.");
        return;
      }
      if (!liveSymbolFresh()) {
        status.className = "paperResult locked";
        text(status, "PUBLIC QUOTE BECAME STALE · simulation was not submitted.");
        return;
      }
      const marketId = `paper-${symbol.toLowerCase()}-usd`;
      const payload = {
        order: { market_id: marketId, asset: symbol, side, quantity: quantityValue, limit_price: limitValue },
        snapshot: {
          symbol,
          market_id: marketId,
          bid: lastFrame.bid,
          ask: lastFrame.ask,
          bid_size: lastFrame.bid_size,
          ask_size: lastFrame.ask_size,
          volatility: Math.max(analytics.data.realized_volatility, 0),
          imbalance: Math.max(-1, Math.min(1, analytics.data.current_imbalance)),
          observed_at: lastFrame.received_at || lastFrame.timestamp,
        },
      };
      const result = await jsonRequest<SimulationResult>("/v1/simulate", { method: "POST", body: JSON.stringify(payload) });
      if (!result.ok || !result.data) {
        status.className = "paperResult error";
        text(status, result.status === 0 ? "Simulation request failed before a server response." : `Simulation endpoint returned ${result.status}.`);
        return;
      }
      if (result.data.accepted && result.data.fill) {
        status.className = "paperResult accepted";
        text(status, `SIMULATED FILL · ${side} ${result.data.fill.filled_quantity} ${symbol} @ ${result.data.fill.fill_price} · fee ${result.data.fill.fee.toFixed(4)} · slippage ${result.data.fill.slippage_bps.toFixed(2)} bp`);
        window.dispatchEvent(new CustomEvent("proto:paper-fill", { detail: { symbol } }));
      } else {
        status.className = "paperResult rejected";
        text(status, `REJECTED BY SIMULATION/RISK GATE · ${result.data.reason}`);
      }
    } finally {
      submitInFlight = false;
      updateSubmitState();
      window.setTimeout(() => void refreshContext(), 250);
    }
  });

  const timer = window.setInterval(() => void refreshContext(), 2000);
  const selectionObserver = new MutationObserver(() => void refreshContext());
  document.querySelectorAll(".marketTile").forEach((tile) => selectionObserver.observe(tile, { attributes: true, attributeFilter: ["class"] }));
  void refreshContext();
  window.addEventListener("beforeunload", () => { window.clearInterval(timer); selectionObserver.disconnect(); }, { once: true });
  return true;
}

function startPaperOrderRuntime() {
  if (mountPaperOrderConsole()) return;
  const observer = new MutationObserver(() => { if (mountPaperOrderConsole()) observer.disconnect(); });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startPaperOrderRuntime, { once: true });
else startPaperOrderRuntime();
