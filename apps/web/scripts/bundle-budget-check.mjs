import assert from "node:assert/strict";
import { readdir, stat } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist", import.meta.url));
const MAX_JS_BYTES = 450 * 1024;
const MAX_CSS_BYTES = 200 * 1024;
const MAX_TOTAL_ASSET_BYTES = 700 * 1024;

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
let total = 0;
for (const file of files) {
  const info = await stat(file);
  total += info.size;
  if (extname(file) === ".js") assert.ok(info.size <= MAX_JS_BYTES, `${file} exceeds ${MAX_JS_BYTES} bytes`);
  if (extname(file) === ".css") assert.ok(info.size <= MAX_CSS_BYTES, `${file} exceeds ${MAX_CSS_BYTES} bytes`);
}
assert.ok(total <= MAX_TOTAL_ASSET_BYTES, `dist assets total ${total} bytes exceeds ${MAX_TOTAL_ASSET_BYTES}`);
console.log(`frontend bundle budget: ok (${total} bytes across ${files.length} files)`);
