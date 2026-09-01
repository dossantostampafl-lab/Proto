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
type LiveAnalytics = {
  realized_volatility: number;
  current_imbalance: number;
};
type SimulationResult = {
  accepted: boolean;
  reason: string;
  fill?: {
    fill_price: number;
    filled_quantity: number;
    fee: number;
    slippage_bps: number;
  } | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const REQUEST_TIMEOUT_MS = 3500;
const MAX_QUANTITY = 1000;

function selectedSymbol(): SymbolName {
  const label = document.querySelector<HTMLElement>(".marketTile.active b")?.textContent ?? "BTC/USD";
  const symbol = label.split("/")[0]?.trim().toUpperCase();
  return symbol === "ETH" || symbol === "SOL" ? symbol : "BTC";
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T | null }> {
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
    return { ok: false, status: 0, data: null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function text(node: HTMLElement, value: string) {
  node.textContent = value;
}

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
  quote.append(quoteBid, quoteAsk, quoteBook);

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "paperSubmit";
  submit.textContent = "SIMULATE PAPER ORDER";

  const status = document.createElement("div");
  status.className = "paperResult idle";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = "Waiting for a fresh public quote.";

  const boundary = document.createElement("small");
  boundary.className = "paperBoundary";
  boundary.textContent = "No exchange credentials · no financial connectivity · no real-money execution";

  form.append(head, sideRow, fields, quote, submit, status, boundary);
  automation.append(form);

  let side: Side = "BUY";
  let lastFrame: LiveFrame | null = null;
  let refreshInFlight = false;
  let submitInFlight = false;

  const setSide = (next: Side) => {
    side = next;
    buy.classList.toggle("active", side === "BUY");
    sell.classList.toggle("active", side === "SELL");
    buy.setAttribute("aria-pressed", String(side === "BUY"));
    sell.setAttribute("aria-pressed", String(side === "SELL"));
    if (lastFrame) limit.value = String(side === "BUY" ? lastFrame.ask : lastFrame.bid);
  };

  buy.addEventListener("click", () => setSide("BUY"));
  sell.addEventListener("click", () => setSide("SELL"));
  setSide("BUY");

  const refreshQuote = async () => {
    if (refreshInFlight || submitInFlight) return;
    refreshInFlight = true;
    try {
      const symbol = selectedSymbol();
      text(symbolBadge, `${symbol}/USD · PAPER`);
      const frameResult = await jsonRequest<LiveFrame>(`/live/market-data/${symbol}`);
      if (!frameResult.ok || !frameResult.data) {
        lastFrame = null;
        text(quoteBid, "bid —");
        text(quoteAsk, "ask —");
        text(quoteBook, "top size —");
        status.className = "paperResult error";
        text(status, frameResult.status === 0 ? "Public quote request unavailable." : `Public quote unavailable (${frameResult.status}).`);
        submit.disabled = true;
        return;
      }
      lastFrame = frameResult.data;
      text(quoteBid, `bid ${frameResult.data.bid.toLocaleString("en-US", { maximumFractionDigits: 8 })}`);
      text(quoteAsk, `ask ${frameResult.data.ask.toLocaleString("en-US", { maximumFractionDigits: 8 })}`);
      const topSize = side === "BUY" ? frameResult.data.ask_size : frameResult.data.bid_size;
      text(quoteBook, `${side.toLowerCase()} top ${topSize.toLocaleString("en-US", { maximumFractionDigits: 6 })}`);
      if (!limit.matches(":focus")) limit.value = String(side === "BUY" ? frameResult.data.ask : frameResult.data.bid);
      submit.disabled = false;
      if (!status.classList.contains("accepted") && !status.classList.contains("rejected")) {
        status.className = "paperResult ready";
        text(status, "Fresh public quote loaded. Risk and portfolio state are enforced by the server.");
      }
    } finally {
      refreshInFlight = false;
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitInFlight) return;
    const symbol = selectedSymbol();
    const quantityValue = Number(quantity.value);
    const limitValue = Number(limit.value);
    if (!lastFrame || lastFrame.symbol !== symbol) {
      status.className = "paperResult error";
      text(status, "Selected market changed. Refreshing quote before simulation.");
      await refreshQuote();
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
    submit.disabled = true;
    status.className = "paperResult working";
    text(status, "Submitting to backend risk gate and execution simulator…");
    try {
      const analytics = await jsonRequest<LiveAnalytics>(`/live/analytics/${symbol}`);
      if (!analytics.ok || !analytics.data || !Number.isFinite(analytics.data.realized_volatility) || !Number.isFinite(analytics.data.current_imbalance)) {
        status.className = "paperResult error";
        text(status, "Canonical live analytics are unavailable; simulation was not submitted.");
        return;
      }
      const volatility = Math.max(analytics.data.realized_volatility, 0);
      const imbalance = Math.max(-1, Math.min(1, analytics.data.current_imbalance));
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
          volatility,
          imbalance,
          observed_at: lastFrame.received_at || lastFrame.timestamp,
        },
        server_execution_permitted: true,
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
      submit.disabled = lastFrame === null;
      window.setTimeout(() => void refreshQuote(), 250);
    }
  });

  const timer = window.setInterval(() => void refreshQuote(), 2000);
  const selectionObserver = new MutationObserver(() => void refreshQuote());
  document.querySelectorAll(".marketTile").forEach((tile) => selectionObserver.observe(tile, { attributes: true, attributeFilter: ["class"] }));
  void refreshQuote();

  window.addEventListener("beforeunload", () => {
    window.clearInterval(timer);
    selectionObserver.disconnect();
  }, { once: true });
  return true;
}

function startPaperOrderRuntime() {
  if (mountPaperOrderConsole()) return;
  const observer = new MutationObserver(() => {
    if (mountPaperOrderConsole()) observer.disconnect();
  });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startPaperOrderRuntime, { once: true });
else startPaperOrderRuntime();
