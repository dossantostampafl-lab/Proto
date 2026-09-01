import "./paper-mode-control.css";

type PaperStatus = {
  mode: string;
  running: boolean;
  kill_switch: string;
  paper_execution_enabled: boolean;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const TIMEOUT_MS = 3500;

async function request<T>(path: string, method: "GET" | "POST" = "GET") {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
    });
    return { ok: response.ok, status: response.status, data: response.ok ? await response.json() as T : null };
  } catch {
    return { ok: false, status: 0, data: null as T | null };
  } finally {
    window.clearTimeout(timeout);
  }
}

function mount() {
  const host = document.querySelector<HTMLElement>(".automationBody>div:first-child");
  if (!host || host.querySelector(".paperModeControl")) return false;

  const box = document.createElement("div");
  box.className = "paperModeControl";
  box.setAttribute("aria-label", "Paper automation runtime controls");

  const state = document.createElement("div");
  state.className = "paperModeStatus";
  state.setAttribute("role", "status");
  state.setAttribute("aria-live", "polite");

  const start = document.createElement("button");
  start.type = "button";
  start.className = "paperModeStart";
  start.textContent = "ENABLE PAPER AUTOMATION";

  const stop = document.createElement("button");
  stop.type = "button";
  stop.className = "paperModeStop";
  stop.textContent = "STOP PAPER AUTOMATION";

  box.append(state, start, stop);
  host.append(box);

  let busy = false;

  const render = (status: PaperStatus | null, error?: string) => {
    if (!status) {
      state.className = "paperModeStatus error";
      state.textContent = error ?? "Paper runtime status unavailable.";
      start.disabled = true;
      stop.disabled = true;
      return;
    }
    const safe = status.financial_connectivity === false && status.real_money_execution === false;
    const enabled = safe && status.paper_execution_enabled;
    state.className = `paperModeStatus ${enabled ? "enabled" : "locked"}`;
    state.textContent = enabled
      ? `PAPER AUTOMATION ENABLED · ${status.mode} · kill switch ${status.kill_switch}`
      : `PAPER AUTOMATION OFF · ${status.mode} · live market feed remains read-only`;
    start.disabled = busy || enabled || !safe;
    stop.disabled = busy || !enabled;
  };

  const refresh = async () => {
    if (busy) return;
    const result = await request<PaperStatus>("/paper/status");
    render(result.ok ? result.data : null, result.status === 0 ? "Paper control endpoint unavailable." : `Paper control HTTP ${result.status}`);
  };

  start.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    start.disabled = true;
    stop.disabled = true;
    state.className = "paperModeStatus working";
    state.textContent = "Enabling server-authoritative PAPER_TRADING runtime…";
    const result = await request<PaperStatus>("/paper/start", "POST");
    busy = false;
    if (!result.ok || !result.data) {
      render(null, result.status === 0 ? "Could not reach paper runtime control." : `Paper start HTTP ${result.status}`);
      return;
    }
    await refresh();
  });

  stop.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    start.disabled = true;
    stop.disabled = true;
    state.className = "paperModeStatus working";
    state.textContent = "Stopping simulated execution runtime…";
    const result = await request<PaperStatus>("/paper/stop", "POST");
    busy = false;
    if (!result.ok) {
      render(null, result.status === 0 ? "Could not reach paper runtime control." : `Paper stop HTTP ${result.status}`);
      return;
    }
    await refresh();
  });

  const timer = window.setInterval(() => void refresh(), 3000);
  void refresh();
  window.addEventListener("beforeunload", () => window.clearInterval(timer), { once: true });
  return true;
}

function startRuntime() {
  if (mount()) return;
  const observer = new MutationObserver(() => {
    if (mount()) observer.disconnect();
  });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startRuntime, { once: true });
else startRuntime();
