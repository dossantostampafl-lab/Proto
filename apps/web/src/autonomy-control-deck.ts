type InstrumentCoverage = {
  market_data_provider?: string | null;
  market_data_source?: string | null;
  read_only_market_data?: boolean;
  currently_fresh?: boolean;
  execution_connected?: boolean;
};

type UniverseInstrument = {
  instrument_id: string;
  symbol: string;
  asset_class: string;
  venue: string;
  currency: string;
  coverage: InstrumentCoverage;
};

type UniverseResponse = {
  instruments: UniverseInstrument[];
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

type EquityMarketEvent = {
  instrument_id?: string;
  kind?: string;
  observed_at?: string;
  received_at?: string;
  source?: string;
  provenance?: string;
  bid?: number | null;
  ask?: number | null;
  last?: number | null;
  volume?: number | null;
  quality_flags?: string[];
};

type EquityObservation = {
  instrument: UniverseInstrument;
  event: EquityMarketEvent;
  read_only_market_data: boolean;
  observation_age_seconds: number;
  freshness_threshold_seconds: number | null;
  currently_fresh: boolean | null;
  execution_connected: boolean;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

type CreationStatus = {
  configured: boolean;
  orchestration_available: boolean;
  allowed_jobs: string[];
  accepted_origin: string;
  transport: string;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

type OrchestrationStatus = {
  durable_runtime?: {
    configured?: boolean;
    persistence_ready?: boolean;
    decision_memory_configured?: boolean;
  };
  readiness?: {
    ready_safe_scope?: boolean;
    durable_safe_scope?: boolean;
    live_ready?: boolean;
  };
};

type DecisionMemoryStatus = {
  configured: boolean;
  records: number;
  resolved: number;
  unresolved: number;
  financial_connectivity: boolean;
  real_money_execution: boolean;
};

type ShadowStatus = {
  mode?: string;
  running?: boolean;
  shadow_execution_enabled?: boolean;
  financial_connectivity?: boolean;
  real_money_execution?: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const POLL_MS = 5000;

async function request<T>(path: string, init?: RequestInit): Promise<T | null> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), init?.method === "POST" ? 8000 : 3500);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      signal: controller.signal,
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  } finally {
    window.clearTimeout(timeout);
  }
}

