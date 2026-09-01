import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/paper-autopilot.ts", import.meta.url), "utf8");

assert.match(runtime, /paper_runtime_ready/, "autopilot UI must expose paper runtime readiness");
assert.match(runtime, /SERVER AUTOPILOT PAUSED/, "autopilot UI must expose a paused state instead of pretending active execution");
assert.match(runtime, /financial_connectivity === false/, "autopilot UI must retain the no-financial-connectivity boundary");
assert.match(runtime, /real_money_execution === false/, "autopilot UI must retain the no-real-money boundary");

console.log("frontend autopilot live-boundary contracts: ok");
