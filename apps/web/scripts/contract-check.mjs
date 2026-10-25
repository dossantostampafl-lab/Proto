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
]) assert.doesNotMatch(code, pattern, why);
// localStorage holds one thing only: the viewer's floor view choice, in floorViewPref.ts, guarded by try/catch.
for (const [p, s] of files) {
  if (p.endsWith("floorViewPref.ts") || p.endsWith(".css")) continue;
  assert.doesNotMatch(s, /localStorage/, `${p}: operator token must not outlive the browser session (only floorViewPref.ts may use localStorage)`);
}
const viewPref = files.find(([p]) => p.endsWith("floorViewPref.ts"))?.[1] ?? "";
assert.match(viewPref, /const KEY = "proto\.floor\.view";/, "the floor view choice has its own storage key");
assert.equal((viewPref.match(/localStorage\.\w+\(/g) ?? []).length, 2, "floorViewPref.ts only reads and writes the view key");
assert.equal((viewPref.match(/localStorage\.(getItem|setItem)\(KEY/g) ?? []).length, 2, "floorViewPref.ts touches no key but its own");
assert.equal((viewPref.match(/try \{ (return )?window\.localStorage/g) ?? []).length, 2, "every storage access is wrapped in try/catch");
assert.doesNotMatch(viewPref, /token/i, "the stored preference never involves the operator token");

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
// The photo room (the fallback where WebGL cannot run) may be a photograph, but only of a room with
// blank screens: every operational value is drawn live over it, and the photo is the only picture.
assert.equal((floorScene.match(/<image\b/g) ?? []).length, 1, "the floor may hold a single room photo");
assert.doesNotMatch(floorScene, /<img\b/, "the floor may hold a single room photo");
assert.match(floorScene, /import ROOM from "\.\.\/assets\/floor-room\.webp"/, "the room photo is the blank-screen asset");
assert.match(floorScene, /<image id="fl-room" href=\{ROOM\}/, "the room photo is the blank-screen asset");
assert.match(floorScene, /<WallScreens\b/, "wall screens must be drawn live over the photo");
assert.match(floorScene, /onError=\{\(\) => setPhoto\(false\)\}/, "a missing photo falls back to the vector room");
assert.match(floorView, /const \[stateFresh, setStateFresh\]/, "floor state polling must expose freshness");
assert.match(floorView, /source=\{state == null \? undefined : state\.data_mode === "TESTNET"/, "floor sidebars must disclose paper vs testnet mode");

// The floor is the live 3D room, loaded on demand. While its chunk loads a neutral loading state is
// shown, never the photo, so no scene ever swaps in after load; the photo room is used only where
// WebGL cannot run (or the GPU context is lost).
const sceneFn = floorScene.slice(floorScene.indexOf("export function Scene("), floorScene.indexOf("function PhotoScene("));
const floor3d = files.filter(([p]) => p.includes("floor3d")).map(([, s]) => s).join("\n");
assert.match(floorScene, /lazy\(\(\) => import\("\.\/floor3d\/Floor3D"\)\)/, "the 3D floor must load on demand");
assert.match(sceneFn, /useState<"3d" \| "photo">\(\(\) => \(canRender3d\(\) \? "3d" : "photo"\)\)/, "the photo room is chosen up front, only when WebGL cannot run");
assert.match(sceneFn, /<Suspense fallback=\{<Loading3D \/>\}>/, "the 3D floor shows a neutral loading state while its chunk loads");
assert.doesNotMatch(sceneFn, /fallback=\{<PhotoScene/, "the photo must never stand in while the 3D floor loads");
assert.match(floorScene, /Carregando pregão 3D…/, "the loading state says what is loading");
assert.match(floorScene, /getContext\("webgl2"\)/, "WebGL support is probed, not assumed");
assert.match(floor3d, /Math\.min\(window\.devicePixelRatio \|\| 1, 1\.5\)/, "the 3D floor caps the pixel ratio at 1.5 for tablets");
assert.match(floor3d, /prefers-reduced-motion: reduce/, "the 3D floor must respect reduced motion");
assert.match(floor3d, /IntersectionObserver/, "the 3D floor pauses while off-screen");
assert.match(floor3d, /visibilitychange/, "the 3D floor pauses while the tab is hidden");
assert.match(floor3d, /instance\.dispose\(\)/, "the 3D floor disposes its GPU resources on unmount");
assert.doesNotMatch(floor3d, /EffectComposer|MeshoptDecoder|DRACOLoader|KTX2Loader/, "the 3D floor needs no post-processing and no WebAssembly decoder (the page CSP has no wasm-unsafe-eval)");
assert.match(floor3d, /shadow\.mapSize\.set\(coarse \? 1024 : 2048/, "touch devices get the smaller shadow map");
assert.match(floor3d, /import\("\.\/Floor3DAssets"\)/, "people and textures load on demand, after the scene chunk");
assert.match(floor3d, /role="progressbar"/, "the 3D floor shows its asset download progress");
// Trader agents: only what /v1/agents/roster says, labelled as virtual capital; an elimination walks out.
assert.match(floor3d, /"\/v1\/agents\/roster"/, "agent name tags come from the roster API");
assert.match(floor3d, /raw\.capital !== "VIRTUAL"/, "a roster that is not virtual capital is not shown");
assert.match(floor3d, /VIRTUAL \$\{mode === "TESTNET"/, "agent tags say the capital is virtual");
assert.match(floor3d, /AGENT ROSTER UNAVAILABLE/, "an unavailable roster says so and shows no tags");
assert.match(floor3d, /a\.event_type === "AGENT_ELIMINATED"\) this\.walkOut\(seat\)/, "an eliminated agent walks out");
assert.match(floor3d, /finiteDecimal\(v\)/, "agent equity is printed from the decimal string, never estimated");
assert.match(floor3d, /if \(first \|\| !data\.busy\[a\.station\]\) continue;/, "operators carry work only for fresh live events, never for history");
assert.match(floor3d, /select\.current\?\.\(drawerOf\(id\)\)/, "tapping a 3D desk opens the same drawer as the photo room");
for (const label of ["LIVE PUBLIC", "LAST KNOWN / REST", "PAPER / SIM", "TESTNET · NO REAL MONEY", "MODE UNAVAILABLE", "FLOOR STATE UNAVAILABLE", "STALE", "HALTED", "ARMED", "no feed", "NOT CONFIGURED", "NO REAL MONEY", "ROSTER UNAVAILABLE"]) {
  assert.ok(floor3d.includes(label), `3D floor must show ${label}`);
}

// "Realista | Prédio": the stylized building is a second view of the same live floor, in its own
// on-demand chunk, with the same truthful labels; it never invents a value.
const building = files.filter(([p]) => p.includes("floorBuilding")).map(([, s]) => s).join("\n");
assert.match(floorView, /lazy\(\(\) => import\("\.\/floorBuilding\/FloorBuilding"\)\)/, "the building view loads on demand");
assert.match(floorView, />Realista<\/button>/, "the floor offers the realistic view");
assert.match(floorView, />Prédio<\/button>/, "the floor offers the building view");
assert.match(floorView, /useFloorViewMode\(\)/, "the view choice is remembered");
assert.match(floorView, /<Scene market=\{market\}/, "the realistic room stays available");
assert.match(building, /"\/v1\/agents\/roster"/, "building desks come from the roster API");
assert.match(building, /"\/v1\/oms\/ledger"/, "building limits come from the OMS ledger");
assert.match(building, /raw\.capital !== "VIRTUAL"/, "a roster that is not virtual capital is not shown");
assert.match(building, /capital virtual \(testnet\)/, "agent tags say the capital is virtual");
assert.match(building, /desde que você abriu/, "session-only curves say so");
assert.match(building, /LANE_LABEL\[xp\.state\]/, "the XP floor shows the real lane state");
for (const label of ["AGENT ROSTER UNAVAILABLE", "LEDGER UNAVAILABLE", "FLOOR STATE UNAVAILABLE", "MODE UNAVAILABLE", "TESTNET · NO REAL MONEY", "NOT CONFIGURED", "STALE", "R$ —", "não medido"]) {
  assert.ok(building.includes(label), `building view must show ${label}`);
}
assert.match(building, /Math\.min\(window\.devicePixelRatio \|\| 1, 1\.5\)/, "the building caps the pixel ratio at 1.5");
assert.match(building, /OrthographicCamera/, "the building uses an orthographic camera");
assert.match(building, /InstancedMesh/, "desks and monitors are instanced");
assert.doesNotMatch(building, /shadowMap|castShadow|MeshStandardMaterial|MeshPhysicalMaterial/, "the building uses no shadow maps and only cheap materials");
assert.match(building, /prefers-reduced-motion: reduce/, "the building respects reduced motion");
assert.match(building, /IntersectionObserver/, "the building pauses while off-screen");
assert.match(building, /visibilitychange/, "the building pauses while the tab is hidden");
assert.match(building, /instance\.dispose\(\)/, "the building disposes its GPU resources on unmount");
assert.match(building, /Pausar animação/, "the building animation can be paused");

// "Controle": the mission-control room, a third view of the same live floor; only real numbers.
const control = files.filter(([p]) => p.includes("floorControl")).map(([, s]) => s).join("\n");
assert.match(floorView, /lazy\(\(\) => import\("\.\/floorControl\/FloorControl"\)\)/, "the control view loads on demand");
assert.match(floorView, />Controle<\/button>/, "the floor offers the control view");
assert.match(control, /"\/v1\/floor\/summary"/, "control totals come from the journal summary");
assert.match(control, /windowFull \? "≥ "/, "a truncated journal window is printed as at least");
for (const label of ["SUMMARY UNAVAILABLE", "AGENT ROSTER UNAVAILABLE", "MODE UNAVAILABLE", "TESTNET · NO REAL MONEY", "NOT CONFIGURED", "perda diária: não medida", "fora do pregão"]) {
  assert.ok(control.includes(label), `control view must show ${label}`);
}
// The room is a pre-rendered picture with no people, text or numbers; everything live is drawn over it.
assert.match(control, /"floorcontrol\/room\.webp"/, "the control room is the rendered room image");
assert.match(control, /"floorcontrol\/room-portrait\.webp"/, "phones get the portrait render");
assert.match(control, /import ANCHORS from "\.\/anchors\.json"/, "robots and tags stand where the renderer says the seats are");
assert.doesNotMatch(control, /from "three"/, "the control room needs no WebGL");
assert.match(control, /prefers-reduced-motion: reduce/, "the control room respects reduced motion");
assert.match(control, /IntersectionObserver/, "the control room pauses while off-screen");
assert.match(control, /visibilitychange/, "the control room pauses while the tab is hidden");
assert.match(control, /Pausar animação/, "the control room animation can be paused");
assert.doesNotMatch(floorView, /buildingFailed \|\| !canRender3d\(\) \? <BuildingUnavailable text="Controle/, "the rendered Control Room must never require WebGL");
assert.match(control, /function AgentRoster\(/, "the approved view shows API-backed agents");
assert.match(control, /function Activity\(/, "the approved view shows journal-backed recent activity");
assert.match(control, /<AgentRoster agents=\{agents\}/, "the real roster overlay is mounted");
assert.match(control, /<Activity actions=\{actions\}/, "the real journal overlay is mounted");


// Every desk is a real control over the photo and opens a drawer of live, read-only data.
const desks = ["floorDesks.tsx", "deskData.ts"].map(name => files.find(([p]) => p.endsWith(name))?.[1] ?? "").join("\n");
const api = files.find(([p]) => p.endsWith("api.ts"))?.[1] ?? "";
assert.match(floorScene, /role="button" tabIndex=\{0\}/, "each desk hotspot must be a focusable button");
assert.match(floorScene, /e\.key === "Enter" \|\| e\.key === " "/, "a desk must open from the keyboard");
assert.match(floorScene, /aria-label=\{`Open the \$\{label\} desk\. Status: \$\{TONE_LABEL\[/, "a desk hotspot must say what it opens and its tone in words");
assert.match(floorScene, /aria-haspopup="dialog"/, "a desk hotspot opens a dialog");
assert.match(floorScene, /role=\{onSelect \? "group" : "img"\}/, "an interactive scene must not hide its buttons behind role=img");
assert.match(floorScene, /vectorEffect="non-scaling-stroke"/, "the desk focus ring must keep its width at any zoom");
assert.match(floorView, /<DeskDrawer\b/, "the floor must open desks in a drawer");
assert.match(floorView, /<DeskBoundary\b/, "a failing desk must not take the app down");
assert.match(floorView, /stateFresh=\{floor\.stateFresh\}/, "the drawer must know when the floor state is stale");
assert.match(floorView, /aria-label=\{`Open the \$\{s\.label\} desk, status \$\{TONE_LABEL\[/, "every desk must also be reachable from the desk cards, with its tone in words");
assert.doesNotMatch(floorView, /\.slice\(11, 19\)/, "timestamps are read through hms(), never sliced unchecked");
assert.match(desks, /role="dialog" aria-modal="true"/, "the desk drawer is a modal dialog");
assert.match(desks, /setAttribute\("inert", ""\)/, "the page behind an open drawer is inert");
assert.match(desks, /event\.key === "Escape"/, "Escape closes the drawer");
assert.match(desks, /event\.key !== "Tab"/, "focus stays inside the open drawer");
assert.match(desks, /\.fl-hit\[data-desk=/, "focus returns to the desk even when the drawer was opened by a link");
assert.match(desks, /visibilityState === "hidden"/, "a hidden tab must not poll");
assert.match(desks, /mine !== seq\.current/, "an older poll answer must never overwrite a newer one");
assert.match(desks, /history\.pushState/, "opening a desk is a history entry, so Back closes it");
assert.match(desks, /#floor\/\$\{id\}/, "the open desk is kept in the URL hash so it can be linked");
for (const endpoint of [
  "/v1/signals/recent", "/v1/agents/status", "/v1/agents/decisions/recent", "/v1/strategy/status", "/v1/risk/status",
  "/v1/risk/decisions/recent", "/v1/watchdog/status", "/v1/oms/orders", "/v1/oms/ledger", "/v1/drills/e2e/latest",
]) assert.ok(desks.includes(`"${endpoint}`), `desk data surface missing: ${endpoint}`);
for (const label of ["LIVE · updated", "STALE · last good read", "UNAVAILABLE", "PAUSED · tab hidden"]) {
  assert.ok(desks.includes(label), `every desk panel must say its freshness: ${label}`);
}
assert.match(desks, /assertNever\(state\)/, "the freshness states are exhaustive");
assert.doesNotMatch(desks, /halt\/release|lockdown\/release|killswitch/i, "a desk can never release a halt or a lockdown");
assert.doesNotMatch(desks, /\bpost\(/, "desk actions go through the shared operator command, never a direct write");
// B1: the outcome of an action is visible in the drawer, in one always-mounted live region; the button label does not change while busy.
assert.match(desks, /role="status" aria-live="polite" aria-atomic="true"/, "the drawer has one always-mounted status region");
assert.match(desks, /fl-outcome/, "the outcome of an action is shown in the drawer");
assert.match(desks, /aria-busy=\{busy === label\}/, "a busy action keeps its label and says it is busy");
assert.match(desks, /aria-disabled=\{token \? undefined : true\}/, "an action without the operator token stays focusable and says it is disabled");
assert.match(desks, /aria-describedby=\{token \? undefined : TOKEN_HINT\}/, "a disabled action points to the reason");
assert.match(desks, /Set it in the System view|set it in the System view/, "a disabled desk action says why");
assert.match(desks, /Confirm \$\{verb\}/, "Resolve and Run closed test need a second click");
// B2: a frozen checkpoint is never printed as a measurement.
assert.match(desks, /export const finiteDecimal/, "decimals are validated as strings");
assert.match(desks, /not measured/, "an unmeasured equity says so");
assert.match(desks, /last checkpoint, executor unavailable/, "an unmeasured equity names its cause");
assert.doesNotMatch(desks, /Number\((a|b|k|l|d)\.[a-z_]+\)|Number\(v\)/, "ledger and limit decimals are never formatted through Number()");
// B3: one odd field is that panel's problem, not the app's.
assert.match(desks, /class DeskBoundary extends Component/, "desk views have an error boundary");
assert.match(desks, /Desk view failed - reload/, "a failed desk says so");
for (const guard of ["isRecord", "Array.isArray", "Number.isFinite"]) assert.ok(desks.includes(guard), `desk payloads need runtime guards: ${guard}`);
// XP / B3 is visible and truthful: a strip on the floor, a section in the OMS desk, the session in the Fusion desk.
for (const state of ["NOT CONFIGURED", "UNAVAILABLE", "LIVE", "HALTED"]) assert.ok(desks.includes(`"${state}"`) || desks.includes(`${state}`), `XP lane state missing: ${state}`);
assert.match(floorView, /<XpStrip\b/, "the floor shows the XP / B3 lane strip");
assert.match(floorView, /xp=\{xp\}/, "the desks receive the XP lane");
assert.match(desks, /XP routing is off: XP_EXECUTOR_URL is not set on the API/, "an XP lane that is off says why");
assert.match(desks, /DEMO ACCOUNT · TESTNET · NO REAL MONEY/, "XP is labelled as a demo account");
assert.match(desks, /\/v1\/oms\/orders\?venue=XP_B3/, "XP orders are read with ?venue=XP_B3");
assert.match(desks, /\$\{suffix\}/, "XP cancel and resolve carry ?venue=XP_B3");
assert.match(desks, /session hours \(no holiday calendar\)|no holiday calendar/, "the B3 session says it has no holiday calendar");
assert.match(desks, /America\/Sao_Paulo/, "the B3 session is computed in São Paulo time");
assert.match(desks, /PROTO's own position/, "an XP position says how PROTO's own part is computed");
assert.match(desks, /export function subDecimal|function subDecimal/, "own positions are subtracted on decimal strings");
assert.match(desks, /isB3\(/, "B3 decisions and signals carry a B3 tag");
assert.doesNotMatch(desks, /real[- ]money order|live order/i, "XP never reads as real money");

// Transport: an error body that is not JSON is its HTTP status, bad JSON on a 200 is "invalid JSON".
assert.match(api, /invalid JSON/, "invalid JSON on a success status must be reported as such");
assert.match(api, /if \(!response\.ok\) \{/, "the body is parsed only after the status is checked");

console.log(`frontend contracts: ok (${files.length} source files)`);
