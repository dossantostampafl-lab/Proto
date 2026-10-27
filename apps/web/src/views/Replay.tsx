import { useState } from "react";
import type { Replay as ReplayStatus } from "../api";
import { usePoll } from "../hooks";
import { Button, clamp, Meter, Panel, Stat } from "../ui";

const SPEEDS = ["1x", "5x", "10x", "50x", "100x", "MAX"];

export function Replay() {
  const poll = usePoll<ReplayStatus>("/replay/status", 1500);
  // A failed read shows "—" and says so; the last good status is not presented as current.
  const status = poll.status === 200 ? poll.data : null;
  const [file, setFile] = useState<{ name: string; frames: unknown[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState("1x");
  const [seek, setSeek] = useState("0");

  const load = async (picked: File | undefined) => {
    if (!picked) return;
    try {
      const parsed: unknown = JSON.parse(await picked.text());
      const frames = Array.isArray(parsed) ? parsed
        : parsed && typeof parsed === "object" && Array.isArray((parsed as { frames?: unknown }).frames) ? (parsed as { frames: unknown[] }).frames
        : null;
      if (!frames?.length) throw new Error("Expected an array of frames or an object with frames[].");
      setFile({ name: picked.name, frames });
      setError(null);
    } catch (e) {
      setFile(null);
      setError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  const active = Boolean(status?.active);
  const paused = active && Boolean(status?.paused);
  const state = !status ? (poll.status === null ? "READING…" : `UNAVAILABLE · ${poll.status || "network"}`) : status.finished ? "FINISHED" : !active ? "IDLE" : paused ? "PAUSED" : "RUNNING";
  const progress = status?.total_frames ? status.cursor / status.total_frames : 0;

  return (
    <div className="replay">
      <Panel title="Historical replay" source="paper" className="wide">
        <div className="grid4">
          <Stat label="State" value={state} className={active && !paused ? "up" : ""} />
          <Stat label="Cursor" value={status ? `${status.cursor} / ${status.total_frames}` : "—"} />
          <Stat label="Speed" value={status?.speed ?? "—"} />
          <Stat label="Progress" value={status ? `${Math.round(clamp(progress) * 100)}%` : "—"} />
        </div>
        <Meter value={progress} label="Replay progress" />

        <div className="row wrap">
          <label className="btn file">Load replay JSON<input type="file" accept="application/json,.json" onChange={e => void load(e.target.files?.[0])} /></label>
          <span className="hint">{error ?? (file ? `${file.name} · ${file.frames.length} frames` : "No file loaded. Replay never invents a dataset.")}</span>
          <label className="field inline"><span>Speed</span>
            <select value={speed} onChange={e => setSpeed(e.target.value)}>{SPEEDS.map(s => <option key={s}>{s}</option>)}</select>
          </label>
        </div>

        <div className="row wrap">
          <Button label="START REPLAY" path="/replay/start" body={file ? { frames: file.frames, speed } : undefined} kind="primary" disabled={!file}>Start</Button>
          <Button label="PAUSE REPLAY" path="/replay/pause" disabled={!active}>Pause</Button>
          <Button label="RESUME REPLAY" path="/replay/resume" disabled={!paused}>Resume</Button>
          <Button label="STEP REPLAY" path="/replay/step" disabled={!active}>Step</Button>
          <Button label="RESTART REPLAY" path="/replay/restart" disabled={!active}>Restart</Button>
          <Button label="RESET REPLAY" path="/replay/reset">Reset</Button>
        </div>

        <div className="row wrap">
          <label className="field inline"><span>Seek cursor</span><input value={seek} inputMode="numeric" onChange={e => setSeek(e.target.value)} /></label>
          <Button label="SEEK REPLAY" path="/replay/seek" body={{ cursor: Number(seek.trim()) }} disabled={!active || !/^\d+$/.test(seek.trim())}>Seek</Button>
          <Button label="SET REPLAY SPEED" path="/replay/speed" body={{ speed }} disabled={!active}>Apply speed</Button>
        </div>
      </Panel>
    </div>
  );
}
