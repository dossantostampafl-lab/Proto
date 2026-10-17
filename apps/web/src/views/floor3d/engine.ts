// The 3D trading floor. Desks stand on an arc in pipeline order (TradingView → Fusion → Reviewer →
// Risk → OMS → Reconciliation, and the XP / B3 lane) facing a curved video wall. Every screen is a
// live canvas texture; an operator carries work to the next desk only when a live floor event
// arrives, and floor staff walk between desks so the room is alive. Built for tablets: capped pixel
// ratio, low-poly shared geometry, baked blob shadows instead of shadow maps, no post-processing,
// a paused loop while hidden, and everything disposed on unmount.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { Shared } from "../../main";
import type { XpLaneState } from "../deskData";
import { type FloorAction, type FloorState, STATIONS, type StationId, type Tone, TONE_COLOR } from "../floorModel";
import { chair, Kit, type Look, Person } from "./people";
import * as screens from "./screens";

export type DeskId = StationId | "xp";
export type FloorData = {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  busy: Record<StationId, boolean>;
  xp: XpLaneState | null;
  selected: StationId | null;
};

/** Desks on an arc around C, in pipeline order from left to right. */
const C = new THREE.Vector3(0, 0, 2.2);
const R = 7.2, AISLE = 4.7;
const ORDER: DeskId[] = ["tradingview", "fusion", "reviewer", "risk", "oms", "reconciliation", "xp"];
const ANGLE: Record<DeskId, number> = Object.fromEntries(ORDER.map((id, i) => [id, THREE.MathUtils.degToRad(-75 + i * 25)])) as Record<DeskId, number>;
/** Who hands work to whom: the authority chain. */
const PREV: Partial<Record<StationId, StationId>> = { fusion: "tradingview", reviewer: "fusion", risk: "reviewer", oms: "risk", reconciliation: "oms" };
/** Floor light paths between desks, [from, to, radius]. OMS feeds the XP lane around Reconciliation. */
const PATHS: [DeskId, DeskId, number][] = [
  ["tradingview", "fusion", 5.5], ["fusion", "reviewer", 5.5], ["reviewer", "risk", 5.5], ["risk", "oms", 5.5],
  ["oms", "reconciliation", 5.5], ["oms", "xp", 5.25],
];
const XP_DESK = { label: "XP / B3", role: "Demo account lane" };
/** A desk opens the drawer of its station; the XP lane lives in the OMS drawer. */
export const drawerOf = (id: DeskId): StationId => (id === "xp" ? "oms" : id);

const LOOKS: Record<DeskId, Look> = {
  tradingview: { skin: "#c49a82", hair: "#2a1a12", suit: "#2f3a4a", shirt: "#e8e4dc", long: true },
  fusion: { skin: "#d2a98f", hair: "#141414", suit: "#3a3530" },
  reviewer: { skin: "#8d5f45", hair: "#111111", suit: "#26313b", shirt: "#cfd8e0" },
  risk: { skin: "#a87a5f", hair: "#1b1410", suit: "#4a3b33", long: true },
  oms: { skin: "#d7b29a", hair: "#5a3a22", suit: "#2c3036" },
  reconciliation: { skin: "#b98c70", hair: "#4e321b", suit: "#38404a", shirt: "#f0ece4" },
  xp: { skin: "#6f4a36", hair: "#0e0e0e", suit: "#2d3a33" },
};
const STAFF: Look[] = [
  { skin: "#c09479", hair: "#1a1a1a", suit: "#55606b", shirt: "#f2efe8" },
  { skin: "#9c6b52", hair: "#2b1d14", suit: "#6b5a4c", long: true },
  { skin: "#e0bda4", hair: "#7a5636", suit: "#3c4652" },
];

/** A point on the arc: angle a (0 = straight ahead), radius r. */
const onArc = (a: number, r: number) => new THREE.Vector3(C.x + Math.sin(a) * r, 0, C.z - Math.cos(a) * r);
const angleOf = (p: THREE.Vector3) => Math.atan2(p.x - C.x, -(p.z - C.z));

