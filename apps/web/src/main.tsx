import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Metric = { label: string; value: string; note: string };

const metrics: Metric[] = [
  { label: "Mode", value: "SIMULATION", note: "Real execution disabled" },
  { label: "Markets", value: "BTC · ETH · SOL", note: "Crypto research universe" },
  { label: "Risk", value: "ENFORCED", note: "Notional + slippage limits" },
  { label: "Engine", value: "PYTHON + RUST", note: "Research + low-latency core" },
];

function App() {
  return (
    <main className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">PROTO / PREDICTION MARKET QUANT ENGINE</p>
          <h1>Research. Simulate. Measure edge.</h1>
          <p className="subtitle">
            Institutional-style quantitative workspace for crypto and binary prediction markets,
            operating in paper-trading mode by default.
          </p>
        </div>
        <span className="status">SYSTEM ONLINE</span>
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
          <p className="eyebrow">MVP FOUNDATION</p>
          <h2>Quant terminal bootstrap active</h2>
        </div>
        <div className="flow">
          <span>Market Data</span><b>→</b><span>Fair Value</span><b>→</b><span>Risk</span><b>→</b><span>Simulator</span><b>→</b><span>P&amp;L</span>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
