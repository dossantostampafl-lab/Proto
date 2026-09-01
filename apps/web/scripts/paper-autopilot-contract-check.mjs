import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/paper-autopilot.ts", import.meta.url), "utf8");
const terminalRuntime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/paper-autopilot.css", import.meta.url), "utf8");

assert.match(terminalRuntime, /import "\.\/paper-autopilot"/, "terminal runtime must load the paper autopilot controller");
assert.match(runtime, /\/paper\/status/, "autopilot controller must inspect authoritative paper runtime status");
assert.match(runtime, /\/paper\/start/, "autopilot controller must be able to enable the safe PAPER_TRADING runtime");
assert.match(runtime, /\/paper\/automation\/status/, "autopilot controller must read persistent server worker status");
assert.match(runtime, /\/paper\/automation\/start/, "autopilot controller must start the server worker rather than executing decisions in-browser");
assert.match(runtime, /\/paper\/automation\/stop/, "autopilot controller must stop the server worker");
assert.match(runtime, /financial_connectivity !== false \|\| current\.data\.real_money_execution !== false/, "controller must fail closed if paper safety boundaries are not false");
assert.match(runtime, /Continues on the server after this dashboard tab is closed/, "persistent server lifetime must be disclosed");
assert.match(runtime, /Persistent server task · simulation only · no exchange account · no financial connectivity · no real-money execution/, "autopilot must disclose the simulation-only boundary");
assert.doesNotMatch(runtime, /\/live\/market-data|\/live\/analytics|\/v1\/simulate/, "browser controller must not run the trading decision/execution loop itself");
assert.doesNotMatch(runtime, /setInterval\(\(\) => void executeCycle|document\.hidden|armedDirection/, "browser controller must not masquerade as a persistent automation worker");
assert.doesNotMatch(runtime, /api[_-]?key|bearer\s|private[_-]?key|wallet_address|broker_url/i, "autopilot must not implement account credentials or brokerage connectivity");
assert.match(styles, /\.paperAutopilot/, "autopilot must have dedicated visual treatment");
assert.match(styles, /@media\(max-width:620px\)/, "autopilot must support narrow tablet/mobile layouts");
assert.match(styles, /focus-visible/, "autopilot controls must preserve keyboard focus");
assert.match(styles, /input:disabled/, "running server configuration must visibly lock mutable inputs");

console.log("frontend persistent paper-autopilot contracts: ok");
