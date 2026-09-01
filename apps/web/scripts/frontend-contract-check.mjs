import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

assert.match(source, /lastSequence\.current\[f\.symbol\]/, "market-series dedupe must remain sequence-aware");
assert.match(source, /f\.sequence<=lastSequence\.current\[f\.symbol\]/, "duplicate or regressed ticks must not append to chart history");
assert.match(source, /setEdgeHistory/, "canonical net-edge history must remain explicit");
assert.match(source, /current_intensity/, "Hawkes chart must use Hawkes intensity samples");
assert.match(source, /selectedMarkets=markets\.filter\(\(m\)=>m\.symbol===selected\)/, "research panels must stay isolated by selected symbol");
assert.match(styles, /LIVE PUBLIC FEED/, "live market provenance badge is required");
assert.match(styles, /SYNTHETIC RESEARCH · NOT LIVE MARKET/, "synthetic research provenance badge is required");
assert.match(styles, /RESEARCH: PROBABILITY \/ EDGE \/ LIFECYCLE \/ TORUS \/ GREEKS \/ HAWKES/, "footer provenance legend is required");

console.log("frontend runtime contracts: ok");
