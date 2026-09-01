import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");

assert.match(runtime, /const observed = new Set<HTMLElement>\(\)/, "navigation must track observed workspace nodes");
assert.match(runtime, /function bindSectionNavigation/, "section navigation runtime must remain explicit");
assert.match(runtime, /const observeSection = \(section: HTMLElement\)/, "navigation must support observing sections added after startup");
assert.match(runtime, /node\.matches\("\[data-section\]"\)/, "dynamic workspace insertion must be detected");
assert.match(runtime, /node\.querySelectorAll<HTMLElement>\("\[data-section\]"\)\.forEach\(observeSection\)/, "nested dynamically inserted workspaces must be observed");
assert.match(runtime, /if \(!surface\.dataset\.section\) surface\.dataset\.section = "SYSTEM"/, "operational surface must receive SYSTEM workspace identity when needed");
assert.match(runtime, /observeSection\(surface\)/, "late operational telemetry surface must join the active IntersectionObserver");
assert.match(runtime, /button\.addEventListener\("click", handler\)/, "top command navigation must have functional click handlers");
assert.match(runtime, /button\.removeEventListener\("click", handler\)/, "navigation cleanup must remove topbar handlers");
assert.match(runtime, /sectionWatcher\.disconnect\(\)/, "dynamic workspace observer must be cleaned up");

console.log("frontend dynamic-navigation contracts: ok");
