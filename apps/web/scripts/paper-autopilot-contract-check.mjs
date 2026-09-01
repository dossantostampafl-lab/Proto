import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/paper-autopilot.ts", import.meta.url), "utf8");
const terminalRuntime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/paper-autopilot.css", import.meta.url), "utf8");

assert.match(terminalRuntime, /import "\.\/paper-autopilot"/, "terminal runtime must load the paper autopilot");
assert.match(runtime, /\/paper\/status/, "autopilot must require authoritative paper runtime status");
assert.match(runtime, /paper_execution_enabled === true/, "autopilot must fail closed unless paper execution is enabled");
assert.match(runtime, /financial_connectivity === false/, "autopilot must require no financial connectivity");
assert.match(runtime, /real_money_execution === false/, "autopilot must require no real-money execution");
assert.match(runtime, /\/live\/market-data\/\$\{symbol\}/, "autopilot must use canonical public live quotes");
assert.match(runtime, /\/live\/analytics\/\$\{symbol\}/, "autopilot must use canonical live analytics");
assert.match(runtime, /\/v1\/simulate/, "autopilot must submit only to the canonical simulator");
assert.match(runtime, /Math\.abs\(imbalance\) < config\.thresholdValue \* RESET_THRESHOLD_FACTOR/, "autopilot must use hysteresis before re-arming the same direction");
assert.match(runtime, /armedDirection === side/, "autopilot must prevent repeated same-regime accumulation");
assert.match(runtime, /Date\.now\(\) - lastActionAt < cooldownMs/, "autopilot must enforce cooldown");
assert.match(runtime, /spreadBps > MAX_SPREAD_BPS/, "autopilot must enforce a spread guard");
assert.match(runtime, /config\.quantityValue > topSize/, "autopilot must enforce top-of-book liquidity guard");
assert.match(runtime, /document\.hidden/, "autopilot must pause when the browser session is not visible");
assert.match(runtime, /Runs only while this dashboard session is open/, "browser-session execution lifetime must be disclosed");
assert.match(runtime, /Simulation only · no exchange account · no financial connectivity · no real-money execution/, "autopilot must disclose the simulation-only boundary");
assert.doesNotMatch(runtime, /api[_-]?key|bearer\s|private[_-]?key|wallet_address|broker_url/i, "autopilot must not implement account credentials or brokerage connectivity");
assert.match(styles, /\.paperAutopilot/, "autopilot must have dedicated visual treatment");
assert.match(styles, /@media\(max-width:620px\)/, "autopilot must support narrow tablet/mobile layouts");
assert.match(styles, /focus-visible/, "autopilot controls must preserve keyboard focus");

console.log("frontend paper-autopilot safety contracts: ok");
