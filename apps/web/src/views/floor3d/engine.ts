// The 3D trading floor, built to read like a photograph of a trading room: physically based
// materials with CC0 textures, ACES tone mapping, an image-based fill from a PMREM room environment,
// one warm key light with soft shadow maps, a ceiling with light panels, glass walls, and a camera at
// eye height with a ~33 mm lens. Pipeline desks stand on an arc in chain order (TradingView →
// Fusion → Reviewer → Risk → OMS → Reconciliation, and the XP / B3 lane) facing a curved video wall;
// the roster's trader agents sit at a bench in front. Every screen is a live canvas texture. An
// operator carries work to the next desk only when a live floor event arrives; an eliminated agent
// stands up and walks out and its replacement walks in; floor staff walk between desks so the room
// is alive. Built for tablets: capped pixel ratio, smaller shadow map on touch, ~30 fps on touch, a
// paused loop while hidden, and everything disposed on unmount.
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { Shared } from "../../main";
import type { XpLaneState } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId, type Tone, TONE_COLOR } from "../floorModel";
import type { FloorAssets, Pbr } from "./Floor3DAssets";
import { Human, type Look } from "./people";
import type { Roster, RosterAgent } from "./roster";
import * as screens from "./screens";

export type DeskId = StationId | "xp" | "bench";
export type FloorData = {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  busy: Record<StationId, boolean>;
  xp: XpLaneState | null;
  selected: StationId | null;
};

/** Pipeline desks on an arc around C, in chain order from left to right. */
const C = new THREE.Vector3(0, 0, 1.0);
const R = 6.4, AISLE = 4.1;
const ARC: (StationId | "xp")[] = ["tradingview", "fusion", "reviewer", "risk", "oms", "reconciliation", "xp"];
const ANGLE = Object.fromEntries(ARC.map((id, i) => [id, THREE.MathUtils.degToRad(-75 + i * 25)])) as Record<StationId | "xp", number>;
const PREV: Partial<Record<StationId, StationId>> = { fusion: "tradingview", reviewer: "fusion", risk: "reviewer", oms: "risk", reconciliation: "oms" };
/** A desk opens the drawer of its station; the XP lane lives in the OMS drawer, the agents' bench in Fusion's. */
export const drawerOf = (id: DeskId): StationId => (id === "xp" ? "oms" : id === "bench" ? "fusion" : id);

/** The roster agents' bench: two pods of three seats between the camera and the arc. */
const BENCH_Z = 2.75, SEAT_Z = 3.5;
/** The sitting clips put the pelvis behind the root: the root stands this far ahead of the chair. */
const SIT_FORWARD = 0.3;
const SEATS_X = [-4.6, -3.2, -1.8, 1.8, 3.2, 4.6];
const DOOR = new THREE.Vector3(10.9, 0, 5.0), DOOR_IN = new THREE.Vector3(9.3, 0, 5.0), LANE_Z = 4.7;

const ROOM = { halfW: 10.5, back: -10.2, front: 12, ceiling: 3.6 };
const WALL_R = 10.6;
const PANELS: { key: "btc" | "eth" | "sol" | "equity"; width: number }[] = [
  { key: "btc", width: 3.4 }, { key: "eth", width: 1.95 }, { key: "sol", width: 1.95 }, { key: "equity", width: 1.95 },
];
const PANEL_GAP = 0.06, PANEL_BOTTOM = 1.62, PANEL_H = 1.5, STRIP_H = 0.22, PX_PER_M = 260;

const OPERATORS: Record<StationId | "xp", Look> = {
  tradingview: { sex: "female", light: true, hair: "Hair_Long", hairColor: "#3a2618", shirt: "#2f3b52", trousers: "#1f2633", shoes: "#141414" },
  fusion: { sex: "male", light: false, hair: "Hair_Buzzed", hairColor: "#141210", shirt: "#b9cde4", trousers: "#2b2d31", shoes: "#18120e" },
  reviewer: { sex: "male", light: true, hair: "Hair_SimpleParted", beard: true, hairColor: "#5a3b22", shirt: "#33363b", trousers: "#1c1e22", shoes: "#111111" },
  risk: { sex: "female", light: false, hair: "Hair_Buns", hairColor: "#17110d", shirt: "#8fa6c2", trousers: "#2a2f3a", shoes: "#141414" },
  oms: { sex: "male", light: true, hair: "Hair_SimpleParted", hairColor: "#2b1d14", shirt: "#e9ecef", trousers: "#2d3442", shoes: "#2a1a10" },
  reconciliation: { sex: "female", light: true, hair: "Hair_BuzzedFemale", hairColor: "#7a5636", shirt: "#6b7280", trousers: "#1d1f24", shoes: "#111111" },
  xp: { sex: "male", light: false, hair: "Hair_SimpleParted", beard: true, hairColor: "#0f0d0c", shirt: "#c8b9a0", trousers: "#2a2622", shoes: "#1a1410" },
};
const STAFF: Look[] = [
  { sex: "male", light: true, hair: "Hair_Buzzed", hairColor: "#3a2a1c", shirt: "#2b3a55", trousers: "#8a7a5c", shoes: "#2a1a10" },
  { sex: "female", light: false, hair: "Hair_Long", hairColor: "#120e0b", shirt: "#5b3f3a", trousers: "#1f2633", shoes: "#141414" },
  { sex: "male", light: false, hair: "Hair_SimpleParted", hairColor: "#141210", shirt: "#e9ecef", trousers: "#3b3f46", shoes: "#141414" },
];
const AGENT_SHIRTS = ["#c9ced4", "#8fa6c2", "#2f3b52", "#3a3d42", "#6d7782", "#a89c88", "#4a5a4e", "#5b3f3a"];
const AGENT_TROUSERS = ["#1f2633", "#2b2d31", "#3a3f47", "#1c1e22"];