function installStyle(): void {
  const style = document.createElement("style");
  style.textContent = `
    .proto-control-deck{position:fixed;right:18px;bottom:18px;z-index:1100;width:min(500px,calc(100vw - 36px));max-height:min(78vh,800px);border:1px solid rgba(113,167,255,.24);background:rgba(3,7,13,.96);backdrop-filter:blur(20px);box-shadow:0 28px 90px rgba(0,0,0,.48);border-radius:16px;color:#dbe8ff;font:12px/1.4 Inter,system-ui,sans-serif;overflow:hidden}
    .proto-deck-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 13px;border-bottom:1px solid rgba(126,138,156,.16)}.proto-deck-title{display:flex;align-items:center;gap:9px}.proto-deck-title b{letter-spacing:.08em}.proto-deck-title small{color:#7e8a9c;font-size:9px}.proto-deck-actions{display:flex;gap:6px}.proto-deck-actions button,.proto-deck-tabs button,.proto-deck-command,.proto-instrument{border:1px solid rgba(113,167,255,.22);border-radius:8px;background:#0b1320;color:#dbe8ff;cursor:pointer;font:inherit;font-weight:800}.proto-deck-actions button{padding:5px 8px}.proto-deck-body{overflow:auto;max-height:calc(min(78vh,800px) - 46px)}.proto-deck-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:10px;border-bottom:1px solid rgba(126,138,156,.12)}.proto-deck-tabs button{padding:7px 4px;font-size:10px}.proto-deck-tabs button.active{color:#71a7ff;border-color:rgba(113,167,255,.5);background:rgba(113,167,255,.10)}.proto-deck-section{display:none;padding:12px}.proto-deck-section.active{display:block}.proto-deck-status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.proto-deck-metric{padding:9px;border:1px solid rgba(126,138,156,.13);border-radius:10px;background:rgba(11,19,32,.72)}.proto-deck-metric span{display:block;color:#7e8a9c;font-size:9px;letter-spacing:.07em;margin-bottom:4px}.proto-deck-metric b{font-size:11px}.proto-deck-good{color:#5ce39b}.proto-deck-warn{color:#e8c15a}.proto-deck-bad{color:#f05f65}.proto-universe-list{display:grid;gap:7px}.proto-instrument{width:100%;display:grid;grid-template-columns:1.2fr .8fr .8fr auto;gap:8px;align-items:center;padding:9px;text-align:left;background:rgba(11,19,32,.72)}.proto-instrument:hover,.proto-instrument.selected{border-color:rgba(113,167,255,.55);background:rgba(113,167,255,.09)}.proto-instrument b{font-size:11px;display:block}.proto-instrument span,.proto-instrument small{color:#7e8a9c;font-size:9px}.proto-instrument .fresh{color:#5ce39b}.proto-instrument .catalog{color:#e8c15a}.proto-market-explorer{margin-top:10px;padding:10px;border:1px solid rgba(113,167,255,.18);border-radius:11px;background:rgba(5,12,22,.78)}.proto-explorer-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}.proto-explorer-head b{letter-spacing:.08em}.proto-explorer-head span{font-size:9px;color:#71a7ff}.proto-observation-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.proto-observation-item{padding:8px;background:rgba(11,19,32,.78);border-radius:8px}.proto-observation-item span{display:block;color:#7e8a9c;font-size:9px;margin-bottom:3px}.proto-observation-item b{font-size:10px;word-break:break-word}.proto-deck-command{padding:8px 10px}.proto-deck-command.primary{color:#5ce39b;border-color:rgba(92,227,155,.32);background:rgba(92,227,155,.10)}.proto-deck-command.danger{color:#f05f65;border-color:rgba(240,95,101,.32);background:rgba(240,95,101,.10)}.proto-deck-command:disabled{opacity:.45;cursor:wait}.proto-deck-row{display:flex;gap:8px;margin-top:10px}.proto-deck-row .proto-deck-command{flex:1}.proto-deck-note{margin-top:10px;padding:9px;border-radius:9px;background:rgba(113,167,255,.06);color:#8fa5c7;font-size:10px}.proto-deck-empty{color:#7e8a9c;padding:12px;text-align:center}.proto-control-deck.collapsed .proto-deck-body{display:none}.proto-control-deck.collapsed{width:auto}.proto-control-deck.collapsed .proto-deck-title small{display:none}@media(max-width:820px){.proto-control-deck{right:10px;bottom:10px;width:calc(100vw - 20px);max-height:72vh}.proto-instrument{grid-template-columns:1fr .7fr auto}.proto-instrument .venue{display:none}.proto-observation-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);
}

function boolLabel(value: boolean | undefined): string {
  return value ? "YES" : "NO";
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function numberLabel(value: number | null | undefined, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function freshnessLabel(value: boolean | null, threshold: number | null): string {
  if (value === true) return "FRESH";
  if (value === false) return "STALE";
  return threshold === null ? "NOT EVALUATED" : "UNKNOWN";
}

function mount(): void {
  installStyle();
  const deck = document.createElement("aside");
  deck.className = "proto-control-deck";
  deck.setAttribute("aria-label", "PROTO autonomous control deck");
  deck.innerHTML = `
    <div class="proto-deck-head">
      <div class="proto-deck-title"><b>AUTONOMOUS CONTROL DECK</b><small>FACT-ONLY RUNTIME</small></div>
      <div class="proto-deck-actions"><button data-deck-refresh>REFRESH</button><button data-deck-collapse>—</button></div>
    </div>
    <div class="proto-deck-body">
      <div class="proto-deck-tabs">
        <button class="active" data-tab="universe">UNIVERSE</button>
        <button data-tab="autonomy">AUTONOMY</button>
        <button data-tab="creation">CREATION</button>
        <button data-tab="memory">MEMORY</button>
      </div>
      <section class="proto-deck-section active" data-section="universe"><div class="proto-deck-empty">Loading registered instruments…</div></section>
      <section class="proto-deck-section" data-section="autonomy"><div class="proto-deck-empty">Loading autonomous runtime…</div></section>
      <section class="proto-deck-section" data-section="creation"><div class="proto-deck-empty">Loading Creation bridge…</div></section>
      <section class="proto-deck-section" data-section="memory"><div class="proto-deck-empty">Loading Decision Memory…</div></section>
    </div>
  `;
  document.body.appendChild(deck);

  const sections = new Map<string, HTMLElement>();
  deck.querySelectorAll<HTMLElement>("[data-section]").forEach((node) => sections.set(node.dataset.section ?? "", node));
  const tabs = Array.from(deck.querySelectorAll<HTMLButtonElement>("[data-tab]"));
  const refreshButton = deck.querySelector<HTMLButtonElement>("[data-deck-refresh]");
  const collapseButton = deck.querySelector<HTMLButtonElement>("[data-deck-collapse]");
  if (!refreshButton || !collapseButton) return;

  tabs.forEach((button) => button.addEventListener("click", () => {
    const key = button.dataset.tab ?? "universe";
    tabs.forEach((item) => item.classList.toggle("active", item === button));
    sections.forEach((section, sectionKey) => section.classList.toggle("active", sectionKey === key));
  }));

  collapseButton.addEventListener("click", () => {
    const collapsed = deck.classList.toggle("collapsed");
    collapseButton.textContent = collapsed ? "+" : "—";
  });

  let busy = false;
  let lastUniverse: UniverseResponse | null = null;
  let lastCreation: CreationStatus | null = null;
  let lastOrchestration: OrchestrationStatus | null = null;
  let lastMemory: DecisionMemoryStatus | null = null;
  let lastShadow: ShadowStatus | null = null;
  let selectedInstrumentId: string | null = null;
  let lastEquityObservation: EquityObservation | null = null;
  let equityObservationUnavailable = false;

  const renderMarketExplorer = (): string => {
    if (!selectedInstrumentId) {
      return '<div class="proto-market-explorer"><div class="proto-explorer-head"><b>MARKET EXPLORER</b><span>READ-ONLY OBSERVATION</span></div><div class="proto-deck-empty">Select a registered equity to request a bounded read-only observation.</div></div>';
    }
    const instrument = lastUniverse?.instruments.find((item) => item.instrument_id === selectedInstrumentId);
    if (!instrument) return "";
    if (instrument.asset_class !== "EQUITY") {
      const coverage = instrument.coverage;
      return `<div class="proto-market-explorer"><div class="proto-explorer-head"><b>MARKET EXPLORER</b><span>CORE LIVE COVERAGE</span></div><div class="proto-observation-grid">
        <div class="proto-observation-item"><span>INSTRUMENT</span><b>${escapeHtml(instrument.instrument_id)}</b></div>
        <div class="proto-observation-item"><span>PROVIDER</span><b>${escapeHtml(coverage.market_data_provider ?? "UNAVAILABLE")}</b></div>
        <div class="proto-observation-item"><span>READ-ONLY MARKET DATA</span><b>${boolLabel(coverage.read_only_market_data)}</b></div>
        <div class="proto-observation-item"><span>EXECUTION CONNECTED</span><b>NO</b></div>
      </div></div>`;
    }
    if (equityObservationUnavailable || !lastEquityObservation || lastEquityObservation.instrument.instrument_id !== selectedInstrumentId) {
      return `<div class="proto-market-explorer"><div class="proto-explorer-head"><b>MARKET EXPLORER</b><span>READ-ONLY OBSERVATION</span></div><div class="proto-deck-empty ${equityObservationUnavailable ? "proto-deck-warn" : ""}">${equityObservationUnavailable ? "Read-only provider unavailable or not configured. No market observation was fabricated." : "Requesting read-only provider observation…"}</div></div>`;
    }
    const observation = lastEquityObservation;
    const event = observation.event;
    const priceText = event.bid != null || event.ask != null
      ? `${numberLabel(event.bid)} / ${numberLabel(event.ask)}`
      : numberLabel(event.last);
    const freshText = freshnessLabel(observation.currently_fresh, observation.freshness_threshold_seconds);
    const freshClass = observation.currently_fresh === true ? "proto-deck-good" : observation.currently_fresh === false ? "proto-deck-bad" : "proto-deck-warn";
    const thresholdText = observation.freshness_threshold_seconds === null ? "NOT CONFIGURED" : `${numberLabel(observation.freshness_threshold_seconds, 1)}s`;
    return `<div class="proto-market-explorer"><div class="proto-explorer-head"><b>MARKET EXPLORER</b><span>READ-ONLY OBSERVATION</span></div><div class="proto-observation-grid">
      <div class="proto-observation-item"><span>INSTRUMENT</span><b>${escapeHtml(observation.instrument.instrument_id)}</b></div>
      <div class="proto-observation-item"><span>SOURCE / PROVENANCE</span><b>${escapeHtml(event.source ?? "—")} · ${escapeHtml(event.provenance ?? "—")}</b></div>
      <div class="proto-observation-item"><span>${event.bid != null || event.ask != null ? "BID / ASK" : "LAST"}</span><b>${priceText}</b></div>
      <div class="proto-observation-item"><span>OBSERVATION AGE</span><b>${numberLabel(observation.observation_age_seconds, 2)}s</b></div>
      <div class="proto-observation-item"><span>FRESHNESS</span><b class="${freshClass}">${freshText}</b></div>
      <div class="proto-observation-item"><span>FRESHNESS THRESHOLD</span><b>${thresholdText}</b></div>
      <div class="proto-observation-item"><span>EXECUTION CONNECTED</span><b>${boolLabel(observation.execution_connected)}</b></div>
      <div class="proto-observation-item"><span>REAL MONEY</span><b>${boolLabel(observation.real_money_execution)}</b></div>
    </div><div class="proto-deck-note">Provider observation is read-only. Financial connectivity remains ${observation.financial_connectivity ? "ON" : "OFF"}; catalog membership never authorizes execution.</div></div>`;
  };

  const loadEquityObservation = async (instrumentId: string): Promise<void> => {
    selectedInstrumentId = instrumentId;
    lastEquityObservation = null;
    equityObservationUnavailable = false;
    renderUniverse();
    const result = await request<EquityObservation>(`/equity-market/${encodeURIComponent(instrumentId)}`);
    if (selectedInstrumentId !== instrumentId) return;
    lastEquityObservation = result;
    equityObservationUnavailable = result === null;
    renderUniverse();
  };

  const renderUniverse = (): void => {
    const target = sections.get("universe");
    if (!target) return;
    if (!lastUniverse) {
      target.innerHTML = '<div class="proto-deck-empty proto-deck-bad">Universe endpoint unavailable.</div>';
      return;
    }
    const rows = lastUniverse.instruments.map((instrument) => {
      const live = Boolean(instrument.coverage.read_only_market_data && instrument.coverage.currently_fresh);
      const stateClass = live ? "fresh" : "catalog";
      const state = live ? "LIVE READ-ONLY" : "CATALOG ONLY";
      const selected = selectedInstrumentId === instrument.instrument_id ? " selected" : "";
      return `<button type="button" class="proto-instrument${selected}" data-instrument-id="${escapeHtml(instrument.instrument_id)}"><div><b>${escapeHtml(instrument.instrument_id)}</b><small>${escapeHtml(instrument.asset_class)}</small></div><span class="venue">${escapeHtml(instrument.venue)} · ${escapeHtml(instrument.currency)}</span><span>${escapeHtml(instrument.coverage.market_data_provider ?? "NO PROVIDER")}</span><strong class="${stateClass}">${state}</strong></button>`;
    }).join("");
    target.innerHTML = `<div class="proto-universe-list">${rows || '<div class="proto-deck-empty">No instruments registered.</div>'}</div>${renderMarketExplorer()}<div class="proto-deck-note">Catalog membership never implies executable connectivity. Equity provider data is requested on demand and remains read-only.</div>`;
    target.querySelectorAll<HTMLButtonElement>("[data-instrument-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const instrumentId = button.dataset.instrumentId;
        if (!instrumentId) return;
        const instrument = lastUniverse?.instruments.find((item) => item.instrument_id === instrumentId);
        selectedInstrumentId = instrumentId;
        equityObservationUnavailable = false;
        if (instrument?.asset_class === "EQUITY") {
          void loadEquityObservation(instrumentId);
        } else {
          lastEquityObservation = null;
          renderUniverse();
        }
      });
    });
  };

  const renderAutonomy = (): void => {
    const target = sections.get("autonomy");
    if (!target) return;
    const ready = Boolean(lastOrchestration?.readiness?.ready_safe_scope);
    const durable = Boolean(lastOrchestration?.readiness?.durable_safe_scope);
    const shadowRunning = Boolean(lastShadow?.running || lastShadow?.shadow_execution_enabled);
    target.innerHTML = `<div class="proto-deck-status-grid">
      <div class="proto-deck-metric"><span>SAFE SCOPE</span><b class="${ready ? "proto-deck-good" : "proto-deck-warn"}">${ready ? "READY" : "NOT READY"}</b></div>
      <div class="proto-deck-metric"><span>DURABLE RUNTIME</span><b class="${durable ? "proto-deck-good" : "proto-deck-warn"}">${durable ? "READY" : "NOT READY"}</b></div>
      <div class="proto-deck-metric"><span>SHADOW ENGINE</span><b class="${shadowRunning ? "proto-deck-good" : "proto-deck-warn"}">${shadowRunning ? "RUNNING" : "STOPPED"}</b></div>
      <div class="proto-deck-metric"><span>REAL MONEY READY</span><b class="proto-deck-good">NO</b></div>
    </div><div class="proto-deck-row"><button class="proto-deck-command primary" data-shadow-start>START SHADOW</button><button class="proto-deck-command" data-shadow-stop>STOP SHADOW</button></div><div class="proto-deck-note">SHADOW evaluates candidate decisions without portfolio mutation, fills, broker routing or custody.</div>`;
    target.querySelector<HTMLButtonElement>("[data-shadow-start]")?.addEventListener("click", () => void commandShadow("/shadow/start"));
    target.querySelector<HTMLButtonElement>("[data-shadow-stop]")?.addEventListener("click", () => void commandShadow("/shadow/stop"));
  };

  const renderCreation = (): void => {
    const target = sections.get("creation");
    if (!target) return;
    if (!lastCreation) {
      target.innerHTML = '<div class="proto-deck-empty proto-deck-bad">Creation bridge status unavailable.</div>';
      return;
    }
    target.innerHTML = `<div class="proto-deck-status-grid">
      <div class="proto-deck-metric"><span>TRANSPORT</span><b>${escapeHtml(lastCreation.transport)}</b></div>
      <div class="proto-deck-metric"><span>IDENTITY SECRET</span><b class="${lastCreation.configured ? "proto-deck-good" : "proto-deck-warn"}">${lastCreation.configured ? "CONFIGURED" : "NOT CONFIGURED"}</b></div>
      <div class="proto-deck-metric"><span>PROTOBRAIN</span><b class="${lastCreation.orchestration_available ? "proto-deck-good" : "proto-deck-warn"}">${lastCreation.orchestration_available ? "AVAILABLE" : "UNAVAILABLE"}</b></div>
      <div class="proto-deck-metric"><span>ORIGIN</span><b>${escapeHtml(lastCreation.accepted_origin)}</b></div>
    </div><div class="proto-deck-note">Allowlisted jobs: ${lastCreation.allowed_jobs.map(escapeHtml).join(", ") || "none"}. External The Creation transport is only active when deployment identity is explicitly configured.</div>`;
  };

  const renderMemory = (): void => {
    const target = sections.get("memory");
    if (!target) return;
    if (!lastMemory) {
      target.innerHTML = '<div class="proto-deck-empty proto-deck-bad">Decision Memory status unavailable.</div>';
      return;
    }
    target.innerHTML = `<div class="proto-deck-status-grid">
      <div class="proto-deck-metric"><span>CONFIGURED</span><b class="${lastMemory.configured ? "proto-deck-good" : "proto-deck-warn"}">${boolLabel(lastMemory.configured)}</b></div>
      <div class="proto-deck-metric"><span>RECORDS</span><b>${lastMemory.records ?? 0}</b></div>
      <div class="proto-deck-metric"><span>RESOLVED</span><b>${lastMemory.resolved ?? 0}</b></div>
      <div class="proto-deck-metric"><span>UNRESOLVED</span><b>${lastMemory.unresolved ?? 0}</b></div>
    </div><div class="proto-deck-note">Decision Memory is read-only in this control deck. No record is synthesized when persistence is unavailable.</div>`;
  };

  const renderAll = (): void => {
    renderUniverse();
    renderAutonomy();
    renderCreation();
    renderMemory();
  };

  const refresh = async (): Promise<void> => {
    const [universe, creation, orchestration, memory, shadow] = await Promise.all([
      request<UniverseResponse>("/universe"),
      request<CreationStatus>("/creation/status"),
      request<OrchestrationStatus>("/orchestration/status"),
      request<DecisionMemoryStatus>("/orchestration/decision-memory/status"),
      request<ShadowStatus>("/shadow/status"),
    ]);
    lastUniverse = universe;
    lastCreation = creation;
    lastOrchestration = orchestration;
    lastMemory = memory;
    lastShadow = shadow;
    renderAll();
  };

  const commandShadow = async (path: string): Promise<void> => {
    if (busy) return;
    busy = true;
    const result = await request<ShadowStatus>(path, { method: "POST" });
    if (result) lastShadow = result;
    renderAutonomy();
    busy = false;
  };

  refreshButton.addEventListener("click", () => void refresh());
  void refresh();
  window.setInterval(() => void refresh(), POLL_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
