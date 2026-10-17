import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist", import.meta.url));
// Everything the browser loads up front stays small (the photo fallback included). The live 3D
// trading floor (three.js) is the one chunk loaded on demand, only on the Floor view, never preloaded.
const MAX_JS_BYTES = 450 * 1024;
const MAX_FLOOR_3D_BYTES = 600 * 1024;
const MAX_CSS_BYTES = 200 * 1024;
const MAX_INITIAL_BYTES = 700 * 1024;
const MAX_TOTAL_ASSET_BYTES = MAX_INITIAL_BYTES + MAX_FLOOR_3D_BYTES;

async function filesRecursively(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await filesRecursively(path));
    else files.push(path);
  }
  return files;
}

const files = await filesRecursively(DIST);
const index = await readFile(join(DIST, "index.html"), "utf8");
const isFloor3d = file => /^Floor3D-.*\.js$/.test(basename(file));
let total = 0, initial = 0, floor3dBytes = 0;
for (const file of files) {
  const info = await stat(file);
  total += info.size;
  const floor3d = isFloor3d(file);
  if (floor3d) floor3dBytes += info.size;
  else initial += info.size;
  if (extname(file) === ".js") {
    const limit = floor3d ? MAX_FLOOR_3D_BYTES : MAX_JS_BYTES;
    assert.ok(info.size <= limit, `${file} exceeds ${limit} bytes`);
    if (!floor3d) assert.doesNotMatch(await readFile(file, "utf8"), /WebGLRenderer/, `${basename(file)}: three.js must stay in the on-demand Floor3D chunk`);
  }
  if (extname(file) === ".css") assert.ok(info.size <= MAX_CSS_BYTES, `${file} exceeds ${MAX_CSS_BYTES} bytes`);
}
assert.equal(files.filter(isFloor3d).length, 1, "the 3D floor must be emitted as exactly one on-demand chunk");
assert.doesNotMatch(index, /Floor3D-/, "the 3D floor chunk must not be preloaded by index.html");
assert.ok(initial <= MAX_INITIAL_BYTES, `assets outside the 3D floor total ${initial} bytes, over ${MAX_INITIAL_BYTES}`);
assert.ok(total <= MAX_TOTAL_ASSET_BYTES, `dist assets total ${total} bytes exceeds ${MAX_TOTAL_ASSET_BYTES}`);
console.log(`frontend bundle budget: ok (${initial} bytes up front, 3D floor chunk ${floor3dBytes} bytes, ${total} bytes across ${files.length} files)`);
