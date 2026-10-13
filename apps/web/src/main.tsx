import "@fontsource/chakra-petch/latin-500.css";
import "@fontsource/chakra-petch/latin-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-600.css";
import "./styles.css";

import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { type Health, type LiveStatus, type Market, post, type Runtime, type Sym, SYMBOLS } from "./api";
import { usePoll, useMarket } from "./hooks";
import { CommandContext, Dot, type LogEntry, pct, tone, usd } from "./ui";
import { Command } from "./views/Command";
import { Lab } from "./views/Lab";
import { Replay } from "./views/Replay";
import { System } from "./views/System";
import { Trade } from "./views/Trade";

const VIEWS = ["Command", "Trade", "Lab", "Replay", "System"] as const;
type View = (typeof VIEWS)[number];

export type Shared = {
  market: ReturnType<typeof useMarket>;
  markets: Market[];
  marketsStatus: number | null;
  runtime: Runtime | null;
  selected: Sym;
  select: (s: Sym) => void;
};

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = window.setInterval(() => setNow(new Date()), 1000); return () => window.clearInterval(id); }, []);
  return now.toISOString().slice(11, 19);
}

function App() {
  const [view, setView] = useState<View>("Command");
  const [selected, select] = useState<Sym>("BTC");
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const market = useMarket();
  const health = usePoll<Health>("/health", 5000);
  const live = usePoll<LiveStatus>("/live/status");
  const runtime = usePoll<Runtime>("/system/status");
  const lifecycle = usePoll<{ markets: Market[] }>("/market-lifecycle", 5000);
  const clock = useClock();

  const refreshRuntime = runtime.refresh;
  const run = useCallback(async (label: string, path: string, body?: unknown) => {
    setBusy(label);
    const result = await post(path, body);
    const detail = result.ok ? `HTTP ${result.status}` : result.status === 401
      ? "Operator token required — set it in System."
      : `${result.status || "network"} · ${result.error}`;
    setLog(old => [{ at: new Date().toISOString(), label, ok: result.ok, detail }, ...old].slice(0, 40));
    setBusy(null);
    refreshRuntime();
    return result.ok;
  }, [refreshRuntime]);
  const commander = useMemo(() => ({ busy, run, log }), [busy, run, log]);

  const feedLive = Boolean(live.data?.running && live.data.receiving_data && live.data.all_symbols_fresh);
  const apiUp = health.data?.status === "ok" && health.status === 200;
  const killArmed = runtime.data?.kill_switch === "ARMED";
  const shared: Shared = {
    market,
    markets: lifecycle.data?.markets ?? [],
    marketsStatus: lifecycle.status,
    runtime: runtime.data,
    selected,
    select,
  };

  return (
    <CommandContext.Provider value={commander}>
      <div className="backdrop" aria-hidden="true"><div className="floor" /></div>
      <div className="scanlines" aria-hidden="true" />
      <div className="shell" data-feed={feedLive ? "live" : "stale"}>
        <header className="top">
          <div className="brand"><span className="mark" aria-hidden="true" /><b>PROTO</b><small>Quant Engine</small></div>
          <nav aria-label="Views">
            {VIEWS.map(v => <button key={v} type="button" aria-current={view === v ? "page" : undefined} onClick={() => setView(v)}>{v}</button>)}
          </nav>
          <div className="status" aria-live="polite">
            <span><Dot on={feedLive} warn={!feedLive} />Feed {feedLive ? "live" : live.data ? "stale" : "checking"}</span>
            <span><Dot on={market.connected} warn={!market.connected} />{market.connected ? "Stream" : "REST"}</span>
            <span><Dot on={apiUp} />API</span>
            <span className={killArmed ? "" : "alarm"}>Kill {runtime.data?.kill_switch ?? "—"}</span>
            <span className="mode">{runtime.data?.mode ?? health.data?.mode ?? "—"}</span>
            <time>{clock} UTC</time>
          </div>
        </header>

        <div className="ticker" aria-hidden="true">
          <div className="ticker-track">
            {[0, 1].map(copy => (
              <span key={copy}>
                {SYMBOLS.map(s => {
                  const series = market.series[s];
                  const first = series[0], last = market.frames[s]?.mid ?? series[series.length - 1];
                  const change = first && last ? last / first - 1 : null;
                  return <em key={s}><b>{s}/USD</b> {usd(last)} <i className={tone(change)}>{pct(change)}</i></em>;
                })}
                <em><b>MODE</b> {runtime.data?.mode ?? "—"}</em>
                <em><b>KILL</b> {runtime.data?.kill_switch ?? "—"}</em>
                <em><b>FINANCIAL CONNECTIVITY</b> OFF</em>
              </span>
            ))}
          </div>
        </div>

        <main key={view} className="view">
          {view === "Command" && <Command {...shared} />}
          {view === "Trade" && <Trade {...shared} />}
          {view === "Lab" && <Lab {...shared} />}
          {view === "Replay" && <Replay />}
          {view === "System" && <System health={health.data} />}
        </main>

        <footer className="boundary">
          <span>Simulation · paper · replay only</span>
          <b>FINANCIAL CONNECTIVITY OFF</b>
          <b>REAL MONEY EXECUTION OFF</b>
          {busy && <span className="busy">Running: {busy}</span>}
        </footer>
      </div>
    </CommandContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
