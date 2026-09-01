import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/paper-autopilot.ts", import.meta.url), "utf8");

assert.match(runtime, /live_market_ready/, "autopilot status must expose authoritative live readiness");
assert.match(runtime, /SERVER AUTOPILOT PAUSED/, "paused state must remain visible when execution prerequisites are not ready");
assert.match(runtime, /Persistent server task · simulation only/, "autopilot must retain simulation-only provenance");
assert.doesNotMatch(runtime, /api[_-]?key|private[_-]?key|wallet_address|broker_url/i, "autopilot UI must not add financial connectivity");

console.log("frontend autopilot freshness/provenance contracts: ok");
