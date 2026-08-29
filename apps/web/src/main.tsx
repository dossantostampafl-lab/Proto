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

type StreamEnvelope<T> = {
  type: string;
  data?: T;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");

function formatNumber(value: number, digits = 2) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [journal, setJournal] = useState<FillJournal | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: number | undefined;
    let reconnectTimer: number | undefined;
    const sockets = new Set<WebSocket>();

    async function refresh() {
      try {
        const [healthResponse, portfolioResponse, fillsResponse] = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/v1/portfolio`),
          fetch(`${API_BASE}/v1/fills?limit=8`),
        ]);

        if (!healthResponse.ok || !portfolioResponse.ok || !fillsResponse.ok) {
          throw new Error("API returned a non-success response");
        }

        const [healthBody, portfolioBody, fillsBody] = await Promise.all([
          healthResponse.json() as Promise<Health>,
          portfolioResponse.json() as Promise<Portfolio>,
          fillsResponse.json() as Promise<FillJournal>,
        ]);

        if (!cancelled) {
          setHealth(healthBody);
          setPortfolio(portfolioBody);
          setJournal(fillsBody);
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
          // Ignore malformed transport frames; periodic REST reconciliation remains authoritative.
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
      openSocket("analytics", (message) => {
        if (message.type === "runtime") void refresh();
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
      value: streamOnline ? "LIVE" : "RECONCILING",
      note: streamOnline ? "WebSocket updates" : "REST fallback active",
    },
    {
      label: "Fill journal",
      value: String(journal?.count ?? 0),
      note: "Recent simulated fills",
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