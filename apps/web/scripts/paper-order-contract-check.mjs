import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/paper-order-runtime.ts", import.meta.url), "utf8");
const terminalRuntime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/paper-order-runtime.css", import.meta.url), "utf8");

assert.match(terminalRuntime, /import "\.\/paper-order-runtime"/, "terminal runtime must load the functional paper order console");
assert.match(runtime, /\/v1\/simulate/, "paper console must submit only to the canonical simulation endpoint");
assert.match(runtime, /\/live\/market-data\/\$\{symbol\}/, "paper console must price from canonical public live market data");
assert.match(runtime, /\/live\/analytics\/\$\{symbol\}/, "paper console must use canonical public analytics for simulation snapshot context");
assert.match(runtime, /Canonical live analytics are unavailable; simulation was not submitted\./, "paper console must fail closed when canonical analytics are unavailable");
assert.doesNotMatch(runtime, /market_probability:\s*0\.5/, "paper console must not invent a prediction-market probability for crypto simulation");
assert.match(runtime, /Backend-authoritative simulation only/, "paper console must disclose server-authoritative simulation semantics");
assert.match(runtime, /No exchange credentials · no financial connectivity · no real-money execution/, "paper console must disclose the financial safety boundary");
assert.match(runtime, /server_execution_permitted: true/, "paper request must opt into the existing simulation gate rather than bypass it");
assert.match(runtime, /REJECTED BY SIMULATION\/RISK GATE/, "risk rejection must be visible to the operator");
assert.match(runtime, /SIMULATED FILL/, "accepted results must be explicitly labeled simulated");
assert.match(runtime, /quantityValue <= 0 \|\| quantityValue > MAX_QUANTITY/, "client must bound quantity before submission");
assert.match(runtime, /submitInFlight/, "duplicate concurrent submissions must be prevented");
assert.doesNotMatch(runtime, /api[_-]?key|bearer\s|wallet_address|private[_-]?key|broker_url/i, "paper runtime must not implement account or credential connectivity");
assert.match(styles, /\.paperOrderConsole/, "paper order console must have dedicated visual treatment");
assert.match(styles, /@media\(max-width:620px\)/, "paper console must remain usable on narrow tablet/mobile layouts");
assert.match(styles, /focus-visible/, "paper controls must retain visible keyboard focus");

console.log("frontend paper-order safety and functionality contracts: ok");
