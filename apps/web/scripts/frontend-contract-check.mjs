import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/premium.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/premium.css", import.meta.url), "utf8");
const runtime = await readFile(new URL("../src/premium-runtime.ts", import.meta.url), "utf8");
const runtimeStyles = await readFile(new URL("../src/premium-runtime.css", import.meta.url), "utf8");
const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
const dockerfile = await readFile(new URL("../../../Dockerfile", import.meta.url), "utf8");
const railwayApp = await readFile(new URL("../../api/app/railway_app.py", import.meta.url), "utf8");
const design = await readFile(new URL("../../../DESIGN.md", import.meta.url), "utf8");

assert.match(source, /connection_generation/, "live frames must expose connection generation");
assert.match(source, /lastCursor\.current\[f\.symbol\]/, "market-series dedupe must remain generation/sequence aware");
assert.match(source, /f\.connection_generation === previous\.generation && f\.sequence <= previous\.sequence/, "duplicate or regressed ticks within one generation must be rejected");
assert.match(source, /generationChanged/, "chart history must explicitly handle source reconnect generations");
assert.match(source, /STATUS_TTL_MS/, "live freshness must have a local status-success TTL");
assert.match(source, /requestJson<LiveStatus>\("\/live\/status"\)/, "frontend must reconcile against authoritative live freshness");
assert.match(source, /REST FALLBACK/, "dashboard must expose transport fallback state");
assert.match(source, /Grouped from unique live ticks; not time-bucket OHLC\./, "tick-grouped candles must not masquerade as time OHLC");
assert.match(source, /selectedMarkets = markets\.filter\(\(m\) => m\.symbol === selected\)/, "research panels must stay isolated by selected symbol");
assert.match(source, /SYNTHETIC EDGE/, "expiry field must identify synthetic provenance");
assert.match(source, /SYNTHETIC RESEARCH/, "synthetic research provenance must be explicit");
assert.match(source, /LIVE PUBLIC FEED/, "live public provenance must be explicit");
assert.match(source, /PAPER \/ SIMULATION/, "paper/simulation provenance must be explicit");
assert.match(source, /Execution Simulator|EXECUTION SIMULATOR/, "automation lane must end in simulated execution semantics");
assert.match(source, /No exchange account or real-money execution path is exposed\./, "automation must state the financial-connectivity boundary");
assert.doesNotMatch(source, /LIVE EDGE FIELD/, "synthetic edge must never be labeled live");
assert.doesNotMatch(source, /RESOLUTION GRID · LIVE EXPIRIES/, "synthetic expiry grid must never be labeled live");
assert.match(source, /ValidationPanel/, "validation lab must remain accessible from the command terminal");

assert.match(runtime, /NAV_TARGETS/, "command navigation must have explicit section targets");
assert.match(runtime, /scrollIntoView/, "command navigation must move to the requested section");
assert.match(runtime, /aria-current/, "active command navigation state must be announced");
assert.match(runtime, /prefers-reduced-motion/, "runtime navigation must respect reduced motion");
assert.match(runtime, /document\.body\.style\.overflow = "hidden"/, "modal runtime must lock background scrolling");
assert.match(runtime, /event\.key === "Tab"/, "modal runtime must trap keyboard focus");
assert.match(runtime, /event\.key === "Escape"/, "modal runtime must preserve Escape dismissal");
assert.match(runtime, /opener\.focus\(\)/, "modal runtime must restore focus to its opener");
assert.match(runtimeStyles, /scroll-margin-top/, "section targets must reserve sticky-header scroll offset");

assert.match(styles, /prefers-reduced-motion/, "premium surface must respect reduced motion");
assert.match(styles, /focus-visible/, "premium controls must retain visible keyboard focus");
assert.match(styles, /scrollbar-color/, "application scrollbars must have a global baseline");
assert.match(design, /Expiry \/ Risk Field/, "durable design context must record the terminal signature");
assert.doesNotMatch(index, /validation-root/, "the page must mount only one React application");
assert.match(index, /\/src\/premium\.tsx/, "premium command dashboard must be the active application entrypoint");
assert.match(index, /\/src\/premium-runtime\.ts/, "premium interaction runtime must load with the active dashboard");
assert.match(dockerfile, /VITE_API_BASE_URL=""/, "single-origin deploy must not pin a generated Railway hostname");
assert.doesNotMatch(dockerfile, /proto-production-[^\s]+\.up\.railway\.app/, "frontend bundle must remain hostname-portable");
assert.match(railwayApp, /Cache-Control.*no-store/, "dashboard HTML must not be served from stale cache");
assert.match(railwayApp, /max-age=31536000, immutable/, "fingerprinted Vite assets should be cached immutably");

console.log("frontend premium runtime contracts: ok");
