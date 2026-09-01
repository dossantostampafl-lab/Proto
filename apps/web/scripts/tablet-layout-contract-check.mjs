import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const runtimeStyles = await readFile(new URL("../src/terminal-runtime.css", import.meta.url), "utf8");

assert.match(runtimeStyles, /@media \(min-width:821px\) and \(max-width:1360px\)/, "landscape tablet override must remain explicit");
assert.match(runtimeStyles, /\.grid\{grid-template-columns:188px minmax\(0,1fr\) 218px/, "landscape tablet must preserve the three-column terminal workspace");
assert.match(runtimeStyles, /\.grid>aside:last-child\{grid-column:auto;grid-template-columns:1fr;grid-template-rows:142px 142px 142px\}/, "right analytics rail must not inherit the stacked <=1120px span override");
assert.match(runtimeStyles, /\.systemState\{grid-column:auto;justify-content:flex-start;padding-bottom:0;/, "system state must remain in the topbar row on landscape tablets");
assert.match(runtimeStyles, /\.automation\{grid-column:auto\}/, "automation must not inherit the stacked two-column lower-band span on landscape tablets");
assert.match(runtimeStyles, /\.panel\{content-visibility:visible;contain-intrinsic-size:auto\}/, "touch landscape layout must not blank panels through content virtualization");

console.log("frontend landscape-tablet layout contracts: ok");
