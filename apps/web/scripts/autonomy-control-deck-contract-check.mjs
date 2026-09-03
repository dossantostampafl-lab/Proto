import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
const source = await readFile(new URL("../src/autonomy-control-deck.ts", import.meta.url), "utf8");
const dockerfile = await readFile(new URL("../../../Dockerfile", import.meta.url), "utf8");
const uiWorkflow = await readFile(new URL("../../../.github/workflows/production-ui-contract.yml", import.meta.url), "utf8");

assert.match(index, /\/src\/autonomy-control-deck\.ts/, "autonomous control deck must be active");
assert.doesNotMatch(index, /\/src\/shadow-operator\.ts/, "superseded standalone shadow operator must not remain active");

for (const endpoint of [
  "/universe",
  "/equity-market/",
  "/creation/status",
  "/orchestration/status",
  "/orchestration/decision-memory/status",
  "/shadow/status",
  "/shadow/start",
  "/shadow/stop",
]) {
  assert.match(source, new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `${endpoint} must be wired into the control deck`);
}

for (const label of [
  "AUTONOMOUS CONTROL DECK",
  "MARKET EXPLORER",
  "UNIVERSE",
  "AUTONOMY",
  "CREATION",
  "MEMORY",
  "FACT-ONLY RUNTIME",
  "CATALOG ONLY",
  "READ-ONLY OBSERVATION",
  "REAL MONEY READY",
]) {
  assert.match(source, new RegExp(label), `${label} must remain visible in the control deck`);
}

assert.match(source, /data-instrument-id/, "registered instruments must be selectable in the Market Explorer");
assert.match(source, /encodeURIComponent\(instrumentId\)/, "equity observation requests must encode the instrument id");
assert.match(source, /currently_fresh/, "Market Explorer must render explicit provider freshness semantics");
assert.match(source, /freshness_threshold_seconds/, "Market Explorer must distinguish unknown from evaluated freshness");
assert.match(source, /cache: "no-store"/, "control-deck reads must bypass stale browser cache");
assert.match(source, /AbortController/, "control-deck requests must be time bounded");
assert.match(source, /execution_connected/, "universe rendering must expose execution connectivity separately from catalog membership");
assert.match(source, /No record is synthesized/, "Decision Memory must preserve fact-only behavior");
assert.match(source, /without portfolio mutation/, "SHADOW semantics must remain explicit");

assert.match(dockerfile, /cat index\.html src\/approved-terminal\.tsx src\/autonomy-control-deck\.ts/, "deployment digest must cover every active dashboard source");
assert.match(uiWorkflow, /apps\/web\/src\/autonomy-control-deck\.ts/, "production UI contract must verify the control-deck source");
assert.match(uiWorkflow, /operator-terminal-v3/, "production UI contract must target terminal v3");

console.log("autonomous control deck and Market Explorer contracts: ok");
