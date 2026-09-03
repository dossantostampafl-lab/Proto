type ShadowStatus = {
  mode?: string;
  running?: boolean;
  shadow_execution_enabled?: boolean;
  financial_connectivity?: boolean;
  real_money_execution?: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const POLL_MS = 3000;

async function request<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

function installStyle(): void {
  const style = document.createElement("style");
  style.textContent = `
    .proto-shadow-operator{position:fixed;right:18px;bottom:18px;z-index:1000;width:min(360px,calc(100vw - 36px));border:1px solid rgba(113,167,255,.25);background:rgba(5,8,17,.94);backdrop-filter:blur(18px);box-shadow:0 24px 80px rgba(0,0,0,.42);border-radius:14px;color:#dbe8ff;font:12px/1.35 Inter,system-ui,sans-serif;overflow:hidden}
    .proto-shadow-head{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;border-bottom:1px solid rgba(126,138,156,.18);letter-spacing:.08em}.proto-shadow-head b{font-size:12px}.proto-shadow-pill{padding:3px 7px;border-radius:999px;background:rgba(113,167,255,.12);color:#71a7ff;font-size:10px;font-weight:800}.proto-shadow-pill.on{background:rgba(92,227,155,.12);color:#5ce39b}.proto-shadow-body{padding:12px 13px}.proto-shadow-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}.proto-shadow-metric{padding:8px;border:1px solid rgba(126,138,156,.14);border-radius:9px;background:rgba(11,19,32,.7)}.proto-shadow-metric span{display:block;color:#7e8a9c;font-size:9px;letter-spacing:.08em;margin-bottom:3px}.proto-shadow-metric b{font-size:11px}.proto-shadow-actions{display:flex;gap:8px}.proto-shadow-actions button{flex:1;border:1px solid rgba(113,167,255,.25);border-radius:8px;padding:8px 10px;background:#0b1320;color:#dbe8ff;font-weight:800;cursor:pointer}.proto-shadow-actions button.primary{background:rgba(92,227,155,.12);border-color:rgba(92,227,155,.28);color:#5ce39b}.proto-shadow-actions button:disabled{opacity:.45;cursor:wait}.proto-shadow-note{margin-top:9px;color:#7e8a9c;font-size:10px}.proto-shadow-note strong{color:#e8c15a}.proto-shadow-error{color:#f05f65}
  `;
  document.head.appendChild(style);
}

function mount(): void {
  installStyle();
  const panel = document.createElement("section");
  panel.className = "proto-shadow-operator";
  panel.setAttribute("aria-label", "SHADOW operator control");
  panel.innerHTML = `
    <div class="proto-shadow-head"><b>SHADOW CONTROL</b><span class="proto-shadow-pill" data-shadow-state>CHECKING</span></div>
    <div class="proto-shadow-body">
      <div class="proto-shadow-grid">
        <div class="proto-shadow-metric"><span>MODE</span><b data-shadow-mode>—</b></div>
        <div class="proto-shadow-metric"><span>ENGINE</span><b data-shadow-engine>—</b></div>
        <div class="proto-shadow-metric"><span>FINANCIAL CONNECTIVITY</span><b data-shadow-financial>FALSE</b></div>
        <div class="proto-shadow-metric"><span>REAL MONEY</span><b data-shadow-money>FALSE</b></div>
      </div>
      <div class="proto-shadow-actions"><button class="primary" data-shadow-start>START SHADOW</button><button data-shadow-stop>STOP SHADOW</button></div>
      <div class="proto-shadow-note" data-shadow-note><strong>SHADOW ONLY</strong> · hypothetical evaluation; no portfolio mutation or broker routing.</div>
    </div>
  `;
  document.body.appendChild(panel);

  const state = panel.querySelector<HTMLElement>("[data-shadow-state]");
  const mode = panel.querySelector<HTMLElement>("[data-shadow-mode]");
  const engine = panel.querySelector<HTMLElement>("[data-shadow-engine]");
  const financial = panel.querySelector<HTMLElement>("[data-shadow-financial]");
  const money = panel.querySelector<HTMLElement>("[data-shadow-money]");
  const start = panel.querySelector<HTMLButtonElement>("[data-shadow-start]");
  const stop = panel.querySelector<HTMLButtonElement>("[data-shadow-stop]");
  const note = panel.querySelector<HTMLElement>("[data-shadow-note]");
  if (!state || !mode || !engine || !financial || !money || !start || !stop || !note) return;

  let busy = false;

  const render = (status: ShadowStatus | null): void => {
    if (!status) {
      state.textContent = "UNAVAILABLE";
      state.classList.remove("on");
      mode.textContent = "—";
      engine.textContent = "UNAVAILABLE";
      note.classList.add("proto-shadow-error");
      note.textContent = "SHADOW status endpoint unavailable.";
      return;
    }
    const running = Boolean(status.running || status.shadow_execution_enabled);
    state.textContent = running ? "RUNNING" : "STOPPED";
    state.classList.toggle("on", running);
    mode.textContent = status.mode ?? "SHADOW";
    engine.textContent = running ? "ACTIVE" : "STOPPED";
    financial.textContent = String(Boolean(status.financial_connectivity)).toUpperCase();
    money.textContent = String(Boolean(status.real_money_execution)).toUpperCase();
    note.classList.remove("proto-shadow-error");
    note.innerHTML = "<strong>SHADOW ONLY</strong> · hypothetical evaluation; no portfolio mutation or broker routing.";
  };

  const refresh = async (): Promise<void> => render(await request<ShadowStatus>("/shadow/status"));

  const command = async (path: string): Promise<void> => {
    if (busy) return;
    busy = true;
    start.disabled = true;
    stop.disabled = true;
    const result = await request<ShadowStatus>(path, { method: "POST" });
    render(result);
    busy = false;
    start.disabled = false;
    stop.disabled = false;
  };

  start.addEventListener("click", () => void command("/shadow/start"));
  stop.addEventListener("click", () => void command("/shadow/stop"));
  void refresh();
  window.setInterval(() => void refresh(), POLL_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
