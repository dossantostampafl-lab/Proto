import "./model-quality-runtime.css";

type ReliabilityPoint = {
  lower_bound: number | null;
  upper_bound: number | null;
  count: number | null;
  mean_prediction: number | null;
  observed_frequency: number | null;
  absolute_gap: number | null;
};

type Calibration = {
  status: "COMPUTED" | "NOT_COMPUTED" | string;
  source: string;
  model_version: string;
  feature_version?: string | null;
  observation_count: number;
  brier_score: number | null;
  log_loss: number | null;
  expected_calibration_error: number | null;
  maximum_calibration_error: number | null;
  reliability_curve: ReliabilityPoint[];
  observed_at?: string | null;
  computed_at?: string | null;
  note?: string | null;
};

type ViewState =
  | { kind: "loading" }
  | { kind: "error"; status: number }
  | { kind: "empty"; data: Calibration }
  | { kind: "computed"; data: Calibration; receivedAt: number };

const API_BASE = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const POLL_MS = 30_000;
const REQUEST_TIMEOUT_MS = 4_000;
const STALE_MS = POLL_MS * 3;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function decimal(value: number | null | undefined, digits = 4) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function percent(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function ensureSurface() {
  let surface = document.querySelector<HTMLElement>(".modelQualitySurface");
  if (surface) return surface;
  const footer = document.querySelector<HTMLElement>("footer[data-section='SYSTEM']");
  if (!footer) return null;
  surface = el("section", "modelQualitySurface");
  surface.dataset.section = "RESEARCH";
  surface.setAttribute("aria-label", "Persisted model calibration quality");
  footer.before(surface);
  return surface;
}

function metric(label: string, value: string) {
  const box = el("div", "mqMetric");
  box.append(el("span", "", label), el("b", "", value));
  return box;
}

function renderReliability(points: ReliabilityPoint[]) {
  const wrap = el("div", "mqReliability");
  if (!points.length) {
    wrap.append(el("p", "mqEmpty", "No populated reliability bins are persisted for this calibration snapshot."));
    return wrap;
  }
  const chart = el("div", "mqReliabilityChart");
  points.slice(0, 12).forEach((point) => {
    const row = el("div", "mqReliabilityRow");
    const predicted = point.mean_prediction;
    const observed = point.observed_frequency;
    const label = el("span", "mqBin", `${decimal(point.lower_bound, 2)}–${decimal(point.upper_bound, 2)}`);
    const bars = el("div", "mqBars");
    const predictedBar = el("i", "mqPredicted");
    predictedBar.style.width = `${Math.max(0, Math.min(1, predicted ?? 0)) * 100}%`;
    const observedBar = el("i", "mqObserved");
    observedBar.style.width = `${Math.max(0, Math.min(1, observed ?? 0)) * 100}%`;
    bars.append(predictedBar, observedBar);
    const values = el("span", "mqBinValues", `p ${percent(predicted, 1)} · y ${percent(observed, 1)} · n ${point.count ?? 0}`);
    row.append(label, bars, values);
    chart.append(row);
  });
  wrap.append(chart);
  return wrap;
}

function render(surface: HTMLElement, state: ViewState) {
  surface.replaceChildren();
  const panel = el("article", "mqPanel");
  const header = el("header", "mqHeader");
  header.append(el("strong", "", "MODEL CALIBRATION"), el("span", "mqBadge", "PERSISTED RESEARCH"));
  panel.append(header);

  if (state.kind === "loading") {
    panel.append(el("p", "mqEmpty", "Loading persisted calibration evidence…"));
    surface.append(panel);
    return;
  }

  if (state.kind === "error") {
    const message = state.status === 0
      ? "Calibration endpoint is unreachable. No previous values are being presented as current."
      : `Calibration endpoint returned HTTP ${state.status}. No metrics are being fabricated.`;
    panel.append(el("p", "mqError", message));
    surface.append(panel);
    return;
  }

  const data = state.data;
  const meta = el("div", "mqMeta");
  meta.append(
    el("span", "", `model ${data.model_version || "—"}`),
    el("span", "", `feature ${data.feature_version || "—"}`),
    el("span", "", `source ${data.source || "—"}`),
  );
  panel.append(meta);

  if (state.kind === "empty") {
    panel.append(el("p", "mqEmpty", data.note || "No persisted labeled calibration evidence is available."));
    const emptyMetrics = el("div", "mqMetrics");
    emptyMetrics.append(
      metric("SAMPLES", String(data.observation_count ?? 0)),
      metric("BRIER", "—"),
      metric("LOG LOSS", "—"),
      metric("ECE", "—"),
      metric("MCE", "—"),
    );
    panel.append(emptyMetrics);
    surface.append(panel);
    return;
  }

  const stale = Date.now() - state.receivedAt > STALE_MS;
  const status = el("div", `mqStatus ${stale ? "stale" : "current"}`);
  status.append(
    el("b", "", stale ? "MODEL QUALITY STALE" : "MODEL QUALITY CURRENT"),
    el("span", "", data.computed_at ? `computed ${data.computed_at}` : "computed time unavailable"),
    el("span", "", data.observed_at ? `observed ${data.observed_at}` : "observation time unavailable"),
  );
  panel.append(status);

  const metrics = el("div", "mqMetrics");
  metrics.append(
    metric("SAMPLES", String(data.observation_count ?? 0)),
    metric("BRIER", decimal(data.brier_score)),
    metric("LOG LOSS", decimal(data.log_loss)),
    metric("ECE", percent(data.expected_calibration_error)),
    metric("MCE", percent(data.maximum_calibration_error)),
  );
  panel.append(metrics, renderReliability(Array.isArray(data.reliability_curve) ? data.reliability_curve : []));
  surface.append(panel);
}

async function fetchCalibration(signal: AbortSignal): Promise<ViewState> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(`${API_BASE}/models/calibration`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return { kind: "error", status: response.status };
    const data = await response.json() as Calibration;
    if (data.status !== "COMPUTED") return { kind: "empty", data };
    return { kind: "computed", data, receivedAt: Date.now() };
  } catch {
    return { kind: "error", status: 0 };
  } finally {
    window.clearTimeout(timeout);
    signal.removeEventListener("abort", abort);
  }
}

function start() {
  const controller = new AbortController();
  let timer: number | null = null;

  const refresh = async () => {
    const surface = ensureSurface();
    if (!surface || controller.signal.aborted) return;
    const state = await fetchCalibration(controller.signal);
    if (!controller.signal.aborted) render(surface, state);
  };

  const initial = ensureSurface();
  if (initial) render(initial, { kind: "loading" });
  void refresh();
  timer = window.setInterval(() => void refresh(), POLL_MS);

  const stop = () => {
    controller.abort();
    if (timer != null) window.clearInterval(timer);
  };
  window.addEventListener("pagehide", stop, { once: true });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}