type Face = { c: OffscreenCanvas; g: screens.Ctx; tex: THREE.CanvasTexture<OffscreenCanvas>; w: number; h: number };
type Desk = {
  id: DeskId;
  group: THREE.Group;
  operator: Person;
  main: Face;
  log: Face | null;
  plate: Face;
  sprite: THREE.Sprite;
  beacon: THREE.MeshBasicMaterial;
  led: THREE.MeshBasicMaterial;
  glow: THREE.MeshBasicMaterial;
  ring: THREE.Mesh;
  flash: number;
  sig: string;
};
type PathLine = { from: DeskId; to: DeskId; mat: THREE.MeshBasicMaterial };

const WALL_R = 11.2;
const PANELS: { key: "btc" | "eth" | "sol" | "equity"; width: number }[] = [
  { key: "btc", width: 4.2 }, { key: "eth", width: 2.4 }, { key: "sol", width: 2.4 }, { key: "equity", width: 2.4 },
];
const PANEL_GAP = 0.12, PANEL_BOTTOM = 2.05, PANEL_H = 2.3, STRIP_H = 0.34, PX_PER_M = 220;

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

export class FloorEngine {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(44, 16 / 9, 0.1, 90);
  private readonly kit: Kit;
  private readonly desks = new Map<DeskId, Desk>();
  private readonly paths: PathLine[] = [];
  private readonly staff: Person[] = [];
  private readonly hits: THREE.Mesh[] = [];
  private readonly wall: Record<"btc" | "eth" | "sol" | "equity", Face>;
  private readonly ticker: Face;
  private readonly tickerTextures: THREE.Texture[] = [];
  private readonly raycaster = new THREE.Raycaster();
  private readonly random = rng(11);
  private data: FloorData | null = null;
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
  // camera: orbit around a target, slow auto-orbit until the user drags
  private yaw = 0;
  private pitch = THREE.MathUtils.degToRad(23);
  private radius = 15.5;
  private fit = 1;
  private userAt = -1e9;
  private readonly target = new THREE.Vector3(0, 0.5, -2.9);
  onFirstFrame: (() => void) | null = null;

  constructor(canvas: HTMLCanvasElement, private readonly reduced: boolean, coarse: boolean) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    // Tablets: never render more than 1.5 device pixels per CSS pixel.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.frameMs = coarse ? 1000 / 32 : 0; // touch devices: ~30 fps is plenty and saves battery

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.28;
    pmrem.dispose();
    this.scene.background = new THREE.Color("#16151a");
    this.scene.fog = new THREE.Fog("#16151a", 24, 44);

    const blob = face(128, 128);
    screens.drawBlob(blob.g, 128, 128);
    blob.tex.colorSpace = THREE.NoColorSpace;
    this.kit = new Kit(blob.tex);

