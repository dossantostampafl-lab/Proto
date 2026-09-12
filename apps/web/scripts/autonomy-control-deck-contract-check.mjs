import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
const source = await readFile(new URL("../src/autonomy-control-deck.ts", import.meta.url), "utf8");
const operatorAuth = await readFile(new URL("../src/operator-auth.ts", import.meta.url), "utf8");
const dockerfile = await readFile(new URL("../../../Dockerfile", import.meta.url), "utf8");
const uiWorkflow = await readFile(new URL("../../../.github/workflows/production-ui-contract.yml", import.meta.url), "utf8");

assert.match(index, /\/src\/autonomy-control-deck\.ts/, "autonomous control deck must be active");
assert.match(index, /\/src\/operator-auth\.ts/, "operator auth bootstrap must be active");
assert.ok(
  index.indexOf("/src/operator-auth.ts") < index.indexOf("/src/autonomy-control-deck.ts"),
  "operator auth must load before mutating control-deck code",
);
assert.doesNotMatch(index, /\/src\/shadow-operator\.ts/, "superseded standalone shadow operator must not remain active");

for (const endpoint of [
  "/universe",
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
  "UNIVERSE",
  "AUTONOMY",
  "CREATION",
  "MEMORY",
  "FACT-ONLY RUNTIME",
  "CATALOG ONLY",
  "REAL MONEY READY",
]) {
  assert.match(source, new RegExp(label), `${label} must remain visible in the control deck`);
}

assert.match(source, /cache: "no-store"/, "control-deck reads must bypass stale browser cache");
assert.match(source, /AbortController/, "control-deck requests must be time bounded");
assert.match(source, /execution_connected/, "universe rendering must expose execution connectivity separately from catalog membership");
assert.match(source, /No record is synthesized/, "Decision Memory must preserve fact-only behavior");
assert.match(source, /without portfolio mutation/, "SHADOW semantics must remain explicit");

assert.match(operatorAuth, /X-Proto-Operator-Token/, "mutations must carry the operator identity header");
assert.match(operatorAuth, /window\.prompt/, "operator credential must be supplied interactively, not bundled");
assert.match(operatorAuth, /response\.status === 401/, "rejected operator credentials must be forgotten");
assert.doesNotMatch(operatorAuth, /localStorage|sessionStorage/, "operator credentials must never persist in browser storage");
assert.doesNotMatch(operatorAuth, /import\.meta\.env.*TOKEN/, "operator credentials must never enter the frontend build environment");

assert.match(
  dockerfile,
  /cat index\.html src\/operator-auth\.ts src\/approved-terminal\.tsx src\/autonomy-control-deck\.ts/,
  "deployment digest must cover every active dashboard security and control source",
);
assert.match(uiWorkflow, /apps\/web\/src\/autonomy-control-deck\.ts/, "production UI contract must verify the control-deck source");
assert.match(uiWorkflow, /operator-terminal-v3/, "production UI contract must target terminal v3");

console.log("autonomous control deck endpoints, operator auth and deployment provenance: ok");
