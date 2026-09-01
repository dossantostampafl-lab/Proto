import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtime = await readFile(new URL("../src/terminal-runtime.ts", import.meta.url), "utf8");
const terminal = await readFile(new URL("../src/terminal.tsx", import.meta.url), "utf8");

assert.match(runtime, /const observed = new Set<HTMLElement>\(\)/, "navigation must track observed workspace nodes");
assert.match(runtime, /function bindSectionNavigation/, "section navigation runtime must remain explicit");
assert.match(runtime, /const observeSection = \(section: HTMLElement\)/, "navigation must support observing sections added after startup");
assert.match(runtime, /node\.matches\("\[data-section\]"\)/, "dynamic workspace insertion must be detected");
assert.match(runtime, /node\.querySelectorAll<HTMLElement>\("\[data-section\]"\)\.forEach\(observeSection\)/, "nested dynamically inserted workspaces must be observed");
assert.match(runtime, /if \(!surface\.dataset\.section\) surface\.dataset\.section = "SYSTEM"/, "operational surface must receive SYSTEM workspace identity when needed");
assert.match(runtime, /observeSection\(surface\)/, "late operational telemetry surface must join the active IntersectionObserver");
assert.doesNotMatch(runtime, /topButtonHandlers/, "runtime must not attach a second click owner to React topbar controls");
assert.doesNotMatch(runtime, /button\.addEventListener\("click", handler\)/, "runtime must not duplicate React topbar click handlers");
assert.match(terminal, /onClick=\{\(\)=>go\(s\)\}/, "React topbar buttons must retain their functional navigation owner");
assert.match(runtime, /sectionWatcher\.disconnect\(\)/, "dynamic workspace observer must be cleaned up");

console.log("frontend single-owner dynamic-navigation contracts: ok");
