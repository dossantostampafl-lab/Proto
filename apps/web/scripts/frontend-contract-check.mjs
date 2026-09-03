import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/approved-terminal.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/approved-terminal.css", import.meta.url), "utf8");
const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
const dockerfile = await readFile(new URL("../../../Dockerfile", import.meta.url), "utf8");
const railwayApp = await readFile(new URL("../../api/app/railway_app.py", import.meta.url), "utf8");
const pyproject = await readFile(new URL("../../../pyproject.toml", import.meta.url), "utf8");

async function missing(path, message) {
  try {
    await access(new URL(path, import.meta.url));
    assert.fail(message);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

assert.match(index, /\/src\/approved-terminal\.tsx/, "approved terminal must be active entrypoint");
assert.doesNotMatch(index, /\/src\/terminal-runtime\.ts/, "legacy DOM runtime must not mutate the approved React-owned dashboard");
assert.doesNotMatch(index, /premium/, "active HTML must not reference superseded premium surface");

assert.match(source, /main className="terminal approvedTerminal"/, "approved terminal must retain canonical terminal root");
assert.match(source, /BTC\/USD|\{symbol\}\/USD/, "BTC/ETH/SOL market identity must remain visible");
assert.match(source, /LIVE PUBLIC/, "live public provenance must be explicit");
assert.match(source, /SYNTHETIC RESEARCH/, "synthetic research provenance must be explicit");
assert.match(source, /PAPER \/ SIM/, "paper/simulation provenance must be explicit");
assert.match(source, /FINANCIAL CONNECTIVITY OFF/, "financial connectivity boundary must be visible");
assert.match(source, /REAL MONEY EXECUTION OFF/, "real-money boundary must be visible");
assert.match(source, /Prediction Market Quant Engine/, "approved product identity must be visible");
assert.doesNotMatch(source, /KILL SWITCH.*ARMED \/ SAFE/, "dashboard must not invent an unqueried kill-switch state");
assert.doesNotMatch(source, /LIMIT UTILIZATION/, "derived display score must not masquerade as a canonical risk limit");
assert.match(source, /DISPLAY RISK SCORE/, "derived composite risk display must be labeled as display-only");
assert.match(source, /L1 MICROSTRUCTURE/, "L1 bid-ask analytics must not masquerade as trade-tape order flow");

assert.match(source, /requestJson<LiveStatus>\("\/live\/status"\)/, "dashboard must reconcile authoritative live status");
assert.match(source, /requestJson<LiveMarketResponse>\("\/live\/market-data"\)/, "dashboard must read canonical public live market data");
assert.match(source, /\/live\/analytics\/\$\{sym\}/, "dashboard must read canonical public microstructure analytics");
assert.match(source, /\/live\/history\/\$\{sym\}\?limit=\$\{HISTORY_LIMIT\}/, "dashboard must bootstrap charts from canonical persisted public history");
assert.match(source, /HISTORY_LIMIT = 1000/, "live-history bootstrap must remain bounded");
assert.match(source, /requestJson<LifecycleResponse>\("\/market-lifecycle"\)/, "research lifecycle must come from isolated research endpoint");
assert.match(source, /requestJson<Calibration>\("\/models\/calibration"\)/, "model calibration must come from persisted research lineage endpoint");
assert.match(source, /requestJson<Portfolio>\("\/v1\/portfolio"\)/, "portfolio must come from canonical paper/simulation endpoint");
assert.match(source, /requestJson<PnL>\("\/pnl\/attribution"\)/, "P&L attribution must come from canonical endpoint");
assert.match(source, /\/hawkes\/\$\{selected\}/, "Hawkes surface must be explicitly requested from isolated research endpoint");
assert.match(source, /\/analytics\/greeks\/\$\{encodeURIComponent\(selectedMarket\.market_id\)\}/, "synthetic Greeks must use explicit market lineage");
assert.match(source, /new WebSocket\(`\$\{WS_BASE\}\/ws\/market-data`\)/, "WebSocket must remain the live market-data transport");
assert.match(source, /ws\.onclose/, "WebSocket disconnects must be handled");
assert.match(source, /window\.setTimeout\(connect, 1500\)/, "WebSocket must reconnect after transport loss");
assert.match(source, /cache: "no-store"/, "operational reads must bypass stale browser cache");
assert.match(source, /AbortController/, "network reads must be bounded and cancellable");

for (const endpoint of [
  "/paper/start",
  "/paper/stop",
  "/paper/automation/start",
  "/paper/automation/stop",
  "/v1/simulate",
  "/simulation/reset",
  "/killswitch/trigger",
  "/killswitch/reset",
  "/replay/start",
  "/replay/pause",
  "/replay/resume",
  "/replay/step",
  "/replay/restart",
  "/replay/seek",
  "/replay/speed",
  "/replay/reset",
]) {
  assert.match(source, new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `${endpoint} operator action must be wired`);
}
assert.match(source, /type="file"[^>]*accept="application\/json,\.json"/, "historical replay must load a real user-provided JSON dataset");
assert.match(source, /Server risk gates remain authoritative/, "manual paper order must disclose authoritative risk admission");
assert.match(source, /Replay never invents a dataset/, "replay workspace must forbid fabricated datasets");
assert.match(source, /activeView === "paper"/, "paper trading must have a dedicated workspace");
assert.match(source, /activeView === "research"/, "research must have a dedicated workspace");
assert.match(source, /activeView === "replay"/, "replay must have a dedicated workspace");
assert.match(source, /activeView === "risk"/, "risk must have a dedicated workspace");
assert.match(source, /activeView === "system"/, "system diagnostics must have a dedicated workspace");

assert.match(source, /className={`marketTile assetButton/, "asset selectors must retain production browser E2E compatibility");
assert.match(source, /className="center compatibilityCenter"/, "selected live chart must retain production browser E2E selector contract");
assert.match(source, /FEED \{liveFresh \? "LIVE" : "STALE"\}/, "live status must expose FEED LIVE or FEED STALE");
assert.match(source, /STREAMING|RECONCILING/, "transport state must remain visible");

for (const label of [
  "MARKET LIFECYCLE / RESOLUTION GRID",
  "PORTFOLIO STATUS",
  "L1 MICROSTRUCTURE",
  "RISK / SAFETY",
  "EDGE TIMELINE",
  "P&L CURVE",
  "POSITIONS",
  "MODEL CALIBRATION",
  "HAWKES CASCADE",
  "SYNTHETIC GREEKS",
  "PAPER TRADING CONTROL",
  "MANUAL PAPER ORDER",
  "PAPER AUTOPILOT",
  "HISTORICAL REPLAY CONTROL",
  "OPERATOR ACTION LOG",
]) {
  assert.match(source, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `${label} panel must remain present`);
}

assert.match(styles, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/, "desktop must preserve three primary asset cards");
assert.match(styles, /grid-template-columns:1\.7fr 1\.1fr \.8fr \.72fr/, "desktop must preserve dense four-column analytics grid");
assert.match(styles, /\.riskDonut/, "portfolio risk must have dedicated donut encoding");
assert.match(styles, /\.reliability/, "calibration reliability must have dedicated chart styling");
assert.match(styles, /\.quantTable/, "lifecycle and positions must use institutional table styling");
assert.match(styles, /\.operatorGrid/, "functional workspaces must have a dedicated operator layout");
assert.match(styles, /\.workspaceTabs/, "workspace navigation must have explicit styling");
assert.match(styles, /\.buyButton/, "paper BUY action must be visually distinct");
assert.match(styles, /\.sellButton/, "paper SELL action must be visually distinct");
assert.match(styles, /\.dangerButton/, "kill-switch action must be visually distinct");
assert.match(styles, /@media\(max-width:1180px\)/, "dashboard must include landscape-tablet breakpoint");
assert.match(styles, /@media\(max-width:820px\)/, "dashboard must include compact/tablet breakpoint");
assert.match(styles, /overflow-x:hidden/, "root layout must prevent document-level horizontal overflow");

await missing("../src/premium.tsx", "superseded premium.tsx must remain removed");
await missing("../src/premium.css", "superseded premium.css must remain removed");

assert.match(dockerfile, /VITE_API_BASE_URL=""/, "single-origin deploy must remain hostname-portable");
assert.match(dockerfile, /SYNTHETIC_RESEARCH_ENABLED=true/, "Railway image must explicitly enable isolated synthetic research");
assert.match(dockerfile, /LIVE_PERSISTENCE_ENABLED=true/, "Railway image must preserve public live-history persistence");
assert.doesNotMatch(dockerfile, /\n\s*PERSISTENCE_ENABLED=true/, "live deployment must not implicitly enable general simulation persistence");
assert.match(dockerfile, /sha256sum src\/approved-terminal\.tsx/, "web image must bind a digest to the exact approved UI source");
assert.match(dockerfile, /proto-ui-source\.sha256/, "web image must carry the approved UI digest into dist");
assert.match(pyproject, /"aiosqlite>=0\.21,<1"/, "runtime dependencies must include async sqlite driver used by default live persistence");
assert.doesNotMatch(dockerfile, /proto-production-[^\s]+\.up\.railway\.app/, "bundle must not hardcode Railway hostname");
assert.match(railwayApp, /Cache-Control.*no-store/, "HTML must not be served from stale cache");
assert.match(railwayApp, /max-age=31536000, immutable/, "fingerprinted assets must remain immutable-cacheable");
assert.match(railwayApp, /X-Proto-UI-Source-SHA256/, "runtime must expose the digest of the actually bundled approved UI source");
assert.match(railwayApp, /proto-ui-source\.sha256/, "runtime UI digest must come from the image artifact rather than a static release label");

console.log("operator terminal provenance, controls, transport, semantics, layout and deployment contracts: ok");
