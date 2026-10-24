// The one value this dashboard keeps across browser sessions: which trading-floor view the viewer
// picked ("Realista", "Prédio" or "Controle"). Nothing else (no operator credential) is stored here, and a
// blocked or missing storage simply falls back to the realistic room.
import { useCallback, useState } from "react";

export type FloorViewMode = "realistic" | "building" | "control";
const KEY = "proto.floor.view";

const pick = (v: string | null): FloorViewMode => (v === "building" || v === "control" ? v : "realistic");

function stored(): string | null {
  try { return window.localStorage.getItem(KEY); } catch { return null; }
}

const read = (): FloorViewMode => pick(stored());

export function useFloorViewMode(): [FloorViewMode, (mode: FloorViewMode) => void] {
  const [mode, setMode] = useState<FloorViewMode>(read);
  const choose = useCallback((next: FloorViewMode) => {
    setMode(next);
    try { window.localStorage.setItem(KEY, next); } catch { /* storage blocked: the choice lasts for this page only */ }
  }, []);
  return [mode, choose];
}
