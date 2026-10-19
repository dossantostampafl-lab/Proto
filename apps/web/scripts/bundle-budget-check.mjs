import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import { basename, extname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist", import.meta.url));
// Everything the browser loads up front stays small (the photo fallback included). The live 3D
// trading floor is loaded on demand, only on the Floor view and never preloaded, in three JS chunks
// (Floor3D: scene code; Floor3DThree: three.js; Floor3DAssets: glTF loader) plus its people and
// textures, fetched from dist/floor3d/ by the scene itself. The stylized building view ("Prédio") is a
// fourth on-demand chunk, FloorBuilding, loaded only when that view is selected; it reuses Floor3DThree.
const MAX_JS_BYTES = 450 * 1024;
const MAX_CSS_BYTES = 200 * 1024;
const MAX_INITIAL_BYTES = 700 * 1024;
const FLOOR3D_JS = { "Floor3D": 120 * 1024, "Floor3DThree": 600 * 1024, "Floor3DAssets": 120 * 1024 };
const MAX_FLOOR3D_JS_BYTES = 800 * 1024;
const MAX_FLOOR3D_ASSET_BYTES = 3 * 1024 * 1024;
const MAX_BUILDING_JS_BYTES = 150 * 1024;
const MAX_TOTAL_BYTES = MAX_INITIAL_BYTES + MAX_FLOOR3D_JS_BYTES + MAX_FLOOR3D_ASSET_BYTES + MAX_BUILDING_JS_BYTES;

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
const chunkOf = file => /^(Floor3D|Floor3DThree|Floor3DAssets)-[^.]*\.js$/.exec(basename(file))?.[1] ?? null;
const isFloorAsset = file => relative(DIST, file).split(sep)[0] === "floor3d";
const isBuilding = file => /^FloorBuilding-[^.]*\.js$/.test(basename(file));
let total = 0, initial = 0, floor3dJs = 0, floor3dAssets = 0, buildingJs = 0;
const seen = new Set();
for (const file of files) {
  const info = await stat(file);
  total += info.size;
  const chunk = chunkOf(file);
  if (chunk) {
    seen.add(chunk);
    floor3dJs += info.size;
    assert.ok(info.size <= FLOOR3D_JS[chunk], `${file} exceeds ${FLOOR3D_JS[chunk]} bytes`);
    assert.doesNotMatch(index, new RegExp(`${chunk}-`), `${chunk} must not be preloaded by index.html`);
  } else if (isBuilding(file)) {
    buildingJs += info.size;
    assert.ok(info.size <= MAX_BUILDING_JS_BYTES, `${file} exceeds ${MAX_BUILDING_JS_BYTES} bytes`);
    assert.doesNotMatch(index, /FloorBuilding-/, "the building view must not be preloaded by index.html");
    assert.doesNotMatch(await readFile(file, "utf8"), /WebGLRenderer\.prototype|class WebGLRenderer/, "the building view reuses the Floor3DThree chunk, never a second copy of three.js");
  } else if (isFloorAsset(file)) {
    floor3dAssets += info.size;
    assert.ok(info.size <= 1024 * 1024, `${file}: a single 3D asset stays under 1 MB`);
  } else {
    initial += info.size;
    if (extname(file) === ".js") {
      assert.ok(info.size <= MAX_JS_BYTES, `${file} exceeds ${MAX_JS_BYTES} bytes`);
      assert.doesNotMatch(await readFile(file, "utf8"), /WebGLRenderer|GLTFLoader/, `${basename(file)}: three.js must stay in the on-demand 3D chunks`);
    }
    if (extname(file) === ".css") assert.ok(info.size <= MAX_CSS_BYTES, `${file} exceeds ${MAX_CSS_BYTES} bytes`);
  }
}
assert.ok(buildingJs > 0, "the building view must emit its own FloorBuilding chunk");
for (const chunk of Object.keys(FLOOR3D_JS)) assert.ok(seen.has(chunk), `the 3D floor must emit its ${chunk} chunk`);
assert.ok(files.some(f => f.endsWith(`${sep}floor3d${sep}ASSETS.md`)), "3D assets must ship with their sources and licences (floor3d/ASSETS.md)");
assert.ok(initial <= MAX_INITIAL_BYTES, `assets loaded up front total ${initial} bytes, over ${MAX_INITIAL_BYTES}`);
assert.ok(floor3dJs <= MAX_FLOOR3D_JS_BYTES, `3D floor JS totals ${floor3dJs} bytes, over ${MAX_FLOOR3D_JS_BYTES}`);
assert.ok(floor3dAssets <= MAX_FLOOR3D_ASSET_BYTES, `dist/floor3d totals ${floor3dAssets} bytes, over ${MAX_FLOOR3D_ASSET_BYTES}`);
assert.ok(total <= MAX_TOTAL_BYTES, `dist totals ${total} bytes, over ${MAX_TOTAL_BYTES}`);
console.log(`frontend bundle budget: ok (${initial} bytes up front, 3D floor JS ${floor3dJs} bytes, building JS ${buildingJs} bytes, 3D assets ${floor3dAssets} bytes, ${total} bytes across ${files.length} files)`);