    this.buildRoom();
    this.wall = this.buildWall();
    this.ticker = this.buildTicker();
    ORDER.forEach((id, i) => this.desks.set(id, this.buildDesk(id, i)));
    this.buildPaths();
    this.buildStaff();
    this.placeCamera(0);
    if (document.fonts) void document.fonts.ready.then(() => { this.wallDirty = true; this.desks.forEach(d => { d.sig = ""; }); });
    this.setRunning(true);
  }

  // ---------- building ----------

  private buildRoom() {
    const { scene, kit } = this;
    const floorFace = face(1024, 1024);
    screens.drawFloor(floorFace.g, 1024, 1024);
    floorFace.tex.generateMipmaps = true;
    floorFace.tex.minFilter = THREE.LinearMipmapLinearFilter;
    floorFace.tex.wrapS = floorFace.tex.wrapT = THREE.RepeatWrapping;
    floorFace.tex.repeat.set(12, 12);
    floorFace.tex.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(32, 30), new THREE.MeshStandardMaterial({ map: floorFace.tex, roughness: 0.42, metalness: 0.05, envMapIntensity: 0.6 }));
    floor.rotation.x = -Math.PI / 2; floor.position.z = -1;
    scene.add(floor);
    // A round stand-up table in the middle of the arc.
    const oak = kit.mat("#c4a584", 0.55), steel = kit.mat("#8a8f96", 0.35, 0.8);
    for (const [m, s, y] of [[oak, [0.8, 0.05, 0.8], 1.05], [steel, [0.06, 1.0, 0.06], 0.53], [steel, [0.38, 0.03, 0.38], 0.02]] as const) {
      const part = new THREE.Mesh(m === oak ? new THREE.CylinderGeometry(1, 1, 1, 28) : kit.cyl, m);
      part.scale.set(s[0], s[1], s[2]); part.position.set(C.x, y, C.z - 0.8);
      scene.add(part);
    }
    const tableShade = kit.shadow(2, 2, 0.5);
    tableShade.position.set(C.x, 0.006, C.z - 0.8);
    scene.add(tableShade);

    // Back wall: warm slatted panel behind the video wall.
    const back = new THREE.Mesh(new THREE.PlaneGeometry(24, 6.5), new THREE.MeshStandardMaterial({ color: "#3b3029", roughness: 0.75 }));
    back.position.set(0, 3.25, -10.3);
    scene.add(back);
    const slat = kit.mat("#5a4536", 0.7);
    for (let x = -11.6; x <= 11.6; x += 0.4) {
      const s = new THREE.Mesh(kit.box, slat);
      s.scale.set(0.12, 6.5, 0.08); s.position.set(x, 3.25, -10.22);
      scene.add(s);
    }

    // Glass walls with a dusk city behind them.
    const city = face(2048, 640);
    screens.drawCity(city.g, 2048, 640);
    const glass = new THREE.MeshStandardMaterial({ color: "#c9d3dc", roughness: 0.05, metalness: 0.9, transparent: true, opacity: 0.12, depthWrite: false });
    const frame = kit.mat("#1d1e22", 0.5, 0.6);
    for (const side of [-1, 1]) {
      const sky = new THREE.Mesh(new THREE.PlaneGeometry(34, 11), new THREE.MeshBasicMaterial({ map: city.tex, fog: false }));
      sky.position.set(side * 17, 4.2, -2); sky.rotation.y = -side * Math.PI / 2;
      const pane = new THREE.Mesh(new THREE.PlaneGeometry(22, 6.5), glass);
      pane.position.set(side * 11.4, 3.25, -0.5); pane.rotation.y = -side * Math.PI / 2;
      scene.add(sky, pane);
      for (let z = -10.3; z <= 10; z += 2.4) {
        const post = new THREE.Mesh(kit.box, frame);
        post.scale.set(0.12, 6.5, 0.12); post.position.set(side * 11.4, 3.25, z);
        scene.add(post);
      }
      for (const y of [0.05, 6.45]) {
        const rail = new THREE.Mesh(kit.box, frame);
        rail.scale.set(0.14, 0.1, 21); rail.position.set(side * 11.4, y, -0.3);
        scene.add(rail);
      }
      // Potted plants along the glass.
      for (const z of [-7.5, -2.5, 3]) this.plant(side * 10.4, z);
    }
    const far = new THREE.Mesh(new THREE.PlaneGeometry(60, 14), new THREE.MeshBasicMaterial({ map: city.tex, fog: false }));
    far.position.set(0, 5, -30);
    scene.add(far);

    // Warm, neutral light: sky/ground fill, one key light, no shadow maps (blob shadows instead).
    scene.add(new THREE.HemisphereLight("#fff1dd", "#3a2e25", 1.25));
    const key = new THREE.DirectionalLight("#ffe0b8", 1.7);
    key.position.set(-6, 10, 7);
    scene.add(key, key.target);
    const rim = new THREE.DirectionalLight("#bcd0ff", 0.45);
    rim.position.set(7, 6, -8);
    scene.add(rim);
  }

  private plant(x: number, z: number) {
    const { kit, scene } = this;
    const pot = new THREE.Mesh(kit.cyl, kit.mat("#d8d2c8", 0.6));
    pot.scale.set(0.3, 0.6, 0.3); pot.position.set(x, 0.3, z);
    const shade = kit.shadow(1.1, 1.1);
    shade.position.set(x, 0.006, z);
    scene.add(pot, shade);
    const leaf = kit.mat("#3f6b45", 0.8);
    const geo = new THREE.IcosahedronGeometry(1, 0);
    for (let i = 0; i < 4; i++) {
      const m = new THREE.Mesh(geo, leaf);
      const s = 0.32 + this.random() * 0.2;
      m.scale.set(s, s * 1.4, s);
      m.position.set(x + (this.random() - 0.5) * 0.4, 0.95 + i * 0.28, z + (this.random() - 0.5) * 0.4);
      m.rotation.set(this.random() * 3, this.random() * 3, 0);
      scene.add(m);
    }
  }

  private buildWall() {
    const total = PANELS.reduce((a, p) => a + p.width, 0) + PANEL_GAP * (PANELS.length - 1);
    let s = -total / 2;
    const out = {} as Record<"btc" | "eth" | "sol" | "equity", Face>;
    const backing = this.kit.mat("#0b0c0e", 0.45, 0.6);
    for (const p of PANELS) {
      const phi = (s + p.width / 2) / WALL_R;
      const pos = new THREE.Vector3(C.x + Math.sin(phi) * WALL_R, 0, C.z - Math.cos(phi) * WALL_R);
      const f = face(Math.round(p.width * PX_PER_M), Math.round(PANEL_H * PX_PER_M));
      out[p.key] = f;
      const panel = new THREE.Mesh(new THREE.PlaneGeometry(p.width, PANEL_H), new THREE.MeshBasicMaterial({ map: f.tex, toneMapped: false }));
      panel.position.set(pos.x, PANEL_BOTTOM + PANEL_H / 2, pos.z);
      panel.rotation.y = -phi;
      const frame = new THREE.Mesh(this.kit.box, backing);
      frame.scale.set(p.width + 0.08, PANEL_H + 0.08, 0.1);
      frame.position.copy(panel.position).add(new THREE.Vector3(Math.sin(phi), 0, -Math.cos(phi)).multiplyScalar(0.06));
      frame.rotation.y = -phi;
      this.scene.add(frame, panel);
      s += p.width + PANEL_GAP;
    }
    return out;
  }

  /** The LED ticker: one texture scrolled across strips under the wall panels. */
  private buildTicker() {
    const total = PANELS.reduce((a, p) => a + p.width, 0) + PANEL_GAP * (PANELS.length - 1);
    const loop = total * 2.2; // metres of text before it repeats
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
      strip.position.set(pos.x, PANEL_BOTTOM - 0.12 - STRIP_H / 2, pos.z + 0.02);
      strip.rotation.y = -phi;
      this.scene.add(strip);
      s += p.width + PANEL_GAP;
    }
    return f;
  }

  private buildDesk(id: DeskId, index: number): Desk {
    const { kit } = this;
    const a = ANGLE[id];
    const group = new THREE.Group();
    group.position.copy(onArc(a, R));
    group.rotation.y = -a; // local -z points away from the arc centre, towards the monitors
    this.scene.add(group);
    const part = (m: THREE.Material, s: [number, number, number], p: [number, number, number], geo: THREE.BufferGeometry = kit.box) => {
      const mesh = new THREE.Mesh(geo, m); mesh.scale.set(...s); mesh.position.set(...p); group.add(mesh); return mesh;
    };
    const top = kit.mat("#c4a584", 0.55), body = kit.mat("#2a2c30", 0.6, 0.3), metal = kit.mat("#8a8f96", 0.35, 0.8);
    part(top, [2.3, 0.05, 0.92], [0, 0.745, 0]);
    part(body, [2.2, 0.62, 0.04], [0, 0.4, -0.38]);
    for (const side of [-1, 1]) part(body, [0.05, 0.72, 0.88], [side * 1.12, 0.36, 0]);
    const shade = kit.shadow(3.2, 1.9, 0.6);
    shade.position.set(0, 0.006, 0.1);
    group.add(shade);
    // Under-edge LED: tone colour while the desk works.
    const led = new THREE.MeshBasicMaterial({ color: "#f2e9da", toneMapped: false });
    part(led, [2.2, 0.012, 0.012], [0, 0.715, 0.46]);
    // Floor glow under the desk: tone colour, strong for an alert.
    const glow = new THREE.MeshBasicMaterial({ map: kit.blob.map, color: "#000000", transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 1 });
    const glowMesh = new THREE.Mesh(kit.blobGeo, glow);
    glowMesh.rotation.x = -Math.PI / 2; glowMesh.scale.set(4.2, 2.8, 1); glowMesh.position.set(0, 0.012, 0.2);
    group.add(glowMesh);
    // Desk beacon: the red light of an alert, the tone colour otherwise.
    const beacon = new THREE.MeshBasicMaterial({ color: "#5b6269", toneMapped: false });
    part(metal, [0.05, 0.04, 0.05], [1.0, 0.79, -0.32], kit.cyl);
    part(beacon, [0.08, 0.22, 0.08], [1.0, 0.92, -0.32], kit.cyl);

    // Two monitors: the live station headline, and the station's journal (the XP lane: one).
    const bezel = kit.mat("#0c0d0f", 0.4, 0.5);
    const main = face(512, 300);
    const log = id === "xp" ? null : face(512, 300);
    const monitors = log ? [[-0.36, main], [0.36, log]] as const : [[0, main]] as const;
    for (const [x, f] of monitors) {
      const mon = new THREE.Group();
      mon.position.set(x, 0, -0.22 + Math.abs(x) * 0.12);
      mon.rotation.y = -Math.sign(x) * 0.22;
      const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.66, 0.39), new THREE.MeshBasicMaterial({ map: f.tex, toneMapped: false }));
      screen.position.set(0, 1.1, 0.014);
      const back = new THREE.Mesh(kit.box, bezel); back.scale.set(0.69, 0.42, 0.025); back.position.set(0, 1.1, 0);
      const stand = new THREE.Mesh(kit.box, metal); stand.scale.set(0.04, 0.3, 0.03); stand.position.set(0, 0.88, -0.03);
      const foot = new THREE.Mesh(kit.box, metal); foot.scale.set(0.22, 0.012, 0.14); foot.position.set(0, 0.776, -0.03);
      mon.add(screen, back, stand, foot);
      group.add(mon);
    }
    part(kit.mat("#18191c", 0.5), [0.44, 0.015, 0.14], [0, 0.778, 0.26]);

    // Chair and operator.
    const ch = chair(kit);
    ch.position.set(0, 0, 0.85);
    group.add(ch);
    const operator = new Person(kit, LOOKS[id], index * 1.7);
    this.scene.add(operator.group);
    group.updateMatrixWorld(true);
    operator.place(this.seat(group), -a, true);

    // Name plate floating over the desk.
    const plate = face(512, 128);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: plate.tex, transparent: true, depthWrite: false, toneMapped: false }));
    sprite.scale.set(2.3, 0.575, 1);
    sprite.position.set(0, 2.25, -0.2);
    sprite.renderOrder = 10;
    group.add(sprite);

    // Selection ring on the floor.
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.75, 1.85, 48), new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.75, depthWrite: false, toneMapped: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(0, 0.015, 0.35); ring.scale.set(1, 0.7, 1); ring.visible = false;
    group.add(ring);

    // What a tap hits: an invisible box around desk, monitors and operator.
    const hit = new THREE.Mesh(kit.box, new THREE.MeshBasicMaterial({ visible: false }));
    hit.scale.set(2.5, 2.4, 2.3); hit.position.set(0, 1.2, 0.35);
    hit.userData.desk = id;
    group.add(hit);
    this.hits.push(hit);

    return { id, group, operator, main, log, plate, sprite, beacon, led, glow, ring, flash: 0, sig: "" };
  }

  private local(group: THREE.Group, x: number, z: number) { return group.localToWorld(new THREE.Vector3(x, 0, z)).setY(0); }
  private seat(group: THREE.Group) { return this.local(group, 0, 0.85); }

  private buildPaths() {
    for (const [from, to, r] of PATHS) {
      const a0 = ANGLE[from] + 0.1, a1 = ANGLE[to] - 0.1;
      // RingGeometry lies in XY with theta from +x; flat on the floor theta = π/2 - a.
      const geo = new THREE.RingGeometry(r - 0.05, r + 0.05, 40, 1, Math.PI / 2 - a1, a1 - a0);
      const mat = new THREE.MeshBasicMaterial({ color: "#f2e9da", transparent: true, opacity: 0.3, depthWrite: false, toneMapped: false });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.rotation.x = -Math.PI / 2; mesh.position.set(C.x, 0.01, C.z);
      this.scene.add(mesh);
      this.paths.push({ from, to, mat });
    }
  }

  private buildStaff() {
    STAFF.forEach((look, i) => {
      const p = new Person(this.kit, look, 3 + i * 2.3);
      const desk = this.desks.get(ORDER[(i * 3 + 1) % ORDER.length])!;
      p.place(this.local(desk.group, -0.95, 1.3), -ANGLE[desk.id], false);
      p.plan({ kind: "wait", seconds: 1 + i * 2.5 });
      this.scene.add(p.group);
      this.staff.push(p);
    });
  }

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
  private handoff(from: DeskId, to: DeskId, tone: Tone) {
    const src = this.desks.get(from)!, dst = this.desks.get(to)!;
    const p = src.operator;
    if (p.queued > 60) { dst.flash = 1; return; } // a burst of events: light the desk, skip the walk
    const seat = this.seat(src.group), out = this.local(src.group, 0.7, 1.45);
    const visit = this.local(dst.group, 0.85, 1.2);
    p.plan({ kind: "sit", on: false }, { kind: "carry", color: TONE_COLOR[tone] });
    p.walk([out, ...this.route(out, visit)]);
    p.plan(
      { kind: "face", heading: -ANGLE[to] }, { kind: "wait", seconds: this.reduced ? 2.5 : 1.3 },
      { kind: "call", fn: () => { dst.flash = 1; } }, { kind: "carry", color: null }, { kind: "wait", seconds: 0.5 },
    );
    p.walk([...this.route(visit, out), seat]);
    p.plan({ kind: "face", heading: -ANGLE[from] }, { kind: "sit", on: true });
  }

  /** Floor staff: walk to a desk, look at its screens for a while, move on. Decoration only. */
  private wander(p: Person) {
    const id = ORDER[Math.floor(this.random() * ORDER.length)];
    const desk = this.desks.get(id)!;
    const spot = this.local(desk.group, -0.95 + (this.random() - 0.5) * 0.3, 1.3);
    p.walk(this.route(p.group.position.clone(), spot));
    p.plan({ kind: "face", heading: -ANGLE[id] }, { kind: "wait", seconds: 3 + this.random() * 6 });
  }

  setData(data: FloorData) {
    const first = this.seen === null;
    if (first) this.seen = new Set();
    const seen = this.seen!;
    for (const a of data.actions) {
      const key = `${a.seq}:${a.station}`;
      if (seen.has(key)) continue;
      seen.add(key);
      // History (the first read, or anything not fresh from the stream) never makes anyone walk.
      if (first || !data.busy[a.station]) continue;
      const prev = PREV[a.station];
      if (prev) this.handoff(prev, a.station, a.tone);
      else this.desks.get(a.station)!.flash = 1;
    }
    if (seen.size > 600) this.seen = new Set(data.actions.map(a => `${a.seq}:${a.station}`));
    this.data = data;
    this.wallDirty = true;
  }

  // ---------- camera and input ----------

  resize(width: number, height: number) {
    if (width < 2 || height < 2) return;
    this.renderer.setSize(width, height, false);
    const aspect = width / height;
    this.camera.aspect = aspect;
    // Narrow screens step back so the whole arc stays in view.
    this.fit = Math.max(1, Math.pow(1.7 / aspect, 0.85));
    this.camera.updateProjectionMatrix();
  }

  private placeCamera(dt: number) {
    if (!this.reduced && this.t - this.userAt > 8) {
      const auto = Math.sin(this.t * 0.045) * 0.22;
      this.yaw += (auto - this.yaw) * Math.min(1, dt * 0.4);
    }
    const r = this.radius * this.fit;
    this.camera.position.set(
      this.target.x + Math.sin(this.yaw) * Math.cos(this.pitch) * r,
      this.target.y + Math.sin(this.pitch) * r,
      this.target.z + Math.cos(this.yaw) * Math.cos(this.pitch) * r,
    );
    this.camera.lookAt(this.target);
  }

  orbit(dx: number, dy: number) {
    this.userAt = this.t;
    this.yaw = THREE.MathUtils.clamp(this.yaw - dx * 0.005, -1.1, 1.1);
    this.pitch = THREE.MathUtils.clamp(this.pitch + dy * 0.004, THREE.MathUtils.degToRad(8), THREE.MathUtils.degToRad(42));
    if (!this.running) this.renderOnce();
  }

  zoom(factor: number) {
    this.userAt = this.t;
    this.radius = THREE.MathUtils.clamp(this.radius * factor, 6.5, 17);
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
      screens.drawPlate(desk.plate.g, desk.plate.w, desk.plate.h, XP_DESK.label, tone, d.xp === "live", selected);
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
      screens.drawPlate(desk.plate.g, desk.plate.w, desk.plate.h, info.label, action ? action.tone : "idle", busy, selected);
    }
    desk.main.tex.needsUpdate = true;
    desk.plate.tex.needsUpdate = true;
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
    if (!this.reduced) {
      this.scroll += dt * 0.018;
      for (const tex of this.tickerTextures) tex.offset.x = (tex.userData.base as number) + this.scroll;
    }

    const pulse = this.reduced ? 1 : 0.7 + 0.3 * Math.sin(t * 5);
    const color = new THREE.Color();
    for (const desk of this.desks.values()) {
      const { tone, busy } = this.toneOf(desk, d);
      const alert = tone === "alert";
      desk.flash = Math.max(0, desk.flash - dt * 0.6);
      const strength = Math.min(1, (busy ? 0.75 : 0) + desk.flash + (alert ? 0.6 : 0));
      color.set(TONE_COLOR[tone]);
      desk.beacon.color.copy(color).multiplyScalar(alert ? 1.6 * pulse : busy ? 1.3 : 0.8);
      desk.led.color.set(busy || alert ? TONE_COLOR[tone] : "#f2e9da").multiplyScalar(busy || alert ? 1.4 : 0.9);
      desk.glow.color.copy(color).multiplyScalar(strength * (alert ? 1.25 * pulse : 0.5));
      desk.ring.visible = d?.selected != null && drawerOf(desk.id) === d.selected;
      desk.operator.update(t, dt, busy, this.reduced);
    }
    for (const line of this.paths) {
      const dst = this.desks.get(line.to)!;
      const { tone, busy } = this.toneOf(dst, d);
      const lit = busy || dst.flash > 0.05;
      line.mat.color.set(lit ? TONE_COLOR[tone] : "#f2e9da");
      line.mat.opacity = lit ? 0.55 + 0.4 * pulse : 0.4;
    }
    for (const p of this.staff) {
      if (!p.busy) this.wander(p);
      p.update(t, dt, false, this.reduced);
    }
    this.placeCamera(dt);
    this.renderer.render(this.scene, this.camera);
    if (this.onFirstFrame) { const cb = this.onFirstFrame; this.onFirstFrame = null; cb(); }
  }

  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    const textures = new Set<THREE.Texture>(this.tickerTextures);
    this.scene.traverse(o => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Sprite) {
        o.geometry.dispose();
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) {
          const map = (m as THREE.MeshBasicMaterial).map;
          if (map) textures.add(map);
          m.dispose();
        }
      }
    });
    textures.forEach(t => t.dispose());
    this.scene.environment?.dispose();
    this.renderer.dispose();
  }
}
