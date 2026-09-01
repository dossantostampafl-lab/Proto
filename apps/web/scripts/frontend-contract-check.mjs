import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const auditStyles = await readFile(new URL("../src/audit.css", import.meta.url), "utf8");
const index = await readFile(new URL("../index.html", import.meta.url), "utf8");

assert.match(source, /lastSequence\.current\[f\.symbol\]/, "market-series dedupe must remain sequence-aware");
assert.match(source, /f\.sequence<=lastSequence\.current\[f\.symbol\]/, "duplicate or regressed ticks must not append to chart history");
assert.match(source, /setEdgeHistory/, "canonical net-edge history must remain explicit");
assert.match(source, /current_intensity/, "Hawkes chart must use Hawkes intensity samples");
assert.match(source, /selectedMarkets=markets\.filter\(\(m\)=>m\.symbol===selected\)/, "research panels must stay isolated by selected symbol");
assert.match(source, /requestJson<LiveStatus>\("\/live\/status"\)/, "frontend must reconcile against authoritative live freshness");
assert.match(source, /REST FALLBACK/, "dashboard must expose transport fallback state");
assert.match(source, /SYNTHETIC EDGE FIELD/, "expiry torus must identify synthetic provenance");
assert.doesNotMatch(source, /LIVE EDGE FIELD/, "synthetic edge must never be labeled live");
assert.doesNotMatch(source, /RESOLUTION GRID · LIVE EXPIRIES/, "synthetic expiry grid must never be labeled live");
assert.match(styles, /LIVE PUBLIC FEED/, "legacy visual provenance badge must remain available");
assert.match(auditStyles, /sourceTag\.research/, "explicit research source tag styling is required");
assert.match(source, /ValidationPanel/, "validation lab must remain accessible from the main terminal");
assert.doesNotMatch(index, /validation-root/, "the page must mount only one React application");
assert.doesNotMatch(index, /validation\.tsx/, "validation must not mount as a second application");

console.log("frontend runtime contracts: ok");
