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
    else if (/\.(tsx?|css)$/.test(entry.name)) out.push([path, (await readFile(path, "utf8")).replace(/\r\n/g, "\n")]);
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
  "/paper/start", "/paper/stop", "/v1/simulate",
  "/simulation/reset", "/killswitch/trigger", "/killswitch/reset", "/replay/start", "/replay/pause",
  "/replay/resume", "/replay/step", "/replay/restart", "/replay/seek", "/replay/speed", "/replay/reset",
  "/shadow/start", "/shadow/stop",
]) assert.ok(code.includes(`"${endpoint}"`), `operator command missing: ${endpoint}`);

// The paper autopilot is retired: autonomy runs through the agent pipeline and its permits.
assert.ok(!code.includes('"/paper/automation/start"'), "the retired paper autopilot must not be startable from the UI");

// Every read surface is wired to its canonical endpoint.
for (const endpoint of [
  "/health", "/system/status", "/live/status", "/live/market-data", "/live/history/", "/live/analytics/",
  "/market-lifecycle", "/models/calibration", "/v1/portfolio", "/pnl/attribution", "/hawkes/", "/analytics/greeks/",
  "/paper/status", "/v1/strategy/status", "/v1/drills/e2e/latest", "/replay/status", "/universe", "/orchestration/status",
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


assert.match(floorScene, /export function Scene\(props: SceneProps\) \{\n  return <PhotoScene \{\.\.\.props\} \/>;/, "the floor is the photo room, with nothing replacing it after load");
assert.doesNotMatch(floorScene, /floor3d|Floor3D/, "no second scene may swap in after the photo room loads");

// Every desk is a real control over the photo and opens a drawer of live, read-only data.
const desks = files.find(([p]) => p.endsWith("floorDesks.tsx"))?.[1] ?? "";
assert.match(floorScene, /role="button" tabIndex=\{0\}/, "each desk hotspot must be a focusable button");
assert.match(floorScene, /e\.key === "Enter" \|\| e\.key === " "/, "a desk must open from the keyboard");
assert.match(floorScene, /aria-label=\{`Open the \$\{label\} desk/, "a desk hotspot must say what it opens");
assert.match(floorScene, /role=\{onSelect \? "group" : "img"\}/, "an interactive scene must not hide its buttons behind role=img");
assert.match(floorView, /<DeskDrawer\b/, "the floor must open desks in a drawer");
assert.match(floorView, /aria-label=\{`Open the \$\{s\.label\} desk`\}/, "every desk must also be reachable from the desk cards");
assert.match(desks, /role="dialog" aria-modal="true"/, "the desk drawer is a modal dialog");
assert.match(desks, /event\.key === "Escape"/, "Escape closes the drawer");
assert.match(desks, /event\.key !== "Tab"/, "focus stays inside the open drawer");
assert.match(desks, /opener\?\.focus/, "focus returns to the desk that opened the drawer");
assert.match(desks, /visibilityState === "hidden"/, "a hidden tab must not poll");
assert.match(desks, /#floor\/\$\{id\}/, "the open desk is kept in the URL hash so it can be linked");
for (const endpoint of [
  "/v1/signals/recent", "/v1/agents/status", "/v1/agents/decisions/recent", "/v1/strategy/status", "/v1/risk/status",
  "/v1/risk/decisions/recent", "/v1/watchdog/status", "/v1/oms/orders", "/v1/oms/ledger", "/v1/drills/e2e/latest",
]) assert.ok(desks.includes(`"${endpoint}`), `desk data surface missing: ${endpoint}`);
for (const label of ["LIVE · updated", "STALE · last good read", "UNAVAILABLE"]) {
  assert.ok(desks.includes(label), `every desk panel must say its freshness: ${label}`);
}
assert.doesNotMatch(desks, /halt\/release|lockdown\/release|killswitch/i, "a desk can never release a halt or a lockdown");
assert.doesNotMatch(desks, /\bpost\(/, "desk actions go through the shared operator command, never a direct write");
assert.match(desks, /disabled=\{!token \|\| busy !== null\}/, "desk actions are disabled without the operator token");
assert.match(desks, /Set the operator token in the System view/, "a disabled desk action says why");

console.log(`frontend contracts: ok (${files.length} source files)`);
