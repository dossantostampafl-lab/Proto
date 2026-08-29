import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Health = {
  status: string;
  mode: string;
  version: string;
  persistence_enabled: boolean;
};

type Position = {
  asset: string;
  quantity: number;
  average_price: number;
  mark_price: number | null;
  market_value: number | null;
  realized_pnl: number;
  unrealized_pnl: number | null;
  fees: number;
};

type Portfolio = {
  mode: string;
  positions: Position[];
  total_realized_pnl: number;
  total_unrealized_pnl: number;
  total_pnl_after_fees: number;
  total_fees: number;
};

type FillEntry = {
  order_id: string;
  market_id: string;
  asset: string;
  side: string;
  filled_quantity: number;
  fill_price: number;
  fee: number;
  slippage_bps: number;
  filled_at: string;
};

type FillJournal = {
  mode: string;
  count: number;
  fills: FillEntry[];
};

type ReplayStatus = {
  mode: string;
  active: boolean;
  paused: boolean;
  speed: string;
  cursor: number;
  total_frames: number;
  finished: boolean;
  last_timestamp: string | null;
};

type ReplaySpeed = "1x" | "5x" | "10x" | "50x" | "100x" | "MAX";

type MarketDataFrame = {
  timestamp: string;
  market_id: string;
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread: number;
  market_probability: number;
  volatility: number;
};

type OrderBookFrame = {
  timestamp: string;
  symbol: string;
  best_bid: number;
  best_ask: number;
  bid_size: number;
  ask_size: number;
  mid_price: number;
  spread: number;
  imbalance: number;
};

type LifecycleRow = {
  market_id: string;
  symbol: string;
  source: string;
  lifecycle_state: string;
  resolution_state: string;
  market_probability: number;
  model_probability: number;
  confidence: number;
  uncertainty: number;
  net_edge: number;
  edge_decision: string;
  liquidity_depth: number;
  imbalance: number;
  expiry_horizon_minutes: number;
  synthetic_expires_at: string;
  real_money_execution: boolean;
};

type LifecycleResponse = {
  source: string;
  count: number;
  markets: LifecycleRow[];
};

type SyntheticGreeks = {
  market_id: string;
  symbol: string;
  source: string;
  market_probability_delta: number;
  volatility_vega: number;
  imbalance_kappa: number;
  time_theta: number;
  model_version: string;
  feature_version: string;
};

type HawkesState = {
  symbol: string;
  source: string;
  event_count: number;
  baseline_intensity: number;
  current_intensity: number;
  excitation: number;
  decay: number;
  branching_ratio: number;
  event_probability: number;
};

type ExpiryPoint = {
  market_id: string;
  symbol: string;
  expiry_horizon_minutes: number;
  model_probability: number;
  net_edge: number;
  absolute_net_edge: number;
};

type ExpiryMap = {
  source: string;
  axes: {
    radius: string;
    height: string;
    intensity: string;
  };
  points: ExpiryPoint[];
};

