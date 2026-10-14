// Frontend safety and wiring contracts. Checks what the dashboard does, not how a line is spelled.
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../src", import.meta.url));

async function sources(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await sources(path));
    else if (/\.(tsx?|css)$/.test(entry.name)) out.push([path, await readFile(path, "utf8")]);
  }
  return out;
}

const files = await sources(SRC);
const code = files.filter(([p]) => !p.endsWith(".css")).map(([, s]) => s).join("\n");
const index = await readFile(new URL("../index.html", import.meta.url), "utf8");

// One React owner of the DOM.
assert.equal((index.match(/<script /g) ?? []).length, 1, "index.html must have a single entry script");
assert.match(index, /src="\/src\/main\.tsx"/, "main.tsx must be the entry point");
for (const [pattern, why] of [
  [/innerHTML/, "no raw HTML injection"],
  [/window\.prompt/, "no prompt() credential capture"],
  [/window\.fetch\s*=/, "no global fetch monkey-patching"],
  [/document\.createElement/, "no imperative DOM outside React"],
  [/localStorage/, "operator token must not outlive the browser session"],
]) assert.doesNotMatch(code, pattern, why);

// Provenance and the financial boundary stay visible.
for (const label of ["LIVE PUBLIC", "SYNTHETIC RESEARCH", "PAPER / SIM", "FINANCIAL CONNECTIVITY OFF", "REAL MONEY EXECUTION OFF", "Display risk score"]) {
  assert.ok(code.includes(label), `missing provenance/boundary label: ${label}`);
}
assert.doesNotMatch(code, /\b(withdraw|deposit|broker order|live order|real[- ]money order)\b/i, "no real-money affordances");
assert.doesNotMatch(code, /\blive (market p|model p|edge|probability)\b/i, "research probability/edge must never be labelled live");
const command = files.find(([p]) => p.endsWith("Command.tsx"))?.[1] ?? "";
for (const source of ["live", "research", "paper"]) {
  assert.ok(command.includes(`source="${source}"`), `Command view must tag ${source} panels`);
}

// Every operator command of the previous terminal is still reachable.
for (const endpoint of [
  "/paper/start", "/paper/stop", "/paper/automation/start", "/paper/automation/stop", "/v1/simulate",
  "/simulation/reset", "/killswitch/trigger", "/killswitch/reset", "/replay/start", "/replay/pause",
  "/replay/resume", "/replay/step", "/replay/restart", "/replay/seek", "/replay/speed", "/replay/reset",
  "/shadow/start", "/shadow/stop",
]) assert.ok(code.includes(`"${endpoint}"`), `operator command missing: ${endpoint}`);

// Every read surface is wired to its canonical endpoint.
for (const endpoint of [
  "/health", "/system/status", "/live/status", "/live/market-data", "/live/history/", "/live/analytics/",
  "/market-lifecycle", "/models/calibration", "/v1/portfolio", "/pnl/attribution", "/hawkes/", "/analytics/greeks/",
  "/paper/status", "/paper/automation/status", "/replay/status", "/universe", "/orchestration/status",
  "/orchestration/decision-memory/status", "/shadow/status", "/creation/status", "/deployment/info", "/ws/market-data",
]) assert.ok(code.includes(endpoint), `read surface missing: ${endpoint}`);

// Transport hygiene.
assert.match(code, /AbortController/, "network reads must be bounded");
assert.match(code, /cache: "no-store"/, "operational reads must bypass browser cache");
assert.match(code, /X-Proto-Operator-Token/, "operator commands must carry the operator token header");
assert.match(code, /sessionStorage/, "operator token is session-scoped");
assert.match(code, /onclose/, "WebSocket loss must be handled with reconnect");

// Motion is optional.
assert.ok(files.some(([p, s]) => p.endsWith(".css") && s.includes("prefers-reduced-motion: reduce")), "reduced motion must be respected");


// Trading-room presentation must derive its animation from actual station events,
// never from a decorative looping animation or an image of fake market data.
const floorScene = files.find(([p]) => p.endsWith("floorScene.tsx"))?.[1] ?? "";
const floorView = files.find(([p]) => p.endsWith("Floor.tsx"))?.[1] ?? "";
assert.match(floorScene, /<AgentOperator\b/, "trading-room agents must be rendered as live React scene elements");
assert.match(floorScene, /data-activity=\{busy \? "working" : "idle"\}/, "visual agent work must track real station events");
assert.match(floorView, /connected && \(activeAt\[s\]/, "agents must stop performing work when the floor stream disconnects");
assert.match(floorScene, /market\.connected \? "LIVE PUBLIC" : "LAST KNOWN \/ REST"/, "screen must disclose a disconnected market stream");
assert.doesNotMatch(floorScene, /shapeRendering="crispEdges"/, "new floor must not use pixel-art rendering");
// The room may be a photograph, but only of a room with blank screens: every operational value is drawn
// live over it, and the photo is the only picture on the floor.
assert.equal((floorScene.match(/<image\b/g) ?? []).length, 1, "the floor may hold a single room photo");
assert.doesNotMatch(floorScene, /<img\b/, "the floor may hold a single room photo");
assert.match(floorScene, /import ROOM from "\.\.\/assets\/floor-room\.webp"/, "the room photo is the blank-screen asset");
assert.match(floorScene, /<image id="fl-room" href=\{ROOM\}/, "the room photo is the blank-screen asset");
assert.match(floorScene, /<WallScreens\b/, "wall screens must be drawn live over the photo");
assert.match(floorScene, /onError=\{\(\) => setPhoto\(false\)\}/, "a missing photo falls back to the vector room");
assert.match(floorView, /const \[stateFresh, setStateFresh\]/, "floor state polling must expose freshness");
assert.match(floorView, /source=\{state == null \? undefined : state\.data_mode === "TESTNET"/, "floor sidebars must disclose paper vs testnet mode");


// The live 3D floor: loaded on demand, its screens carry the same provenance labels, its operators
// move from verified desk activity only, and it builds no DOM outside React.
const floor3d = files.filter(([p]) => p.includes("floor3d")).map(([, s]) => s).join("\n");
assert.match(floorScene, /lazy\(\(\) => import\("\.\/floor3d\/Floor3D"\)\)/, "the 3D floor must load on demand");
assert.match(floorScene, /<PhotoScene \{\.\.\.props\} \/>/, "the photo room stands in while the 3D floor loads or cannot run");
for (const label of ["LIVE PUBLIC", "LAST KNOWN / REST", "PAPER / SIM", "TESTNET · NO REAL MONEY", "HALTED", "ARMED", "no feed"]) {
  assert.ok(floor3d.includes(label), `3D floor screens must show ${label}`);
}
assert.match(floor3d, /operator\.update\(t, dt, busy, this\.reduced\)/, "3D operators must move from their desk's verified activity");
assert.match(floor3d, /active\(s\.id\)/, "3D desk activity must come from the floor stream");
assert.match(floor3d, /prefers-reduced-motion: reduce/, "the 3D floor must respect reduced motion");

console.log(`frontend contracts: ok (${files.length} source files)`);
