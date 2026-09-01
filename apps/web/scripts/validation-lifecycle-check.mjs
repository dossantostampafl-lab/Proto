import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../src/ValidationPanel.tsx", import.meta.url), "utf8");

assert.match(source, /const mounted = useRef\(true\)/, "validation panel must track mount state");
assert.match(source, /mounted\.current = false/, "unmount cleanup must invalidate the component before aborting requests");
assert.match(source, /activeRequest\.current\?\.abort\(\)/, "unmount cleanup must abort active validation requests");
assert.match(source, /activeRequest\.current = null/, "unmount cleanup must release the active request reference");
assert.match(source, /if \(!mounted\.current \|\| activeRequest\.current !== controller\) return/, "stale or unmounted requests must not publish errors");
assert.match(source, /mounted\.current && activeRequest\.current === controller/, "validation results must only publish from the current mounted request");
assert.match(source, /if \(mounted\.current\) setBusy\(false\)/, "request finalization must not update state after unmount");

console.log("validation lifecycle contracts: ok");