/** A point on the arc: angle a (0 = straight ahead, towards the wall), radius r. */
const onArc = (a: number, r: number) => new THREE.Vector3(C.x + Math.sin(a) * r, 0, C.z - Math.cos(a) * r);
const angleOf = (p: THREE.Vector3) => Math.atan2(p.x - C.x, -(p.z - C.z));
/** The heading that faces direction (dx, dz): the characters face +z. */
const facing = (dx: number, dz: number) => Math.atan2(dx, dz);
const hash = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return h >>> 0; };

type Face = { c: OffscreenCanvas; g: screens.Ctx; tex: THREE.CanvasTexture<OffscreenCanvas>; w: number; h: number };
type Desk = {
  id: StationId | "xp";
  group: THREE.Group;
  operator: Human;
  main: Face;
  log: Face | null;
  sign: Face;
  beacon: THREE.MeshStandardMaterial;
  glow: THREE.MeshBasicMaterial;
  flash: number;
  sig: string;
};
type Seat = {
  index: number;
  pos: THREE.Vector3;
  slot: string | null;
  agent: RosterAgent | null;
  person: Human | null;
  tag: Face | null;
  chart: Face;
  card: Face;
  workUntil: number;
  sig: string;
};

function face(w: number, h: number): Face {
  const { c, g } = screens.canvas(w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.generateMipmaps = false;
  tex.minFilter = THREE.LinearFilter;
  return { c, g, tex, w, h };
}

/** Deterministic pseudo-random numbers: the room moves the same way on every load. */
function rng(seed: number) {
  return () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
}

/** A tiled PBR material from one of the CC0 texture sets. */
function pbrMaterial(set: Pbr, repeat: [number, number], opts: THREE.MeshStandardMaterialParameters = {}) {
  const t = (tex: THREE.Texture | null) => {
    if (!tex) return null;
    const c = tex.clone();
    c.repeat.set(repeat[0], repeat[1]);
    c.needsUpdate = true;
    return c;
  };
  return new THREE.MeshStandardMaterial({ map: t(set.color), normalMap: t(set.normal), roughnessMap: t(set.roughness), ...opts });
}

export class FloorEngine {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.1, 80);
  private readonly desks = new Map<StationId | "xp", Desk>();
  private readonly seats: Seat[] = [];
  private readonly staff: Human[] = [];
  private readonly leaving: Human[] = [];
  private readonly hits: THREE.Mesh[] = [];
  private readonly wall: Record<"btc" | "eth" | "sol" | "equity", Face>;
  private readonly ticker: Face;
  private readonly tickerTextures: THREE.Texture[] = [];
  private readonly raycaster = new THREE.Raycaster();
  private readonly random = rng(11);
  private readonly mats: Record<"desk" | "metal" | "dark" | "fabric" | "plastic" | "screenOff", THREE.MeshStandardMaterial>;
  private readonly blob: THREE.CanvasTexture<OffscreenCanvas>;
  private data: FloorData | null = null;
  private roster: Roster | null = null;
  private rosterSeen = false;
  private seen: Set<string> | null = null;
  private wallDirty = true;
  private lastWall = -10;
  private running = false;
  private disposed = false;
  private last = performance.now();
  private lastFrame = 0;
  private t = 0;
  private scroll = 0;
  private readonly frameMs: number;
  // camera: an eye-height orbit around a target, slow drift until the user drags
  private yaw = 0;
  private eye = 1.62;
  private distance = 12.6;
  private fit = 1;
  private userAt = -1e9;
  private readonly target = new THREE.Vector3(0, 1.05, -2.6);
  onFirstFrame: (() => void) | null = null;

  constructor(canvas: HTMLCanvasElement, private readonly assets: FloorAssets, private readonly reduced: boolean, coarse: boolean) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    // Tablets: never render more than 1.5 device pixels per CSS pixel.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.92;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.frameMs = coarse ? 1000 / 32 : 0; // touch devices: ~30 fps is plenty and saves battery

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.55;
    pmrem.dispose();
    this.scene.background = new THREE.Color("#1a1918");

    const blob = face(128, 128);
    screens.drawBlob(blob.g, 128, 128);
    blob.tex.colorSpace = THREE.NoColorSpace;
    this.blob = blob.tex;

    const a = assets;
    this.mats = {
      desk: pbrMaterial(a.desk, [1, 0.4], { roughness: 1, color: "#d9cdbd" }),
      metal: pbrMaterial(a.metal, [1, 1], { metalness: 1, roughness: 1, color: "#b9bcc0" }),
      dark: new THREE.MeshStandardMaterial({ color: "#1d1f22", roughness: 0.55, metalness: 0.3 }),
      fabric: pbrMaterial(a.fabric, [2, 2], { color: "#3a3d42", roughness: 0.95 }),
      plastic: new THREE.MeshStandardMaterial({ color: "#121315", roughness: 0.35, metalness: 0.1 }),
      screenOff: new THREE.MeshStandardMaterial({ color: "#050506", roughness: 0.15 }),
    };

    this.buildRoom(coarse);
    this.wall = this.buildWall();
    this.ticker = this.buildTicker();
    ARC.forEach((id, i) => this.desks.set(id, this.buildDesk(id, i)));
    this.buildBench();
    this.buildStaff();
    this.placeCamera(0);
    if (document.fonts) void document.fonts.ready.then(() => { this.wallDirty = true; this.desks.forEach(d => { d.sig = ""; }); this.seats.forEach(s => { s.sig = ""; }); });
    this.setRunning(true);
  }

  // ---------- building ----------

  private mesh(geo: THREE.BufferGeometry, mat: THREE.Material, parent: THREE.Object3D, pos: [number, number, number], shadow = true) {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(...pos);
    m.castShadow = shadow;
    m.receiveShadow = true;
    parent.add(m);
    return m;
  }

  private contact(parent: THREE.Object3D, x: number, z: number, w: number, d: number, opacity: number) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ map: this.blob, color: "#000000", transparent: true, opacity, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    m.position.set(x, 0.004, z);
    m.renderOrder = -1;
    parent.add(m);
    return m;
  }

  private buildRoom(coarse: boolean) {
    const { scene, assets } = this;
    const W = ROOM.halfW * 2, D = ROOM.front - ROOM.back;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D), pbrMaterial(assets.floor, [W / 2.2, D / 2.2], { roughness: 1, envMapIntensity: 0.8 }));
    floor.rotation.x = -Math.PI / 2; floor.position.z = (ROOM.front + ROOM.back) / 2; floor.receiveShadow = true;
    scene.add(floor);

    const wallMat = pbrMaterial(assets.wall, [6, 1.2], { roughness: 1, color: "#b8b3ab" });
    const back = new THREE.Mesh(new THREE.PlaneGeometry(W, ROOM.ceiling), wallMat);
    back.position.set(0, ROOM.ceiling / 2, ROOM.back); back.receiveShadow = true;
    const front = new THREE.Mesh(new THREE.PlaneGeometry(W, ROOM.ceiling), wallMat);
    front.position.set(0, ROOM.ceiling / 2, ROOM.front); front.rotation.y = Math.PI;
    scene.add(back, front);
    // Dark acoustic panel behind the video wall.
    this.mesh(new THREE.BoxGeometry(11.5, 2.4, 0.06), this.mats.dark, scene, [0, 2.2, ROOM.back + 0.04], false);

    // Ceiling with recessed light panels.
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(W, D), new THREE.MeshStandardMaterial({ color: "#d9d6d0", roughness: 0.9 }));
    ceiling.rotation.x = Math.PI / 2; ceiling.position.set(0, ROOM.ceiling, floor.position.z);
    scene.add(ceiling);
    const panel = new THREE.MeshStandardMaterial({ color: "#000000", emissive: new THREE.Color("#fff4e2"), emissiveIntensity: 2.2 });
    const panelGeo = new THREE.BoxGeometry(1.2, 0.02, 0.3);
    for (let x = -7.5; x <= 7.5; x += 3) for (let z = -8; z <= 10; z += 3) this.mesh(panelGeo, panel, scene, [x, ROOM.ceiling - 0.012, z], false);

    // Glass walls with a dusk city behind them: reflective, barely tinted (no transmission pass).
    const city = face(2048, 640);
    screens.drawCity(city.g, 2048, 640);
    const glass = new THREE.MeshStandardMaterial({ color: "#aab6c0", roughness: 0.04, metalness: 1, transparent: true, opacity: 0.16, depthWrite: false, envMapIntensity: 1.2 });
    const mullion = new THREE.MeshStandardMaterial({ color: "#2a2c30", roughness: 0.4, metalness: 0.8 });
    for (const side of [-1, 1]) {
      const sky = new THREE.Mesh(new THREE.PlaneGeometry(40, 13), new THREE.MeshBasicMaterial({ map: city.tex, toneMapped: true }));
      sky.position.set(side * 18, 3.2, 0); sky.rotation.y = -side * Math.PI / 2;
      const pane = new THREE.Mesh(new THREE.PlaneGeometry(D, ROOM.ceiling), glass);
      pane.position.set(side * ROOM.halfW, ROOM.ceiling / 2, floor.position.z); pane.rotation.y = -side * Math.PI / 2;
      scene.add(sky, pane);
      for (let z = ROOM.back; z <= ROOM.front; z += 1.8) this.mesh(new THREE.BoxGeometry(0.08, ROOM.ceiling, 0.08), mullion, scene, [side * ROOM.halfW, ROOM.ceiling / 2, z], false);
      this.mesh(new THREE.BoxGeometry(0.12, 0.12, D), mullion, scene, [side * ROOM.halfW, 0.06, floor.position.z], false);
      for (const z of [-7.8, -2.4, 6.5]) this.plant(side * (ROOM.halfW - 0.7), z);
    }

    // Light: image-based fill from the room environment, a soft sky fill and one warm key with shadows.
    scene.add(new THREE.HemisphereLight("#fff4e6", "#4a3c30", 0.45));
    const key = new THREE.DirectionalLight("#ffe7c7", 2.4);
    key.position.set(-6, 12, 7);
    key.target.position.set(0, 0, -2);
    key.castShadow = true;
    key.shadow.mapSize.set(coarse ? 1024 : 2048, coarse ? 1024 : 2048);
    const sc = key.shadow.camera;
    sc.left = -12; sc.right = 12; sc.top = 12; sc.bottom = -12; sc.near = 2; sc.far = 40;
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.025;
    key.shadow.radius = 4;
    scene.add(key, key.target);
    const fill = new THREE.DirectionalLight("#cfdcff", 0.35);
    fill.position.set(8, 6, -6);
    scene.add(fill);
  }

  private plant(x: number, z: number) {
    const pot = this.mesh(new THREE.CylinderGeometry(0.28, 0.22, 0.6, 24), new THREE.MeshStandardMaterial({ color: "#d8d2c8", roughness: 0.55 }), this.scene, [x, 0.3, z]);
    pot.castShadow = true;
    const leaf = new THREE.MeshStandardMaterial({ color: "#3f6b45", roughness: 0.7 });
    const geo = new THREE.IcosahedronGeometry(1, 1);
    for (let i = 0; i < 5; i++) {
      const s = 0.25 + this.random() * 0.18;
      const m = this.mesh(geo, leaf, this.scene, [x + (this.random() - 0.5) * 0.45, 0.85 + i * 0.22, z + (this.random() - 0.5) * 0.45]);
      m.scale.set(s, s * 1.5, s);
      m.rotation.set(this.random() * 3, this.random() * 3, 0);
    }
  }

  private buildWall() {
    const total = PANELS.reduce((a, p) => a + p.width, 0) + PANEL_GAP * (PANELS.length - 1);
    let s = -total / 2;
    const out = {} as Record<"btc" | "eth" | "sol" | "equity", Face>;
    for (const p of PANELS) {
      const phi = (s + p.width / 2) / WALL_R;
      const pos = new THREE.Vector3(C.x + Math.sin(phi) * WALL_R, 0, C.z - Math.cos(phi) * WALL_R);
      const f = face(Math.round(p.width * PX_PER_M), Math.round(PANEL_H * PX_PER_M));
      out[p.key] = f;
      const panel = new THREE.Mesh(new THREE.PlaneGeometry(p.width, PANEL_H), new THREE.MeshStandardMaterial({ color: "#000000", emissive: "#ffffff", emissiveMap: f.tex, emissiveIntensity: 1.25, roughness: 0.2 }));
      panel.position.set(pos.x, PANEL_BOTTOM + PANEL_H / 2, pos.z);
      panel.rotation.y = -phi;
      const frame = this.mesh(new THREE.BoxGeometry(p.width + 0.03, PANEL_H + 0.03, 0.06), this.mats.plastic, this.scene, [0, 0, 0], false);
      frame.position.copy(panel.position).add(new THREE.Vector3(Math.sin(phi), 0, -Math.cos(phi)).multiplyScalar(0.035));
      frame.rotation.y = -phi;
      this.scene.add(panel);
      s += p.width + PANEL_GAP;
    }
    return out;
  }

  /** The LED ticker: one texture scrolled across strips under the wall panels. */
  private buildTicker() {
    const total = PANELS.reduce((a, p) => a + p.width, 0) + PANEL_GAP * (PANELS.length - 1);
    const loop = total * 3.2;
    const f = face(4096, 64);
    f.tex.wrapS = THREE.RepeatWrapping;
    let s = -total / 2;
    for (const p of PANELS) {
      const w = p.width + PANEL_GAP;
      const phi = (s + p.width / 2) / WALL_R;
      const pos = new THREE.Vector3(C.x + Math.sin(phi) * WALL_R, 0, C.z - Math.cos(phi) * WALL_R);
      const tex = f.tex.clone();
      tex.repeat.set(w / loop, 1);
      tex.userData.base = (s + total / 2) / loop;
      tex.offset.set(tex.userData.base as number, 0);
      this.tickerTextures.push(tex);
      const strip = new THREE.Mesh(new THREE.PlaneGeometry(w, STRIP_H), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
      strip.position.set(pos.x, PANEL_BOTTOM - 0.08 - STRIP_H / 2, pos.z + 0.01);
      strip.rotation.y = -phi;
      this.scene.add(strip);
      s += p.width + PANEL_GAP;
    }
    return f;
  }

  /** An office chair: fabric seat and back, black plastic, brushed-metal star base. */
  private chair(parent: THREE.Object3D, x: number, z: number, rotY: number) {
    const g = new THREE.Group();
    g.position.set(x, 0, z); g.rotation.y = rotY;
    parent.add(g);
    const { fabric, plastic, metal } = this.mats;
    this.mesh(new RoundedBoxGeometry(0.5, 0.08, 0.48, 2, 0.03), fabric, g, [0, 0.47, 0]);
    const back = this.mesh(new RoundedBoxGeometry(0.46, 0.58, 0.06, 2, 0.03), fabric, g, [0, 0.86, 0.26]);
    back.rotation.x = 0.12;
    this.mesh(new THREE.BoxGeometry(0.05, 0.3, 0.04), plastic, g, [0, 0.6, 0.27]);
    this.mesh(new THREE.CylinderGeometry(0.025, 0.03, 0.36, 12), metal, g, [0, 0.26, 0]);
    for (let i = 0; i < 5; i++) {
      const a = (i * Math.PI * 2) / 5;
      const leg = this.mesh(new THREE.BoxGeometry(0.035, 0.03, 0.3), metal, g, [Math.sin(a) * 0.14, 0.06, Math.cos(a) * 0.14]);
      leg.rotation.y = a;
      this.mesh(new THREE.SphereGeometry(0.025, 8, 6), plastic, g, [Math.sin(a) * 0.28, 0.025, Math.cos(a) * 0.28], false);
    }
    for (const side of [-1, 1]) this.mesh(new THREE.BoxGeometry(0.04, 0.03, 0.26), plastic, g, [side * 0.27, 0.66, 0.02]);
    this.contact(g, 0, 0, 0.9, 0.9, 0.35);
    return g;
  }

  /** A monitor on a slim stand with a live, emissive screen. */
  private monitor(parent: THREE.Object3D, x: number, z: number, rotY: number, f: Face | null, w = 0.6, h = 0.35) {
    const g = new THREE.Group();
    g.position.set(x, 0, z); g.rotation.y = rotY;
    parent.add(g);
    const { plastic, metal } = this.mats;
    this.mesh(new RoundedBoxGeometry(w + 0.02, h + 0.02, 0.03, 2, 0.008), plastic, g, [0, 1.06, -0.016]);
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(w, h), f
      ? new THREE.MeshStandardMaterial({ color: "#000000", emissive: "#ffffff", emissiveMap: f.tex, emissiveIntensity: 1.1, roughness: 0.18 })
      : this.mats.screenOff);
    screen.position.set(0, 1.06, 0.0);
    g.add(screen);
    this.mesh(new THREE.BoxGeometry(0.04, 0.26, 0.03), metal, g, [0, 0.86, -0.05]);
    this.mesh(new THREE.BoxGeometry(0.22, 0.012, 0.16), metal, g, [0, 0.756, -0.04]);
    return g;
  }

  /** A desk: wood-veneer top, brushed-metal frame, keyboard and mouse. Local -z is the far side. */
  private deskBody(g: THREE.Group, width: number, depth: number) {
    const { desk, metal, dark, plastic } = this.mats;
    this.mesh(new RoundedBoxGeometry(width, 0.04, depth, 2, 0.012), desk, g, [0, 0.73, 0]);
    for (const side of [-1, 1]) {
      this.mesh(new THREE.BoxGeometry(0.05, 0.7, 0.05), metal, g, [side * (width / 2 - 0.08), 0.355, depth / 2 - 0.1]);
      this.mesh(new THREE.BoxGeometry(0.05, 0.7, 0.05), metal, g, [side * (width / 2 - 0.08), 0.355, -depth / 2 + 0.1]);
    }
    this.mesh(new THREE.BoxGeometry(width - 0.2, 0.36, 0.015), dark, g, [0, 0.5, -depth / 2 + 0.08]);
    this.contact(g, 0, 0, width + 0.9, depth + 0.9, 0.4);
    return plastic;
  }

  private buildDesk(id: StationId | "xp", index: number): Desk {
    const a = ANGLE[id];
    const group = new THREE.Group();
    group.position.copy(onArc(a, R));
    group.rotation.y = -a; // local -z points away from the arc centre, towards the monitors
    this.scene.add(group);
    const plastic = this.deskBody(group, 2.0, 0.85);
    this.mesh(new RoundedBoxGeometry(0.44, 0.02, 0.14, 1, 0.006), plastic, group, [0, 0.76, 0.16]);
    this.mesh(new RoundedBoxGeometry(0.06, 0.025, 0.1, 1, 0.01), plastic, group, [0.36, 0.76, 0.18]);

    // Three monitors: the live station headline in the middle, its journal and a dark spare (XP: one).
    const main = face(512, 300);
    const log = id === "xp" ? null : face(512, 300);
    this.monitor(group, 0, -0.2, 0, main);
    if (log) {
      this.monitor(group, -0.63, -0.14, 0.28, log);
      this.monitor(group, 0.63, -0.14, -0.28, null);
    }

    // Beacon: the red light of an alert, the tone colour otherwise.
    const beacon = new THREE.MeshStandardMaterial({ color: "#111111", emissive: "#5b6269", emissiveIntensity: 1, roughness: 0.3 });
    this.mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.1, 16), beacon, group, [0.92, 0.8, -0.3], false);
    const glow = new THREE.MeshBasicMaterial({ map: this.blob, color: "#000000", transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    const glowMesh = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 2.4), glow);
    glowMesh.rotation.x = -Math.PI / 2; glowMesh.position.set(0, 0.008, 0.25);
    group.add(glowMesh);

    // Hanging desk sign on two wires: name and tone in words.
    const sign = face(512, 128);
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(1.7, 0.425), new THREE.MeshBasicMaterial({ map: sign.tex, transparent: true, toneMapped: false }));
    plate.position.set(0, 2.45, -0.15);
    group.add(plate);
    for (const side of [-1, 1]) this.mesh(new THREE.CylinderGeometry(0.003, 0.003, ROOM.ceiling - 2.66, 4), this.mats.metal, group, [side * 0.7, (ROOM.ceiling + 2.66) / 2, -0.15], false);

    // Chair and operator.
    const seat = this.local(group, 0, 0.72);
    this.chair(group, 0, 0.72, 0);
    const operator = new Human(this.assets, OPERATORS[id], index * 1.7);
    this.scene.add(operator.group);
    operator.place(this.sitSpot(seat, a), facing(Math.sin(a), -Math.cos(a)), true);

    const hit = new THREE.Mesh(new THREE.BoxGeometry(2.3, 2.6, 2.2), new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.set(0, 1.3, 0.35);
    hit.userData.desk = id;
    group.add(hit);
    this.hits.push(hit);
    return { id, group, operator, main, log, sign, beacon, glow, flash: 0, sig: "" };
  }

  /** The roster bench: two pods of three seats, each with a market chart and the agent's card. */
  private buildBench() {
    for (const podX of [-3.2, 3.2]) {
      const g = new THREE.Group();
      g.position.set(podX, 0, BENCH_Z);
      this.scene.add(g);
      this.deskBody(g, 4.3, 0.8);
      const hit = new THREE.Mesh(new THREE.BoxGeometry(4.4, 2.2, 2), new THREE.MeshBasicMaterial({ visible: false }));
      hit.position.set(0, 1.1, 0.4);
      hit.userData.desk = "bench";
      g.add(hit);
      this.hits.push(hit);
    }
    SEATS_X.forEach((x, index) => {
      const chart = face(512, 300), card = face(512, 300);
      const g = new THREE.Group();
      g.position.set(x, 0, BENCH_Z);
      this.scene.add(g);
      this.monitor(g, -0.3, -0.18, 0.12, chart, 0.54, 0.32);
      this.monitor(g, 0.3, -0.18, -0.12, card, 0.54, 0.32);
      this.mesh(new RoundedBoxGeometry(0.42, 0.02, 0.13, 1, 0.006), this.mats.plastic, g, [0, 0.76, 0.16]);
      this.chair(g, 0, SEAT_Z - BENCH_Z, 0);
      this.seats.push({ index, pos: new THREE.Vector3(x, 0, SEAT_Z), slot: null, agent: null, person: null, tag: null, chart, card, workUntil: 0, sig: "" });
    });
  }

  private buildStaff() {
    STAFF.forEach((look, i) => {
      const p = new Human(this.assets, look, 3 + i * 2.3);
      const desk = this.desks.get(ARC[(i * 3 + 1) % ARC.length])!;
      p.place(this.local(desk.group, -0.95, 1.25), -ANGLE[desk.id] + Math.PI, false);
      p.plan({ kind: "wait", seconds: 1 + i * 2.5 });
      this.scene.add(p.group);
      this.staff.push(p);
    });
  }

  private local(group: THREE.Group, x: number, z: number) { group.updateMatrixWorld(true); return group.localToWorld(new THREE.Vector3(x, 0, z)).setY(0); }
  /** Where a seated person's root stands: just in front of the chair, towards the desk. */
  private sitSpot(seat: THREE.Vector3, a: number) { return seat.clone().add(new THREE.Vector3(Math.sin(a), 0, -Math.cos(a)).multiplyScalar(SIT_FORWARD)); }

  // ---------- behaviour ----------

  /** The walking route between two floor points: out to the aisle, along the arc, in to the goal. */
  private route(from: THREE.Vector3, to: THREE.Vector3) {
    const a0 = angleOf(from), a1 = angleOf(to);
    const pts = [onArc(a0, AISLE)];
    const steps = Math.max(1, Math.ceil(Math.abs(a1 - a0) / 0.18));
    for (let i = 1; i < steps; i++) pts.push(onArc(a0 + ((a1 - a0) * i) / steps, AISLE));
    pts.push(onArc(a1, AISLE), to.clone());
    return pts;
  }

  /** A live event at `to`: the operator of the desk before it in the chain carries the work over. */
  private handoff(from: StationId, to: StationId) {
    const src = this.desks.get(from)!, dst = this.desks.get(to)!;
    const p = src.operator;
    if (p.queued > 60) { dst.flash = 1; return; } // a burst of events: light the desk, skip the walk
    const seat = this.sitSpot(this.local(src.group, 0, 0.72), ANGLE[from]);
    const out = this.local(src.group, 0.75, 1.4);
    const visit = this.local(dst.group, 0.8, 1.15);
    p.plan({ kind: "sit", on: false });
    p.walk([out, ...this.route(out, visit)]);
    p.plan(
      { kind: "face", heading: facing(Math.sin(ANGLE[to]), -Math.cos(ANGLE[to])) }, { kind: "wait", seconds: this.reduced ? 2.5 : 1.8 },
      { kind: "call", fn: () => { dst.flash = 1; } },
    );
    p.walk([...this.route(visit, out), seat]);
    p.plan({ kind: "face", heading: facing(Math.sin(ANGLE[from]), -Math.cos(ANGLE[from])) }, { kind: "sit", on: true });
  }

  /** Floor staff: walk to a desk, look at its screens for a while, move on. Decoration only. */
  private wander(p: Human) {
    const id = ARC[Math.floor(this.random() * ARC.length)];
    const desk = this.desks.get(id)!;
    const spot = this.local(desk.group, -0.95 + (this.random() - 0.5) * 0.3, 1.25);
    p.walk(this.route(p.group.position.clone(), spot));
    p.plan({ kind: "face", heading: facing(Math.sin(ANGLE[id]), -Math.cos(ANGLE[id])) }, { kind: "wait", seconds: 3 + this.random() * 6 });
  }

  setData(data: FloorData) {
    const first = this.seen === null;
    if (first) this.seen = new Set();
    const seen = this.seen!;
    for (const a of data.actions) {
      const key = `${a.seq}:${a.station}`;
      if (seen.has(key)) continue;
      seen.add(key);
      // History (the first read, or anything not fresh from the stream) never makes anyone move.
      if (first || !data.busy[a.station]) continue;
      const code = /AG-\d+-[A-Z0-9]+/.exec(a.headline)?.[0] ?? null;
      const seat = code ? this.seats.find(s => s.agent?.id === code) : undefined;
      if (seat && a.event_type === "AGENT_ELIMINATED") this.walkOut(seat);
      else if (seat) seat.workUntil = this.t + 6;
      const prev = PREV[a.station];
      if (prev) this.handoff(prev, a.station);
      else this.desks.get(a.station)!.flash = 1;
    }
    if (seen.size > 600) this.seen = new Set(data.actions.map(a => `${a.seq}:${a.station}`));
    this.data = data;
    this.wallDirty = true;
  }

  // ---------- the roster ----------

  private agentLook(id: string): Look {
    const h = hash(id);
    const female = h % 2 === 1;
    return {
      sex: female ? "female" : "male",
      light: (h >> 1) % 2 === 0,
      hair: female ? ((h >> 2) % 2 ? "Hair_BuzzedFemale" : "Hair_Buns") : ((h >> 2) % 2 ? "Hair_SimpleParted" : "Hair_Buzzed"),
      beard: !female && (h >> 3) % 3 === 0,
      hairColor: ["#141210", "#3a2618", "#5a3b22", "#7a5636"][(h >> 4) % 4],
      shirt: AGENT_SHIRTS[(h >> 6) % AGENT_SHIRTS.length],
      trousers: AGENT_TROUSERS[(h >> 9) % AGENT_TROUSERS.length],
      shoes: (h >> 11) % 2 ? "#141414" : "#2a1a10",
    };
  }

  private seatHeading() { return Math.PI; } // facing -z, towards the desk and the wall

  private spawn(seat: Seat, agent: RosterAgent, walkIn: boolean) {
    const p = new Human(this.assets, this.agentLook(agent.id), seat.index * 1.3 + 0.4);
    this.scene.add(p.group);
    const sit = seat.pos.clone().add(new THREE.Vector3(0, 0, -SIT_FORWARD));
    if (walkIn && !this.reduced) {
      p.place(DOOR, facing(-1, 0), false);
      p.walk([DOOR_IN, new THREE.Vector3(seat.pos.x, 0, LANE_Z), sit]);
      p.plan({ kind: "face", heading: this.seatHeading() }, { kind: "sit", on: true });
    } else p.place(sit, this.seatHeading(), true);
    seat.person = p;
    seat.agent = agent;
    seat.slot = agent.slot;
    seat.sig = "";
    this.tagSeat(seat);
  }

  /** The agent stands up and walks out of the room; the seat is free for its replacement. */
  private walkOut(seat: Seat) {
    const p = seat.person;
    if (!p) return;
    p.setTag(null);
    seat.tag?.tex.dispose();
    seat.tag = null;
    seat.person = null;
    seat.agent = null;
    seat.sig = "";
    p.plan({ kind: "sit", on: false });
    p.walk([new THREE.Vector3(seat.pos.x, 0, LANE_Z), DOOR_IN, DOOR]);
    p.plan({ kind: "show", on: false }, { kind: "call", fn: () => { this.leaving.push(p); } });
    this.staffLeaving.add(p);
  }
  private readonly staffLeaving = new Set<Human>();

  private tagSeat(seat: Seat) {
    if (!seat.person || !seat.agent || !this.roster) return;
    if (!seat.tag) seat.tag = face(512, 128);
    screens.drawTag(seat.tag.g, seat.tag.w, seat.tag.h, seat.agent.id, seat.agent.equity, this.roster.capital.replace(" · NO REAL MONEY", ""));
    seat.tag.tex.needsUpdate = true;
    seat.person.setTag(seat.tag.tex);
  }

  /** Seats follow the roster: ACTIVE agents sit at their slot's seat, with a tag; nothing is invented. */
  setRoster(roster: Roster | null) {
    if (roster === null) {
      // Unavailable: no tags and no agents shown (a failed read is not an elimination).
      for (const seat of this.seats) {
        if (seat.person) { this.scene.remove(seat.person.group); seat.person.dispose(); }
        seat.tag?.tex.dispose();
        Object.assign(seat, { person: null, agent: null, tag: null, sig: "" });
      }
      this.roster = null;
      this.rosterSeen = false;
      return;
    }
    const firstLoad = !this.rosterSeen;
    this.rosterSeen = true;
    this.roster = roster;
    const active = roster.agents.filter(a => a.status === "ACTIVE");
    const activeIds = new Set(active.map(a => a.id));
    for (const seat of this.seats) if (seat.agent && !activeIds.has(seat.agent.id)) this.walkOut(seat);
    for (const agent of active) {
      let seat = this.seats.find(s => s.agent?.id === agent.id);
      if (seat) {
        if (seat.agent!.equity !== agent.equity) { seat.agent = agent; seat.sig = ""; this.tagSeat(seat); }
        continue;
      }
      seat = this.seats.find(s => s.slot === agent.slot && !s.person) ?? this.seats.find(s => s.slot === null && !s.person);
      if (!seat) continue; // more agents than seats: the drawer lists them all
      this.spawn(seat, agent, !firstLoad);
    }
    for (const seat of this.seats) this.tagSeat(seat);
  }

  // ---------- camera and input ----------

  resize(width: number, height: number) {
    if (width < 2 || height < 2) return;
    this.renderer.setSize(width, height, false);
    const aspect = width / height;
    this.camera.aspect = aspect;
    // Narrow screens step back so the arc stays in view.
    this.fit = Math.max(1, Math.pow(1.75 / aspect, 0.9));
    this.camera.updateProjectionMatrix();
  }

  private placeCamera(dt: number) {
    if (!this.reduced && this.t - this.userAt > 8) {
      const auto = Math.sin(this.t * 0.04) * 0.16;
      this.yaw += (auto - this.yaw) * Math.min(1, dt * 0.4);
    }
    const r = this.distance * this.fit;
    this.camera.position.set(this.target.x + Math.sin(this.yaw) * r, this.eye + (this.fit - 1) * 0.8, this.target.z + Math.cos(this.yaw) * r);
    this.camera.lookAt(this.target);
  }

  orbit(dx: number, dy: number) {
    this.userAt = this.t;
    this.yaw = THREE.MathUtils.clamp(this.yaw - dx * 0.004, -0.9, 0.9);
    this.eye = THREE.MathUtils.clamp(this.eye + dy * 0.01, 1.2, 3.3);
    if (!this.running) this.renderOnce();
  }

  zoom(factor: number) {
    this.userAt = this.t;
    this.distance = THREE.MathUtils.clamp(this.distance * factor, 5.5, 15.5);
    if (!this.running) this.renderOnce();
  }

  /** The desk under a point given in normalised device coordinates, if any. */
  pick(x: number, y: number): DeskId | null {
    this.raycaster.setFromCamera(new THREE.Vector2(x, y), this.camera);
    const hit = this.raycaster.intersectObjects(this.hits, false)[0];
    return hit ? (hit.object.userData.desk as DeskId) : null;
  }

  // ---------- frame ----------

  setRunning(on: boolean) {
    if (this.disposed || on === this.running) return;
    this.running = on;
    this.last = performance.now();
    this.renderer.setAnimationLoop(on ? () => this.frame() : null);
  }

  private renderOnce() { this.placeCamera(0); this.renderer.render(this.scene, this.camera); }

  private drawWall(d: FloorData) {
    const { btc, eth, sol, equity } = this.wall;
    screens.drawBtc(btc.g, btc.w, btc.h, d.market); btc.tex.needsUpdate = true;
    screens.drawSymbol(eth.g, eth.w, eth.h, d.market, "ETH"); eth.tex.needsUpdate = true;
    screens.drawSymbol(sol.g, sol.w, sol.h, d.market, "SOL"); sol.tex.needsUpdate = true;
    screens.drawEquity(equity.g, equity.w, equity.h, d.state); equity.tex.needsUpdate = true;
    screens.drawTicker(this.ticker.g, this.ticker.w, this.ticker.h, d.state, d.actions, d.market.connected);
    this.ticker.tex.needsUpdate = true;
    for (const t of this.tickerTextures) t.needsUpdate = true;
    for (const seat of this.seats) {
      const sym = (seat.agent?.symbol ?? "").replace(/USDT$|USD$/, "") as "BTC" | "ETH" | "SOL";
      screens.drawMonitorSymbol(seat.chart.g, seat.chart.w, seat.chart.h, d.market, sym === "ETH" || sym === "SOL" ? sym : "BTC", "#cfd4d8", Boolean(seat.agent));
      seat.chart.tex.needsUpdate = true;
    }
  }

  /** Redraws a desk's screens only when what they show has changed. */
  private drawDesk(desk: Desk, d: FloorData) {
    const selected = d.selected !== null && drawerOf(desk.id) === d.selected;
    if (desk.id === "xp") {
      const sig = `${d.xp}|${selected}`;
      if (sig === desk.sig) return;
      desk.sig = sig;
      screens.drawXpScreen(desk.main.g, desk.main.w, desk.main.h, d.xp);
      const tone: Tone | "idle" = d.xp === "halted" ? "alert" : d.xp === "unavailable" ? "warn" : d.xp === "live" ? "ok" : "idle";
      screens.drawPlate(desk.sign.g, desk.sign.w, desk.sign.h, "XP / B3", tone, d.xp === "live", selected);
    } else {
      const id = desk.id;
      const action = d.latest[id], busy = d.busy[id];
      const recent = d.actions.filter(a => a.station === id).slice(-4);
      const sig = `${action?.seq ?? "-"}|${busy}|${selected}|${recent.map(a => a.seq).join(",")}`;
      if (sig === desk.sig) return;
      desk.sig = sig;
      const info = STATIONS.find(s => s.id === id)!;
      screens.drawDeskScreen(desk.main.g, desk.main.w, desk.main.h, info.label, info.role, action, busy);
      if (desk.log) { screens.drawMonitorLog(desk.log.g, desk.log.w, desk.log.h, recent, busy); desk.log.tex.needsUpdate = true; }
      screens.drawPlate(desk.sign.g, desk.sign.w, desk.sign.h, info.label, action ? action.tone : "idle", busy, selected);
    }
    desk.main.tex.needsUpdate = true;
    desk.sign.tex.needsUpdate = true;
  }

  private drawSeat(seat: Seat) {
    const sig = `${seat.agent?.id ?? "-"}|${seat.agent?.equity ?? ""}|${this.roster?.capital ?? ""}`;
    if (sig === seat.sig) return;
    seat.sig = sig;
    screens.drawAgentScreen(seat.card.g, seat.card.w, seat.card.h, seat.agent?.id ?? null, seat.agent?.slotLabel ?? (this.roster ? "no agent" : "roster unavailable"),
      seat.agent?.equity ?? "—", this.roster ? "VIRTUAL CAPITAL · NO REAL MONEY" : "ROSTER UNAVAILABLE");
    seat.card.tex.needsUpdate = true;
  }

  private toneOf(desk: Desk, d: FloorData | null): { tone: Tone | "idle"; busy: boolean } {
    if (!d) return { tone: "idle", busy: false };
    if (desk.id === "xp") return { tone: d.xp === "halted" ? "alert" : d.xp === "unavailable" ? "warn" : d.xp === "live" ? "ok" : "idle", busy: false };
    const action = d.latest[desk.id];
    return { tone: action ? action.tone : "idle", busy: d.busy[desk.id] };
  }

  private frame() {
    if (this.disposed) return;
    const now = performance.now();
    if (this.frameMs && now - this.lastFrame < this.frameMs) return;
    this.lastFrame = now;
    const dt = Math.min((now - this.last) / 1000, 0.1);
    this.last = now;
    this.t += dt;
    const { t } = this;
    const d = this.data;

    if (d) {
      if (this.wallDirty && t - this.lastWall > 1) { this.wallDirty = false; this.lastWall = t; this.drawWall(d); }
      for (const desk of this.desks.values()) this.drawDesk(desk, d);
    }
    for (const seat of this.seats) this.drawSeat(seat);
    if (!this.reduced) {
      this.scroll += dt * 0.012;
      for (const tex of this.tickerTextures) tex.offset.x = (tex.userData.base as number) + this.scroll;
    }

    const pulse = this.reduced ? 1 : 0.65 + 0.35 * Math.sin(t * 5);
    const color = new THREE.Color();
    for (const desk of this.desks.values()) {
      const { tone, busy } = this.toneOf(desk, d);
      const alert = tone === "alert";
      desk.flash = Math.max(0, desk.flash - dt * 0.6);
      color.set(TONE_COLOR[tone]);
      desk.beacon.emissive.copy(color);
      desk.beacon.emissiveIntensity = alert ? 3.2 * pulse : busy ? 2 : 0.6;
      const strength = Math.min(1, (busy ? 0.5 : 0) + desk.flash + (alert ? 0.7 : 0));
      desk.glow.color.copy(color).multiplyScalar(strength * (alert ? 0.8 * pulse : 0.35));
      desk.operator.update(dt, busy, false, this.reduced);
    }
    for (const seat of this.seats) seat.person?.update(dt, seat.workUntil > t, false, this.reduced);
    for (const p of this.staff) {
      if (!p.busy) this.wander(p);
      p.update(dt, false, !p.busy, this.reduced);
    }
    for (const p of this.staffLeaving) p.update(dt, false, false, this.reduced);
    while (this.leaving.length) {
      const p = this.leaving.pop()!;
      this.staffLeaving.delete(p);
      this.scene.remove(p.group);
      p.dispose();
    }
    this.placeCamera(dt);
    this.renderer.render(this.scene, this.camera);
    if (this.onFirstFrame) { const cb = this.onFirstFrame; this.onFirstFrame = null; cb(); }
  }

  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    const textures = new Set<THREE.Texture>(this.tickerTextures);
    const people = [...this.desks.values()].map(d => d.operator).concat(this.staff, [...this.staffLeaving], this.seats.flatMap(s => (s.person ? [s.person] : [])));
    people.forEach(p => p.dispose());
    const collect = (root: THREE.Object3D) => root.traverse(o => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Sprite) {
        o.geometry.dispose();
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) {
          for (const v of Object.values(m)) if (v instanceof THREE.Texture) textures.add(v);
          m.dispose();
        }
      }
    });
    collect(this.scene);
    for (const g of [this.assets.male, this.assets.female]) collect(g.scene);
    for (const set of [this.assets.floor, this.assets.desk, this.assets.fabric, this.assets.metal, this.assets.wall]) [set.color, set.normal, set.roughness].forEach(x => x && textures.add(x));
    textures.add(this.assets.skins.male).add(this.assets.skins.female);
    this.seats.forEach(s => { s.tag?.tex.dispose(); });
    textures.forEach(x => x.dispose());
    this.scene.environment?.dispose();
    this.renderer.dispose();
  }
}