type StreamEnvelope<T> = {
  type: string;
  data?: T;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");
const REPLAY_SPEEDS: ReplaySpeed[] = ["1x", "5x", "10x", "50x", "100x", "MAX"];

function formatNumber(value: number, digits = 2) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function formatPercent(value: number, digits = 2) {
  return `${formatNumber(value * 100, digits)}%`;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [journal, setJournal] = useState<FillJournal | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [marketData, setMarketData] = useState<MarketDataFrame | null>(null);
  const [orderBook, setOrderBook] = useState<OrderBookFrame | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleResponse | null>(null);
  const [greeks, setGreeks] = useState<SyntheticGreeks | null>(null);
  const [hawkes, setHawkes] = useState<HawkesState | null>(null);
  const [expiryMap, setExpiryMap] = useState<ExpiryMap | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [seekCursor, setSeekCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: number | undefined;
    let reconnectTimer: number | undefined;
    const sockets = new Set<WebSocket>();

    async function refresh() {
      try {
        const responses = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/v1/portfolio`),
          fetch(`${API_BASE}/v1/fills?limit=8`),
          fetch(`${API_BASE}/replay/status`),
          fetch(`${API_BASE}/market-lifecycle`),
          fetch(`${API_BASE}/analytics/greeks/btc-threshold`),
          fetch(`${API_BASE}/hawkes/BTC`),
          fetch(`${API_BASE}/analytics/expiry-map`),
        ]);

        if (responses.some((response) => !response.ok)) {
          throw new Error("API returned a non-success response");
        }

        const [
          healthBody,
          portfolioBody,
          fillsBody,
          replayBody,
          lifecycleBody,
          greeksBody,
          hawkesBody,
          expiryBody,
        ] = await Promise.all([
          responses[0].json() as Promise<Health>,
          responses[1].json() as Promise<Portfolio>,
          responses[2].json() as Promise<FillJournal>,
          responses[3].json() as Promise<ReplayStatus>,
          responses[4].json() as Promise<LifecycleResponse>,
          responses[5].json() as Promise<SyntheticGreeks>,
          responses[6].json() as Promise<HawkesState>,
          responses[7].json() as Promise<ExpiryMap>,
        ]);

        if (!cancelled) {
          setHealth(healthBody);
          setPortfolio(portfolioBody);
          setJournal(fillsBody);
          setReplay(replayBody);
          setSeekCursor(replayBody.cursor);
          setLifecycle(lifecycleBody);
          setGreeks(greeksBody);
          setHawkes(hawkesBody);
          setExpiryMap(expiryBody);
          setError(null);
        }
      } catch (requestError) {
        if (!cancelled) {
          const message = requestError instanceof Error ? requestError.message : "Unknown API error";
          setError(message);
        }
      }
    }

    function openSocket(channel: string, onMessage: (message: StreamEnvelope<unknown>) => void) {
      const socket = new WebSocket(`${WS_BASE}/ws/${channel}`);
      sockets.add(socket);
      socket.onopen = () => {
        if (!cancelled) setStreamOnline(true);
      };
      socket.onmessage = (event) => {
        try {
          onMessage(JSON.parse(event.data as string) as StreamEnvelope<unknown>);
        } catch {
          // Periodic REST reconciliation remains authoritative after malformed transport frames.
        }
      };
      socket.onerror = () => {
        if (!cancelled) setStreamOnline(false);
      };
      socket.onclose = () => {
        sockets.delete(socket);
        if (!cancelled) setStreamOnline(false);
      };
    }

    function connectStreams() {
      openSocket("portfolio", (message) => {
        if (message.type === "portfolio" && message.data) {
          setPortfolio(message.data as Portfolio);
        }
      });
      openSocket("fills", (message) => {
        if (message.type === "fill") void refresh();
      });
      openSocket("market-data", (message) => {
        if (message.type === "market-data" && message.data) {
          setMarketData(message.data as MarketDataFrame);
        }
      });
      openSocket("orderbook", (message) => {
        if (message.type === "orderbook" && message.data) {
          setOrderBook(message.data as OrderBookFrame);
        }
      });
      openSocket("analytics", (message) => {
        if (message.type === "runtime") void refresh();
        if (message.type === "replay" && message.data) {
          const status = {
            mode: "HISTORICAL_REPLAY",
            ...(message.data as Omit<ReplayStatus, "mode">),
          };
          setReplay(status);
          setSeekCursor(status.cursor);
        }
      });
    }

    void refresh();
    connectStreams();
    refreshTimer = window.setInterval(() => void refresh(), 30_000);
    reconnectTimer = window.setInterval(() => {
      if (!cancelled && sockets.size === 0) connectStreams();
    }, 5_000);

    return () => {
      cancelled = true;
      if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
      if (reconnectTimer !== undefined) window.clearInterval(reconnectTimer);
      for (const socket of sockets) socket.close();
      sockets.clear();
    };
  }, []);

  async function replayControl(
    action: "pause" | "resume" | "step" | "restart" | "reset" | "seek" | "speed",
    payload?: object,
  ) {
    setControlBusy(true);
    try {
      const response = await fetch(`${API_BASE}/replay/${action}`, {
        method: "POST",
        headers: payload ? { "Content-Type": "application/json" } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      });
      const body = (await response.json()) as ReplayStatus & { detail?: string };
      if (!response.ok) {
        throw new Error(body.detail ?? `Replay ${action} failed`);
      }
      setReplay(body);
      setSeekCursor(body.cursor);
      if (action === "step") {
        const stepBody = body as ReplayStatus & { frame?: MarketDataFrame | null };
        if (stepBody.frame) setMarketData(stepBody.frame);
      }
      setError(null);
    } catch (controlError) {
      setError(controlError instanceof Error ? controlError.message : "Replay control failed");
    } finally {
      setControlBusy(false);
    }
  }

  const metrics = [
    {
      label: "Mode",
      value: health?.mode ?? "SIMULATION",
      note: "Real execution disabled",
    },
    {
      label: "API",
      value: health?.status === "ok" ? "ONLINE" : "CONNECTING",
      note: health ? `Version ${health.version}` : API_BASE,
    },
    {
      label: "Stream",
      value: streamOnline ? "STREAMING" : "RECONCILING",
      note: streamOnline ? "WebSocket updates" : "REST fallback active",
    },
    {
      label: "Replay",
      value: replay?.active ? (replay.paused ? "PAUSED" : "RUNNING") : "IDLE",
      note: replay?.active
        ? `${replay.cursor}/${replay.total_frames} @ ${replay.speed}`
        : "No replay session loaded",
    },
  ];

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">PROTO / PREDICTION MARKET QUANT ENGINE</p>
          <h1>Research. Simulate. Measure edge.</h1>
          <p className="subtitle">
            Quantitative workspace for crypto and binary prediction-market research. The terminal
            is intentionally restricted to simulation and paper-trading workflows.
          </p>
        </div>
        <span className={error ? "status statusError" : "status"}>
          {error ? "API OFFLINE" : health ? "SYSTEM ONLINE" : "CONNECTING"}
        </span>
      </header>

      <section className="grid">
        {metrics.map((metric) => (
          <article className="card" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.note}</small>
          </article>
        ))}
      </section>

      <section className="panel replayPanel">
        <div>
          <p className="eyebrow">HISTORICAL REPLAY</p>
          <h2>Deterministic session control</h2>
          <p className="panelNote">
            Controls become active after a replay dataset is loaded through the backend.
          </p>
        </div>
        <div className="replayControls" aria-label="Replay controls">
          <label className="replayField">
            <span>Speed</span>
            <select
              aria-label="Replay speed"
              disabled={!replay?.active || controlBusy}
              value={(replay?.speed ?? "1x") as ReplaySpeed}
              onChange={(event) =>
                void replayControl("speed", { speed: event.target.value as ReplaySpeed })
              }
            >
              {REPLAY_SPEEDS.map((speed) => <option key={speed}>{speed}</option>)}
            </select>
          </label>
          <label className="replayField">
            <span>Cursor</span>
            <input
              aria-label="Replay cursor"
              disabled={!replay?.active || controlBusy}
              max={replay?.total_frames ?? 0}
              min={0}
              type="number"
              value={seekCursor}
              onChange={(event) => setSeekCursor(Number(event.target.value))}
            />
          </label>
          <button
            disabled={
              !replay?.active || controlBusy || seekCursor < 0 || seekCursor > replay.total_frames
            }
            onClick={() => void replayControl("seek", { cursor: seekCursor })}
          >
            Seek
          </button>
          <button
            disabled={!replay?.active || replay.paused || controlBusy}
            onClick={() => void replayControl("pause")}
          >
            Pause
          </button>
          <button
            disabled={!replay?.active || !replay.paused || replay.finished || controlBusy}
            onClick={() => void replayControl("resume")}
          >
            Resume
          </button>
          <button
            disabled={!replay?.active || replay.finished || controlBusy}
            onClick={() => void replayControl("step")}
          >
            Step
          </button>
          <button
            disabled={!replay?.active || controlBusy}
            onClick={() => void replayControl("restart")}
          >
            Restart
          </button>
          <button
            disabled={!replay?.active || controlBusy}
            onClick={() => void replayControl("reset")}
          >
            Reset
          </button>
        </div>
      </section>

      <section className="dataGrid marketGrid">
        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">MODEL FEED / MARKET DATA</p>
            <span>{marketData?.symbol ?? "WAITING"}</span>
          </div>
          <div className="quoteGrid">
            <span>Bid <b>{marketData ? formatNumber(marketData.bid) : "—"}</b></span>
            <span>Ask <b>{marketData ? formatNumber(marketData.ask) : "—"}</b></span>
            <span>Mid <b>{marketData ? formatNumber(marketData.mid) : "—"}</b></span>
            <span>Spread <b>{marketData ? formatNumber(marketData.spread, 4) : "—"}</b></span>
            <span>Market P <b>{marketData ? formatPercent(marketData.market_probability) : "—"}</b></span>
            <span>Vol <b>{marketData ? formatNumber(marketData.volatility, 4) : "—"}</b></span>
          </div>
        </article>

        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">ORDER BOOK L1</p>
            <span>{orderBook?.symbol ?? "WAITING"}</span>
          </div>
          <div className="quoteGrid">
            <span>Bid size <b>{orderBook ? formatNumber(orderBook.bid_size, 4) : "—"}</b></span>
            <span>Ask size <b>{orderBook ? formatNumber(orderBook.ask_size, 4) : "—"}</b></span>
            <span>Best bid <b>{orderBook ? formatNumber(orderBook.best_bid) : "—"}</b></span>
            <span>Best ask <b>{orderBook ? formatNumber(orderBook.best_ask) : "—"}</b></span>
            <span>Imbalance <b>{orderBook ? formatNumber(orderBook.imbalance, 4) : "—"}</b></span>
            <span>Spread <b>{orderBook ? formatNumber(orderBook.spread, 4) : "—"}</b></span>
          </div>
        </article>
      </section>

      <section className="dataGrid">
        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">MARKET LIFECYCLE / RESOLUTION GRID</p>
            <span>{lifecycle?.source ?? "WAITING"}</span>
          </div>
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Market</th>
                  <th>State</th>
                  <th>Resolution</th>
                  <th>Market P</th>
                  <th>Model P</th>
                  <th>Net edge</th>
                  <th>Expiry</th>
                </tr>
              </thead>
              <tbody>
                {(lifecycle?.markets ?? []).map((row) => (
                  <tr key={row.market_id}>
                    <td>{row.symbol}</td>
                    <td>{row.lifecycle_state}</td>
                    <td>{row.resolution_state}</td>
                    <td>{formatPercent(row.market_probability)}</td>
                    <td>{formatPercent(row.model_probability)}</td>
                    <td>{formatPercent(row.net_edge)}</td>
                    <td>{row.expiry_horizon_minutes}m</td>
                  </tr>
                ))}
                {(lifecycle?.markets.length ?? 0) === 0 && (
                  <tr><td colSpan={7} className="empty">Waiting for lifecycle analytics.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </article>

        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">SYNTHETIC GREEKS FIELD / BTC</p>
            <span>{greeks?.source ?? "WAITING"}</span>
          </div>
          <div className="quoteGrid">
            <span>Probability Δ <b>{greeks ? formatNumber(greeks.market_probability_delta, 6) : "—"}</b></span>
            <span>Volatility ν <b>{greeks ? formatNumber(greeks.volatility_vega, 6) : "—"}</b></span>
            <span>Imbalance κ <b>{greeks ? formatNumber(greeks.imbalance_kappa, 6) : "—"}</b></span>
            <span>Time θ <b>{greeks ? formatNumber(greeks.time_theta, 6) : "—"}</b></span>
            <span>Model <b>{greeks?.model_version ?? "—"}</b></span>
            <span>Features <b>{greeks?.feature_version ?? "—"}</b></span>
          </div>
        </article>
      </section>

      <section className="dataGrid">
        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">HAWKES CASCADE / BTC</p>
            <span>{hawkes?.source ?? "WAITING"}</span>
          </div>
          <div className="quoteGrid">
            <span>Baseline λ <b>{hawkes ? formatNumber(hawkes.baseline_intensity, 6) : "—"}</b></span>
            <span>Current λ <b>{hawkes ? formatNumber(hawkes.current_intensity, 6) : "—"}</b></span>
            <span>Excitation <b>{hawkes ? formatNumber(hawkes.excitation, 6) : "—"}</b></span>
            <span>Decay <b>{hawkes ? formatNumber(hawkes.decay, 6) : "—"}</b></span>
            <span>Branching <b>{hawkes ? formatNumber(hawkes.branching_ratio, 6) : "—"}</b></span>
            <span>Event P <b>{hawkes ? formatPercent(hawkes.event_probability) : "—"}</b></span>
          </div>
        </article>

        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">EXPIRY TORUS DATA</p>
            <span>{expiryMap?.source ?? "WAITING"}</span>
          </div>
          <div className="fillList">
            {(expiryMap?.points ?? []).map((point) => (
              <div className="fillRow" key={point.market_id}>
                <div>
                  <strong>{point.symbol} / {point.expiry_horizon_minutes}m</strong>
                  <small>{point.market_id}</small>
                </div>
                <div className="fillNumbers">
                  <span>Model {formatPercent(point.model_probability)}</span>
                  <b>Edge {formatPercent(point.net_edge)}</b>
                </div>
              </div>
            ))}
            {(expiryMap?.points.length ?? 0) === 0 && (
              <p className="empty">Waiting for expiry analytics.</p>
            )}
          </div>
        </article>
      </section>

      <section className="panel">
        <div>
          <p className="eyebrow">SIMULATED PORTFOLIO</p>
          <h2>P&amp;L and exposure</h2>
        </div>
        <div className="summaryMetrics">
          <span>Realized <b>{formatNumber(portfolio?.total_realized_pnl ?? 0)}</b></span>
          <span>Unrealized <b>{formatNumber(portfolio?.total_unrealized_pnl ?? 0)}</b></span>
          <span>Fees <b>{formatNumber(portfolio?.total_fees ?? 0)}</b></span>
          <span>Net <b>{formatNumber(portfolio?.total_pnl_after_fees ?? 0)}</b></span>
        </div>
      </section>

      <section className="dataGrid">
        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">POSITIONS</p>
            <span>{portfolio?.positions.length ?? 0} open/known</span>
          </div>
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Qty</th>
                  <th>Avg</th>
                  <th>Realized</th>
                  <th>Fees</th>
                </tr>
              </thead>
              <tbody>
                {(portfolio?.positions ?? []).map((position) => (
                  <tr key={position.asset}>
                    <td>{position.asset}</td>
                    <td>{formatNumber(position.quantity, 6)}</td>
                    <td>{formatNumber(position.average_price)}</td>
                    <td>{formatNumber(position.realized_pnl)}</td>
                    <td>{formatNumber(position.fees)}</td>
                  </tr>
                ))}
                {(portfolio?.positions.length ?? 0) === 0 && (
                  <tr><td colSpan={5} className="empty">No simulated positions yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </article>

        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">FILL JOURNAL</p>
            <span>Latest {journal?.count ?? 0}</span>
          </div>
          <div className="fillList">
            {(journal?.fills ?? []).map((fill) => (
              <div className="fillRow" key={fill.order_id}>
                <div>
                  <strong>{fill.asset} {fill.side}</strong>
                  <small>{fill.market_id}</small>
                </div>
                <div className="fillNumbers">
                  <span>{formatNumber(fill.filled_quantity, 6)}</span>
                  <b>{formatNumber(fill.fill_price)}</b>
                </div>
              </div>
            ))}
            {(journal?.fills.length ?? 0) === 0 && (
              <p className="empty">No simulated fills recorded.</p>
            )}
          </div>
        </article>
      </section>

      {error && <p className="errorBanner">API connection: {error}</p>}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
