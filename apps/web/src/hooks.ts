import { useCallback, useEffect, useRef, useState } from "react";
import { type Frame, get, type Sym, SYMBOLS, WS_BASE } from "./api";

export type Poll<T> = { data: T | null; status: number | null; refresh: () => void };

/** Poll a GET endpoint. `data` keeps the last good value; `status` is the last HTTP status (0 = network). */
export function usePoll<T>(path: string | null, ms = 3000): Poll<T> {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) { setData(null); setStatus(null); return; }
    let live = true;
    const load = async () => {
      const result = await get<T>(path);
      if (!live) return;
      setStatus(result.status);
      if (result.ok) setData(result.data);
    };
    void load();
    const id = window.setInterval(() => void load(), ms);
    return () => { live = false; window.clearInterval(id); };
  }, [path, ms, tick]);
  const refresh = useCallback(() => setTick(t => t + 1), []);
  return { data, status, refresh };
}

const MAX_POINTS = 120;
const STREAM_FRESH_MS = 15000;
type Book<T> = Record<Sym, T>;
const book = <T,>(make: () => T): Book<T> => ({ BTC: make(), ETH: make(), SOL: make() });

/** Live public market data: history bootstrap + WebSocket stream with ordered, de-duplicated frames. */
export function useMarket() {
  const [frames, setFrames] = useState<Book<Frame | null>>(() => book(() => null));
  const [series, setSeries] = useState<Book<number[]>>(() => book(() => []));
  const [open, setOpen] = useState(false);
  const [streamAt, setStreamAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const cursor = useRef(book(() => ({ generation: -1, sequence: -1 })));

  const ingest = useCallback((frame: Frame, streamed = false) => {
    if (!SYMBOLS.includes(frame.symbol) || !(frame.mid > 0)) return;
    if (streamed) setStreamAt(Date.now());
    const last = cursor.current[frame.symbol];
    const stale = frame.connection_generation < last.generation
      || (frame.connection_generation === last.generation && frame.sequence <= last.sequence);
    if (stale) return;
    cursor.current[frame.symbol] = { generation: frame.connection_generation, sequence: frame.sequence };
    setFrames(old => ({ ...old, [frame.symbol]: frame }));
    setSeries(old => ({ ...old, [frame.symbol]: [...old[frame.symbol], frame.mid].slice(-MAX_POINTS) }));
  }, []);

  useEffect(() => {
    let live = true;
    void Promise.all(SYMBOLS.map(s => get<{ history: Frame[] }>(`/live/history/${s}?limit=1000`))).then(results => {
      if (!live) return;
      setSeries(old => {
        const next = { ...old };
        results.forEach((r, i) => {
          if (!r.ok) return;
          const mids = r.data.history.filter(f => f.mid > 0).map(f => f.mid).slice(-MAX_POINTS);
          if (mids.length) next[SYMBOLS[i]] = [...mids, ...old[SYMBOLS[i]]].slice(-MAX_POINTS);
        });
        return next;
      });
    });

    let socket: WebSocket | null = null;
    let retry: number | null = null;
    let attempt = 0;
    const connect = () => {
      socket = new WebSocket(`${WS_BASE}/ws/market-data`);
      socket.onopen = () => { attempt = 0; setOpen(true); };
      socket.onmessage = event => {
        try {
          const msg = JSON.parse(String(event.data)) as { type: string; data?: Frame };
          if (msg.type === "market-data" && msg.data) ingest(msg.data, true);
        } catch { /* malformed frame ignored */ }
      };
      socket.onclose = () => {
        setOpen(false);
        if (!live) return;
        retry = window.setTimeout(connect, Math.min(15000, 750 * 2 ** attempt));
        attempt = Math.min(attempt + 1, 5);
      };
    };
    connect();

    // REST reconciliation keeps prices current when the socket is down.
    const reconcile = window.setInterval(async () => {
      const r = await get<{ markets: Frame[] }>("/live/market-data");
      if (live && r.ok) r.data.markets.forEach(f => ingest(f));
      if (live) setNow(Date.now());
    }, 3000);

    return () => {
      live = false;
      window.clearInterval(reconcile);
      if (retry !== null) window.clearTimeout(retry);
      socket?.close();
    };
  }, [ingest]);

  // "Connected" means the socket is open AND it delivered a frame recently: an open socket that
  // carries no data (upstream feed down) is not a live stream, so the UI says REST / last known.
  const connected = open && now - streamAt < STREAM_FRESH_MS;
  return { frames, series, connected };
}

/** Append a value to a bounded series whenever it changes. */
export function useSeries(value: number | null | undefined, max = MAX_POINTS): number[] {
  const [values, setValues] = useState<number[]>([]);
  useEffect(() => {
    if (value == null || !Number.isFinite(value)) return;
    setValues(old => [...old, value].slice(-max));
  }, [value, max]);
  return values;
}
