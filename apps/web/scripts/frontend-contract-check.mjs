import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/terminal.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/terminal.css", import.meta.url), "utf8");
const runtime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");
const runtimeStyles = await readFile(new URL("../src/terminal-runtime.css", import.meta.url), "utf8");
const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
const dockerfile = await readFile(new URL("../../../Dockerfile", import.meta.url), "utf8");
const railwayApp = await readFile(new URL("../../api/app/railway_app.py", import.meta.url), "utf8");
const design = await readFile(new URL("../../../DESIGN.md", import.meta.url), "utf8");

async function missing(path, message) { try { await access(new URL(path, import.meta.url)); assert.fail(message); } catch (error) { if (error?.code !== "ENOENT") throw error; } }

assert.match(source, /connection_generation/, "live frames must expose connection generation");
assert.match(source, /frame\.connection_generation===prev\.generation&&frame\.sequence<=prev\.sequence/, "dedupe must use generation and sequence");
assert.match(source, /STATUS_TTL_MS/, "live freshness must have a local success TTL");
assert.match(source, /CANDLE_MS = 5000/, "live chart must use explicit time-bucket OHLC");
assert.match(source, /Math\.floor\(sourceMs \/ CANDLE_MS\)/, "OHLC bucket must derive from source timestamp");
assert.match(source, /120\)/, "websocket batching interval must remain bounded");
assert.match(source, /requestJson<LiveStatus>\("\/live\/status"\)/, "frontend must reconcile authoritative live status");
assert.match(source, /REST FALLBACK/, "transport fallback must be visible");
assert.match(source, /LIVE PUBLIC/, "live provenance must be explicit");
assert.match(source, /SYNTHETIC RESEARCH/, "research provenance must be explicit");
assert.match(source, /PAPER \/ SIM/, "paper provenance must be explicit");
assert.match(source, /EXECUTION SIMULATOR/, "automation must terminate in simulated execution");
assert.match(source, /No exchange-account connectivity or real-money execution path is exposed\./, "financial boundary must be explicit");
assert.match(source, /L1 ORDER BOOK/, "frontend must not claim unavailable L2 depth");
assert.match(source, /ValidationPanel/, "validation lab must remain integrated");
assert.match(runtime, /event\.key !== "Tab"/, "validation modal must trap keyboard focus");
assert.match(runtime, /event\.key === "Escape"/, "validation modal must support Escape");
assert.match(runtime, /document\.body\.style\.overflow = "hidden"/, "validation modal must lock background scrolling");
assert.match(runtime, /IntersectionObserver/, "command navigation must track visible sections");
assert.match(runtime, /aria-current/, "visible command section must be announced");
assert.match(runtimeStyles, /aria-current="page"/, "active command section must have visible styling");
assert.match(styles, /prefers-reduced-motion/, "terminal must respect reduced motion");
assert.match(styles, /focus-visible/, "terminal controls must expose keyboard focus");
assert.match(styles, /scrollbar-color/, "terminal must define scrollbar baseline");
assert.match(styles, /@media\(max-width:820px\)/, "terminal must include tablet breakpoint");
assert.match(design, /Expiry \/ Risk Field/, "durable design context must preserve signature field");
assert.match(index, /\/src\/terminal\.tsx/, "terminal must be active entrypoint");
assert.match(index, /\/src\/terminal-runtime\.ts/, "terminal runtime must be active");
assert.doesNotMatch(index, /premium/, "active HTML must not reference superseded premium surface");
await missing("../src/premium.tsx", "superseded premium.tsx must remain removed");
await missing("../src/premium.css", "superseded premium.css must remain removed");
await missing("../src/premium-runtime.ts", "superseded premium runtime must remain removed");
await missing("../src/premium-runtime.css", "superseded premium runtime styles must remain removed");
assert.match(dockerfile, /VITE_API_BASE_URL=""/, "single-origin deploy must remain hostname-portable");
assert.doesNotMatch(dockerfile, /proto-production-[^\s]+\.up\.railway\.app/, "bundle must not hardcode Railway hostname");
assert.match(railwayApp, /Cache-Control.*no-store/, "HTML must not be served from stale cache");
assert.match(railwayApp, /max-age=31536000, immutable/, "fingerprinted assets must remain immutable-cacheable");

console.log("frontend single-surface contracts: ok");
