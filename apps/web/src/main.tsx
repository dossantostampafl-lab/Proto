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
  source?: string;
  sequence?: number;
};

type LiveState = "IDLE" | "CONNECTING" | "STREAMING" | "RECONNECTING" | "ERROR" | "STOPPED";

type LiveStatus = {
  mode: "LIVE_DATA_READ_ONLY";
  state: LiveState;
  source: string | null;
  symbol: string | null;
  last_tick_at: string | null;
  last_sequence: number | null;
  received: number;
  rejected: number;
  reconnect_attempts: number;
  last_error: string | null;
  stale: boolean;
  latency_ms: number | null;
  staleness_ms: number | null;
  read_only: boolean;
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

type StreamEnvelope<T> = {
  type: string;
  data?: T;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");
const REPLAY_SPEEDS: ReplaySpeed[] = ["1x", "5x", "10x", "50x", "100x", "MAX"];
const LIVE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;

const EMPTY_LIVE_STATUS: LiveStatus = {
  mode: "LIVE_DATA_READ_ONLY",
  state: "IDLE",
  source: null,
  symbol: null,
  last_tick_at: null,
  last_sequence: null,
  received: 0,
  rejected: 0,
  reconnect_attempts: 0,
  last_error: null,
  stale: false,
  latency_ms: null,
  staleness_ms: null,
  read_only: true,
};

function formatNumber(value: number, digits = 2) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function formatDuration(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 1_000) return `${Math.round(value)} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

function formatTickTime(value: string | null) {
  if (!value) return "Waiting for first tick";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString();
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [journal, setJournal] = useState<FillJournal | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [marketData, setMarketData] = useState<MarketDataFrame | null>(null);
  const [orderBook, setOrderBook] = useState<OrderBookFrame | null>(null);
  const [live, setLive] = useState<LiveStatus>(EMPTY_LIVE_STATUS);
  const [liveSource] = useState("binance");
  const [liveSymbol, setLiveSymbol] = useState<(typeof LIVE_SYMBOLS)[number]>("BTCUSDT");
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [seekCursor, setSeekCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function refreshLiveStatus() {
    try {
      const response = await fetch(`${API_BASE}/live/status`);
      if (!response.ok) throw new Error(`Live status returned ${response.status}`);
      const body = (await response.json()) as LiveStatus;
      setLive(body);
      if (body.symbol && LIVE_SYMBOLS.includes(body.symbol as (typeof LIVE_SYMBOLS)[number])) {
        setLiveSymbol(body.symbol as (typeof LIVE_SYMBOLS)[number]);
      }
      setLiveError(body.last_error);
    } catch (statusError) {
      setLiveError(statusError instanceof Error ? statusError.message : "Live status unavailable");
    }
  }

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: number | undefined;
    let reconnectTimer: number | undefined;
    const sockets = new Set<WebSocket>();

    async function refresh() {
      void refreshLiveStatus();
      try {
        const [healthResponse, portfolioResponse, fillsResponse, replayResponse] = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/v1/portfolio`),
          fetch(`${API_BASE}/v1/fills?limit=8`),
          fetch(`${API_BASE}/replay/status`),
        ]);

        if (
          !healthResponse.ok ||
          !portfolioResponse.ok ||
          !fillsResponse.ok ||
          !replayResponse.ok
        ) {
          throw new Error("API returned a non-success response");
        }

        const [healthBody, portfolioBody, fillsBody, replayBody] = await Promise.all([
          healthResponse.json() as Promise<Health>,
          portfolioResponse.json() as Promise<Portfolio>,
          fillsResponse.json() as Promise<FillJournal>,
          replayResponse.json() as Promise<ReplayStatus>,
        ]);

        if (!cancelled) {
          setHealth(healthBody);
          setPortfolio(portfolioBody);
          setJournal(fillsBody);
          setReplay(replayBody);
          setSeekCursor(replayBody.cursor);
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
        if ((message.type === "market-data" || message.type === "market_data") && message.data) {
          const frame = message.data as MarketDataFrame;
          setMarketData(frame);
          setLive((current) => {
            if (current.state !== "STREAMING" && current.state !== "RECONNECTING") return current;
            const eventTime = new Date(frame.timestamp).valueOf();
            return {
              ...current,
              state: "STREAMING",
              symbol: frame.symbol || current.symbol,
              source: frame.source || current.source,
              last_tick_at: frame.timestamp,
              last_sequence: frame.sequence ?? current.last_sequence,
              received: current.received + 1,
              stale: false,
              latency_ms: Number.isNaN(eventTime) ? current.latency_ms : Math.max(0, Date.now() - eventTime),
              staleness_ms: 0,
            };
          });
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
    refreshTimer = window.setInterval(() => void refresh(), 10_000);
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

  async function liveControl(action: "start" | "stop") {
    setLiveBusy(true);
    setLiveError(null);
    try {
      const response = await fetch(`${API_BASE}/live/${action}`, {
        method: "POST",
        headers: action === "start" ? { "Content-Type": "application/json" } : undefined,
        body: action === "start" ? JSON.stringify({ source: liveSource, symbol: liveSymbol }) : undefined,
      });
      const body = (await response.json()) as LiveStatus & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? `Live ${action} failed`);
      setLive(body);
      setLiveError(body.last_error);
    } catch (controlError) {
      setLiveError(controlError instanceof Error ? controlError.message : "Live control failed");
    } finally {
      setLiveBusy(false);
    }
  }

  const metrics = [
    {
      label: "Mode",
      value: live.state === "STREAMING" ? live.mode : (health?.mode ?? "SIMULATION"),
      note: "Market data only · orders disabled",
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

      <section className={`panel livePanel ${live.stale ? "livePanelStale" : ""}`}>
        <div className="liveIdentity">
          <div className="liveHeading">
            <p className="eyebrow">PUBLIC MARKET FEED</p>
            <span className="readOnlyBadge">READ ONLY</span>
          </div>
          <h2>Live data connection</h2>
          <p className="panelNote">
            Public prices only. This connection cannot place, modify, or cancel orders.
          </p>
        </div>

        <div className="liveTelemetry" aria-label="Live data telemetry">
          <span>State <b className={`liveState liveState${live.state}`}>{live.state}</b></span>
          <span>Feed <b>{live.source ?? liveSource} / {live.symbol ?? liveSymbol}</b></span>
          <span>Last tick <b>{formatTickTime(live.last_tick_at)}</b></span>
          <span>Latency <b>{formatDuration(live.latency_ms)}</b></span>
          <span>Staleness <b className={live.stale ? "dangerText" : ""}>{formatDuration(live.staleness_ms)}</b></span>
          <span>Frames <b>{live.received.toLocaleString()} ok / {live.rejected.toLocaleString()} rejected</b></span>
          <span>Reconnects <b>{live.reconnect_attempts}</b></span>
        </div>

        <div className="liveControls" aria-label="Live data controls">
          <label className="replayField">
            <span>Source</span>
            <select aria-label="Live data source" disabled value={liveSource}>
              <option value="binance">Binance public</option>
            </select>
          </label>
          <label className="replayField">
            <span>Symbol</span>
            <select
              aria-label="Live data symbol"
              disabled={liveBusy || live.state === "STREAMING" || live.state === "CONNECTING"}
              value={liveSymbol}
              onChange={(event) => setLiveSymbol(event.target.value as (typeof LIVE_SYMBOLS)[number])}
            >
              {LIVE_SYMBOLS.map((symbol) => <option key={symbol}>{symbol}</option>)}
            </select>
          </label>
          <button
            className="liveStart"
            disabled={liveBusy || live.state === "STREAMING" || live.state === "CONNECTING"}
            onClick={() => void liveControl("start")}
          >
            {live.state === "RECONNECTING" ? "Reconnect" : "Connect"}
          </button>
          <button
            disabled={liveBusy || ["IDLE", "STOPPED"].includes(live.state)}
            onClick={() => void liveControl("stop")}
          >
            Disconnect
          </button>
        </div>
      </section>

      {(liveError || live.last_error || live.state === "RECONNECTING") && (
        <div className="liveMessage" role="status">
          <strong>{live.state === "RECONNECTING" ? "Reconnecting to public feed" : "Live feed notice"}</strong>
          <span>{liveError ?? live.last_error ?? "Connection interrupted; automatic retry in progress."}</span>
        </div>
      )}

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
            <span>Bid <b>{marketData ? formatNumber(marketData.bid) : "â€”"}</b></span>
            <span>Ask <b>{marketData ? formatNumber(marketData.ask) : "â€”"}</b></span>
            <span>Mid <b>{marketData ? formatNumber(marketData.mid) : "â€”"}</b></span>
            <span>Spread <b>{marketData ? formatNumber(marketData.spread, 4) : "â€”"}</b></span>
            <span>Market P <b>{marketData ? formatNumber(marketData.market_probability * 100, 2) + "%" : "â€”"}</b></span>
            <span>Vol <b>{marketData ? formatNumber(marketData.volatility, 4) : "â€”"}</b></span>
          </div>
        </article>

        <article className="dataPanel">
          <div className="panelTitle">
            <p className="eyebrow">ORDER BOOK L1</p>
            <span>{orderBook?.symbol ?? "WAITING"}</span>
          </div>
          <div className="quoteGrid">
            <span>Bid size <b>{orderBook ? formatNumber(orderBook.bid_size, 4) : "â€”"}</b></span>
            <span>Ask size <b>{orderBook ? formatNumber(orderBook.ask_size, 4) : "â€”"}</b></span>
            <span>Best bid <b>{orderBook ? formatNumber(orderBook.best_bid) : "â€”"}</b></span>
            <span>Best ask <b>{orderBook ? formatNumber(orderBook.best_ask) : "â€”"}</b></span>
            <span>Imbalance <b>{orderBook ? formatNumber(orderBook.imbalance, 4) : "â€”"}</b></span>
            <span>Spread <b>{orderBook ? formatNumber(orderBook.spread, 4) : "â€”"}</b></span>
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

