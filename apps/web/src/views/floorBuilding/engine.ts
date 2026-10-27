// Stylized isometric building of the trading floor ("Prédio"). Cheap on tablets by design: an
// orthographic camera, Lambert/basic materials, instanced desks and monitors, no shadow maps (soft
// blob decals stand in), DPR capped at 1.5, rendering only while visible. Every desk, character and
// board is driven by the data the view hands in; nothing here invents a value.
import * as THREE from "three";
import { canvas, drawLimits, drawLogo, drawResult, drawSign, type LimitBoard, radial, type ResultBoard } from "./boards";
import type { FloorId, Kind } from "./data";

export type View = "building" | FloorId;
export type SceneAgent = { id: string; kind: Kind; floor: FloorId; status: "ACTIVE" | "ELIMINATED"; busy: boolean };
export type Anchor = { x: number; y: number; visible: boolean };
export type Pick = { type: "floor"; id: FloorId } | { type: "agent"; id: string } | null;

export const FLOORS: FloorId[] = ["binance", "xp"]; // bottom to top
const W = 22, D = 15, H = 4.4, GAP = 8.4;
const KIND_COLOR: Record<Kind, number> = { own: 0x22d3ee, tradingview: 0xb06cff };
const AMBER = 0xffb21e;
const DOOR = new THREE.Vector3(-10.4, 0, 4.6);
const LOUNGE = { x: 7.0, z: -4.6 };
const MAX_DESKS = 24;
const ELEV_MIN = 0.42, ELEV_MAX = 0.95, AZ_MIN = 0.2, AZ_MAX = 1.37;

type Mats = Record<"floor" | "wall" | "deskTop" | "deskBody" | "monitor" | "chair" | "sofa" | "rug" | "pot" | "leaf" | "plinth" | "core", THREE.MeshLambertMaterial>;
const BASE: Record<keyof Mats, number> = {
  floor: 0xcdd3de, wall: 0x1c2438, deskTop: 0xb3bac6, deskBody: 0x7d8695, monitor: 0x1a2030, chair: 0x2e3648,
  sofa: 0x58627a, rug: 0x323b55, pot: 0xe9e4da, leaf: 0x3fae6a, plinth: 0x141b2d, core: 0x161e31,
};
const DIM = new THREE.Color(0x0b1120);

type Seat = { x: number; z: number; face: 1 | -1; kind: Kind };
type Robot = {
  id: string; group: THREE.Group; armL: THREE.Group; armR: THREE.Group; head: THREE.Group; eyes: THREE.Mesh[];
  floor: FloorId; status: "ACTIVE" | "ELIMINATED"; kind: Kind; busy: boolean; phase: number;
  target: THREE.Vector3; rot: number; walk: { from: THREE.Vector3; mid: THREE.Vector3; to: THREE.Vector3; t: number } | null;
  anchor: THREE.Vector3; hit: THREE.Mesh;
};
type Board = { tex: THREE.CanvasTexture<OffscreenCanvas>; c: OffscreenCanvas; g: OffscreenCanvasRenderingContext2D; key: string };
type Floor = {
  id: FloorId; group: THREE.Group; y: number; mats: Mats; slab: THREE.Mesh; dim: boolean;
  desks: THREE.InstancedMesh; bodies: THREE.InstancedMesh; monitors: THREE.InstancedMesh; screens: THREE.InstancedMesh;
  chairs: THREE.InstancedMesh; backs: THREE.InstancedMesh; blobs: THREE.InstancedMesh;
  glows: Record<Kind, { plane: THREE.Mesh; ring: THREE.LineSegments }>;
  result: Board; limits: Board; seats: Seat[]; anchors: Record<string, THREE.Vector3>;
};

const box = (w: number, h: number, d: number) => new THREE.BoxGeometry(w, h, d);
const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

export class BuildingEngine {
  onAnchors: ((a: Record<string, Anchor>) => void) | null = null;
  onFirstFrame: (() => void) | null = null;
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 400);
  private floors = new Map<FloorId, Floor>();
  private robots = new Map<string, Robot>();
  private disposables: { dispose: () => void }[] = [];
  private shared: Record<string, THREE.BufferGeometry> = {};
  private robotMats = new Map<string, THREE.Material>();
  private ray = new THREE.Raycaster();
  private view: View = "building";
  private az = 0.64; private elev = 0.6; private zoomF = 1;
  private target = v3(0, 6, 0); private viewH = 34;
  private goal = { target: v3(0, 6, 0), viewH: 34 };
  private width = 1; private height = 1;
  private running = false; private animate = true; private dirty = true; private raf = 0; private first = true;
  private clock = new THREE.Timer(); // THREE.Clock is deprecated
  private known = new Set<string>(); private rosterSeen = false;
  private sign: THREE.Group;
  private core: THREE.Mesh;

  constructor(canvasEl: HTMLCanvasElement, private reduced: boolean) {
    this.renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.setClearColor(0x000000, 0);
    this.animate = !reduced;
    this.scene.add(new THREE.HemisphereLight(0xe4ecff, 0x1d2335, 1.7));
    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(9, 16, 12);
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0x9fb4ff, 0.5);
    fill.position.set(-10, 6, 4);
    this.scene.add(fill);

    this.shared = {
      deskTop: box(2.0, 0.1, 1.0), deskBody: box(1.9, 0.75, 0.08), monitor: box(0.78, 0.5, 0.06), screen: new THREE.PlaneGeometry(0.7, 0.42),
      seat: box(0.62, 0.12, 0.6), back: box(0.62, 0.62, 0.1), plane: new THREE.PlaneGeometry(1, 1), unit: box(1, 1, 1),
      torso: box(0.6, 0.56, 0.42), stripe: box(0.62, 0.1, 0.44), head: box(0.72, 0.56, 0.56), visor: box(0.58, 0.3, 0.04), eye: box(0.1, 0.1, 0.03),
      arm: box(0.15, 0.44, 0.15), leg: box(0.2, 0.2, 0.5), antenna: new THREE.CylinderGeometry(0.025, 0.025, 0.22, 5), ball: new THREE.IcosahedronGeometry(0.07, 0),
      pot: new THREE.CylinderGeometry(0.28, 0.22, 0.42, 7), leaf: new THREE.IcosahedronGeometry(0.42, 0),
    };
    Object.values(this.shared).forEach(g => this.disposables.push(g));
    const blobTex = new THREE.CanvasTexture(radial(128, "rgba(10,16,30,0.55)", "rgba(10,16,30,0)"));
    const glowTex = new THREE.CanvasTexture(radial(128, "rgba(255,255,255,0.9)", "rgba(255,255,255,0)"));
    this.disposables.push(blobTex, glowTex);

    // Ground plinth with its own soft shadow.
    const plinth = new THREE.Mesh(box(W + 3, 0.8, D + 3), this.mat(0x111829));
    plinth.position.set(0, -0.9, 0);
    this.scene.add(plinth);
    this.neonRect(this.scene, W + 3, D + 3, -0.48, AMBER, 0.08);
    const ground = new THREE.Mesh(this.shared.plane, new THREE.MeshBasicMaterial({ map: blobTex, transparent: true, depthWrite: false }));
    ground.rotation.x = -Math.PI / 2; ground.scale.set(W * 1.9, D * 1.9, 1); ground.position.y = -1.32;
    this.scene.add(ground); this.disposables.push(ground.material as THREE.Material);

    FLOORS.forEach((id, i) => this.floors.set(id, this.buildFloor(id, i * GAP, blobTex, glowTex)));

    // The service core joins the floors at the back-left corner.
    const top = (FLOORS.length - 1) * GAP + H;
    this.core = new THREE.Mesh(box(2.2, top + 1.4, 2.2), this.mat(BASE.core));
    this.core.position.set(-W / 2 + 1.1, top / 2 - 0.7, -D / 2 + 1.1);
    this.scene.add(this.core);

    // The roof sign stands on the top floor's back wall.
    this.sign = new THREE.Group();
    const s = canvas(1024, 256); drawSign(s.g, 1024, 256);
    const signTex = new THREE.CanvasTexture(s.c); signTex.colorSpace = THREE.SRGBColorSpace; this.disposables.push(signTex);
    const face = new THREE.Mesh(new THREE.PlaneGeometry(10, 2.5), new THREE.MeshBasicMaterial({ map: signTex }));
    face.position.set(0, 1.9, 0.08);
    const frame = new THREE.Mesh(box(10.4, 2.9, 0.12), this.mat(0x0a0f1d));
    frame.position.set(0, 1.9, 0);
    const legs = [-3.5, 3.5].map(x => { const m = new THREE.Mesh(box(0.18, 0.7, 0.18), this.mat(0x2a3346)); m.position.set(x, 0.3, 0); return m; });
    this.sign.add(face, frame, ...legs);
    this.neonRect(this.sign, 10.4, 0.01, 0, AMBER, 0.06, 3.38, 0.07);
    this.neonRect(this.sign, 10.4, 0.01, 0, AMBER, 0.06, 0.42, 0.07);
    this.sign.position.set(-2, top + 0.15, -D / 2 + 0.4);
    this.scene.add(this.sign);
    this.disposables.push(face.material as THREE.Material, face.geometry, frame.geometry);
    this.setView("building", true);
  }

  private mat(color: number) {
    const key = `l${color}`;
    let m = this.robotMats.get(key) as THREE.MeshLambertMaterial | undefined;
    if (!m) { m = new THREE.MeshLambertMaterial({ color }); this.robotMats.set(key, m); }
    return m;
  }
  private basic(color: number, opacity = 1) {
    const key = `b${color}:${opacity}`;
    let m = this.robotMats.get(key) as THREE.MeshBasicMaterial | undefined;
    if (!m) { m = new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: opacity >= 1 }); this.robotMats.set(key, m); }
    return m;
  }

  /** Neon edge: four thin glowing bars around a w×d rectangle (or a single bar when d is ~0). */
  private neonRect(parent: THREE.Object3D, w: number, d: number, y: number, color: number, t = 0.07, yOff = 0, z = 0) {
    const m = this.basic(color);
    const bars: [number, number, number, number, number][] = d < 0.05
      ? [[0, z, w, t, t]]
      : [[0, d / 2, w + t, t, t], [0, -d / 2, w + t, t, t], [w / 2, 0, t, t, d], [-w / 2, 0, t, t, d]];
    for (const [x, zz, sx, sy, sz] of bars) {
      const bar = new THREE.Mesh(this.shared.unit, m);
      bar.scale.set(sx, sy, sz);
      bar.position.set(x, y + yOff, zz);
      parent.add(bar);
    }
  }

  private buildFloor(id: FloorId, y: number, blobTex: THREE.Texture, glowTex: THREE.Texture): Floor {
    const group = new THREE.Group();
    group.position.y = y;
    this.scene.add(group);
    const mats = Object.fromEntries(Object.entries(BASE).map(([k, c]) => [k, new THREE.MeshLambertMaterial({ color: c })])) as Mats;
    Object.values(mats).forEach(m => this.disposables.push(m));

    const slab = new THREE.Mesh(box(W, 0.5, D), mats.floor);
    slab.position.y = -0.25;
    group.add(slab);
    const under = new THREE.Mesh(box(W, 0.2, D), mats.plinth);
    under.position.y = -0.6;
    group.add(under);
    // Walls: back and left (the cutaway faces the camera), each with a neon top edge.
    const back = new THREE.Mesh(box(W, H, 0.3), mats.wall); back.position.set(0, H / 2, -D / 2 + 0.15); group.add(back);
    const left = new THREE.Mesh(box(0.3, H, D), mats.wall); left.position.set(-W / 2 + 0.15, H / 2, 0); group.add(left);
    const neon = this.basic(AMBER);
    const bar = (sx: number, sy: number, sz: number, x: number, yy: number, z: number) => {
      const m = new THREE.Mesh(this.shared.unit, neon); m.scale.set(sx, sy, sz); m.position.set(x, yy, z); group.add(m);
    };
    bar(W, 0.08, 0.08, 0, H, -D / 2 + 0.3); bar(0.08, 0.08, D, -W / 2 + 0.3, H, 0);
    // Slab edges and a low glass railing with a neon handrail on the open sides.
    bar(W + 0.1, 0.1, 0.1, 0, 0.02, D / 2); bar(0.1, 0.1, D + 0.1, W / 2, 0.02, 0);
    bar(W, 0.06, 0.06, 0, 0.95, D / 2 - 0.05); bar(0.06, 0.06, D, W / 2 - 0.05, 0.95, 0);
    const glass = this.basic(0x9fd8ff, 0.1);
    const g1 = new THREE.Mesh(this.shared.plane, glass); g1.scale.set(W, 0.9, 1); g1.position.set(0, 0.47, D / 2 - 0.05); group.add(g1);
    const g2 = new THREE.Mesh(this.shared.plane, glass); g2.scale.set(D, 0.9, 1); g2.rotation.y = Math.PI / 2; g2.position.set(W / 2 - 0.05, 0.47, 0); group.add(g2);

    // Entrance on the left wall: a dark door with a neon frame.
    const door = new THREE.Mesh(box(0.06, 2.4, 1.8), this.mat(0x0a0f1d)); door.position.set(-W / 2 + 0.33, 1.2, DOOR.z); group.add(door);
    bar(0.06, 0.07, 1.95, -W / 2 + 0.36, 2.45, DOOR.z); bar(0.06, 2.45, 0.07, -W / 2 + 0.36, 1.22, DOOR.z - 0.95); bar(0.06, 2.45, 0.07, -W / 2 + 0.36, 1.22, DOOR.z + 0.95);
    const mat = new THREE.Mesh(this.shared.plane, this.mat(0x2a3350)); mat.rotation.x = -Math.PI / 2; mat.scale.set(1.6, 2, 1); mat.position.set(-W / 2 + 1.2, 0.012, DOOR.z); group.add(mat);

    // PROTO logo on the left wall.
    const logo = canvas(1024, 256); drawLogo(logo.g, 1024, 256);
    const logoTex = new THREE.CanvasTexture(logo.c); logoTex.colorSpace = THREE.SRGBColorSpace; logoTex.anisotropy = 4;
    const logoMesh = new THREE.Mesh(new THREE.PlaneGeometry(7, 1.75), new THREE.MeshBasicMaterial({ map: logoTex }));
    logoMesh.rotation.y = Math.PI / 2; logoMesh.position.set(-W / 2 + 0.32, 2.7, -1.6); group.add(logoMesh);
    this.disposables.push(logoTex, logoMesh.geometry, logoMesh.material as THREE.Material);

    // Wall boards (redrawn only when their content changes).
    const mkBoard = (w: number, h: number, pw: number, ph: number, x: number): Board => {
      const { c, g } = canvas(w, h);
      const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
      const m = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), new THREE.MeshBasicMaterial({ map: tex }));
      m.position.set(x, 2.45, -D / 2 + 0.34);
      group.add(m);
      const fr = new THREE.Mesh(box(pw + 0.2, ph + 0.2, 0.06), this.mat(0x0a0f1d)); fr.position.set(x, 2.45, -D / 2 + 0.27); group.add(fr);
      this.disposables.push(tex, m.geometry, m.material as THREE.Material, fr.geometry);
      return { tex, c, g, key: "" };
    };
    const result = mkBoard(1024, 600, 6.0, 3.52, -3.9);
    const limits = mkBoard(800, 600, 4.7, 3.52, 2.9);

    // Lounge for eliminated agents: rug, two sofas, a coffee table.
    const rug = new THREE.Mesh(this.shared.plane, mats.rug); rug.rotation.x = -Math.PI / 2; rug.scale.set(6.4, 4.6, 1); rug.position.set(LOUNGE.x, 0.01, LOUNGE.z + 0.4); group.add(rug);
    for (const [x, z, ry] of [[LOUNGE.x, LOUNGE.z - 1.5, 0], [LOUNGE.x + 2.6, LOUNGE.z + 0.8, -Math.PI / 2]] as const) {
      const sofa = new THREE.Group();
      const base = new THREE.Mesh(box(3.4, 0.45, 1.0), mats.sofa); base.position.y = 0.3;
      const backrest = new THREE.Mesh(box(3.4, 0.7, 0.3), mats.sofa); backrest.position.set(0, 0.7, -0.38);
      const armA = new THREE.Mesh(box(0.3, 0.6, 1.0), mats.sofa); armA.position.set(-1.6, 0.45, 0);
      const armB = armA.clone(); armB.position.x = 1.6;
      sofa.add(base, backrest, armA, armB);
      sofa.position.set(x, 0, z); sofa.rotation.y = ry;
      group.add(sofa);
    }
    const table = new THREE.Mesh(box(1.4, 0.35, 0.9), mats.deskBody); table.position.set(LOUNGE.x, 0.18, LOUNGE.z + 0.6); group.add(table);
    const mug = new THREE.Mesh(this.shared.pot, this.mat(0xffffff)); mug.scale.setScalar(0.25); mug.position.set(LOUNGE.x + 0.3, 0.42, LOUNGE.z + 0.5); group.add(mug);

    // Plants in the corners.
    for (const [x, z] of [[W / 2 - 0.9, D / 2 - 0.9], [-W / 2 + 1, D / 2 - 1], [W / 2 - 0.9, -D / 2 + 0.9], [-W / 2 + 3.2, -D / 2 + 0.9]] as const) {
      const pot = new THREE.Mesh(this.shared.pot, mats.pot); pot.position.set(x, 0.21, z);
      const leaf = new THREE.Mesh(this.shared.leaf, mats.leaf); leaf.position.set(x, 0.78, z); leaf.scale.set(1, 1.25, 1);
      const leaf2 = new THREE.Mesh(this.shared.leaf, mats.leaf); leaf2.position.set(x + 0.15, 1.25, z - 0.05); leaf2.scale.setScalar(0.65);
      group.add(pot, leaf, leaf2);
    }

    // Instanced office furniture; counts follow the seats.
    const inst = (geo: THREE.BufferGeometry, m: THREE.Material, n: number) => { const im = new THREE.InstancedMesh(geo, m, n); im.count = 0; im.frustumCulled = false; group.add(im); this.disposables.push(im); return im; };
    const screenMat = new THREE.MeshBasicMaterial({ color: 0xffffff }); this.disposables.push(screenMat);
    const blobMat = new THREE.MeshBasicMaterial({ map: blobTex, transparent: true, depthWrite: false }); this.disposables.push(blobMat);
    const desks = inst(this.shared.deskTop, mats.deskTop, MAX_DESKS);
    const bodies = inst(this.shared.deskBody, mats.deskBody, MAX_DESKS);
    const monitors = inst(this.shared.monitor, mats.monitor, MAX_DESKS * 2);
    const screens = inst(this.shared.screen, screenMat, MAX_DESKS * 2);
    const chairs = inst(this.shared.seat, mats.chair, MAX_DESKS);
    const backs = inst(this.shared.back, mats.chair, MAX_DESKS);
    const blobs = inst(this.shared.plane, blobMat, MAX_DESKS + 8);
    blobs.renderOrder = -1;

    const glows = {} as Floor["glows"];
    for (const kind of ["own", "tradingview"] as Kind[]) {
      const gm = new THREE.MeshBasicMaterial({ map: glowTex, color: KIND_COLOR[kind], transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.AdditiveBlending });
      const plane = new THREE.Mesh(this.shared.plane, gm); plane.rotation.x = -Math.PI / 2; plane.position.y = 0.015; group.add(plane);
      const ringGeo = new THREE.BufferGeometry();
      const ring = new THREE.LineSegments(ringGeo, new THREE.LineBasicMaterial({ color: KIND_COLOR[kind] }));
      ring.position.y = 0.03; group.add(ring);
      this.disposables.push(gm, ringGeo, ring.material as THREE.Material);
      glows[kind] = { plane, ring };
    }
    return { id, group, y, mats, slab, dim: false, desks, bodies, monitors, screens, chairs, backs, blobs, glows, result, limits, seats: [], anchors: {} };
  }

  /** Seats per strategy kind: pods of two facing rows, own strategy on the left, TradingView on the right. */
  private layout(counts: Record<Kind, number>): Seat[] {
    const seats: Seat[] = [];
    const centre: Record<Kind, number> = { own: -4.8, tradingview: 2.4 };
    for (const kind of ["own", "tradingview"] as Kind[]) {
      const n = Math.min(counts[kind], MAX_DESKS / 2);
      const perPod = 8, pods = Math.max(1, Math.ceil(n / perPod));
      for (let i = 0; i < n; i++) {
        const pod = Math.floor(i / perPod), j = i % perPod, row = j % 2, col = Math.floor(j / 2);
        const cols = Math.min(4, Math.ceil(Math.min(n - pod * perPod, perPod) / 2));
        const x = centre[kind] + (col - (cols - 1) / 2) * 2.45;
        const zc = 2.0 + (pod - (pods - 1) / 2) * 4.9;
        seats.push({ x, z: zc + (row === 0 ? 0.62 : -0.62), face: row === 0 ? -1 : 1, kind });
      }
    }
    return seats;
  }

  private placeFurniture(f: Floor, seats: Seat[], screenOn: boolean[]) {
    const o = new THREE.Object3D();
    const put = (im: THREE.InstancedMesh, i: number, x: number, y: number, z: number, ry = 0, sx = 1, sy = 1, sz = 1, rx = 0) => {
      o.position.set(x, y, z); o.rotation.set(rx, ry, 0); o.scale.set(sx, sy, sz); o.updateMatrix(); im.setMatrixAt(i, o.matrix);
    };
    const c = new THREE.Color();
    seats.forEach((s, i) => {
      const f2 = s.face; // -1: the person sits at +z and looks toward -z
      put(f.desks, i, s.x, 0.82, s.z);
      put(f.bodies, i, s.x, 0.4, s.z + f2 * 0.42);
      for (const k of [0, 1]) {
        const mx = s.x + (k ? 0.42 : -0.42), mz = s.z + f2 * 0.3, ry = (f2 === -1 ? 0 : Math.PI) + (k ? -0.18 : 0.18);
        put(f.monitors, i * 2 + k, mx, 1.18, mz, ry);
        put(f.screens, i * 2 + k, mx + Math.sin(ry) * 0.035, 1.18, mz + Math.cos(ry) * 0.035, ry);
        const lit = screenOn[i] && !f.dim;
        c.setHex(lit ? KIND_COLOR[s.kind] : 0x232b3d);
        if (lit) c.lerp(new THREE.Color(0x0b1424), 0.35);
        f.screens.setColorAt(i * 2 + k, c);
      }
      const cz = s.z - f2 * 0.95;
      put(f.chairs, i, s.x, 0.5, cz);
      put(f.backs, i, s.x, 0.88, cz - f2 * 0.3);
      put(f.blobs, i, s.x, 0.012, s.z - f2 * 0.2, 0, 2.8, 2.6, 1, -Math.PI / 2);
    });
    for (const im of [f.desks, f.bodies, f.chairs, f.backs]) { im.count = seats.length; im.instanceMatrix.needsUpdate = true; }
    for (const im of [f.monitors, f.screens]) { im.count = seats.length * 2; im.instanceMatrix.needsUpdate = true; }
    if (f.screens.instanceColor) f.screens.instanceColor.needsUpdate = true;
    // Lounge blob under the sofas.
    put(f.blobs, seats.length, LOUNGE.x + 0.6, 0.011, LOUNGE.z, 0, 8, 6, 1, -Math.PI / 2);
    f.blobs.count = seats.length + 1; f.blobs.instanceMatrix.needsUpdate = true;

    for (const kind of ["own", "tradingview"] as Kind[]) {
      const mine = seats.filter(s => s.kind === kind);
      const { plane, ring } = f.glows[kind];
      plane.visible = ring.visible = mine.length > 0;
      if (!mine.length) continue;
      const x0 = Math.min(...mine.map(s => s.x)) - 1.5, x1 = Math.max(...mine.map(s => s.x)) + 1.5;
      const z0 = Math.min(...mine.map(s => s.z)) - 1.7, z1 = Math.max(...mine.map(s => s.z)) + 1.7;
      plane.position.set((x0 + x1) / 2, 0.015, (z0 + z1) / 2); plane.scale.set((x1 - x0) * 1.35, (z1 - z0) * 1.35, 1);
      (plane.material as THREE.MeshBasicMaterial).opacity = f.dim ? 0.12 : 0.5;
      ring.geometry.setFromPoints([v3(x0, 0, z0), v3(x1, 0, z0), v3(x1, 0, z0), v3(x1, 0, z1), v3(x1, 0, z1), v3(x0, 0, z1), v3(x0, 0, z1), v3(x0, 0, z0)]);
    }
  }

  /** The roster on the floors; null = unavailable: the default desks stay empty and nobody sits. */
  setAgents(agents: SceneAgent[] | null) {
    const list = agents ?? [];
    const firstRoster = !this.rosterSeen && agents !== null;
    for (const f of this.floors.values()) {
      const here = list.filter(a => a.floor === f.id);
      const active = here.filter(a => a.status === "ACTIVE");
      const counts: Record<Kind, number> = { own: 0, tradingview: 0 };
      active.forEach(a => { counts[a.kind] += 1; });
      // Empty floors keep a default office of three desks per group, unlit.
      const placeholder = active.length === 0;
      const seats = this.layout(placeholder ? { own: 3, tradingview: 3 } : counts);
      f.seats = seats;
      const order: Record<Kind, SceneAgent[]> = { own: active.filter(a => a.kind === "own"), tradingview: active.filter(a => a.kind === "tradingview") };
      const used: Record<Kind, number> = { own: 0, tradingview: 0 };
      const byId = new Map<string, Seat>();
      const lit = seats.map(s => {
        if (placeholder) return false;
        const a = order[s.kind][used[s.kind]++];
        if (a) byId.set(a.id, s);
        return Boolean(a);
      });
      this.placeFurniture(f, seats, lit);
      f.anchors = {};
      const resting = here.filter(a => a.status === "ELIMINATED");
      for (const a of here) {
        let r = this.robots.get(a.id);
        if (!r) {
          r = this.makeRobot(a);
          this.robots.set(a.id, r);
          f.group.add(r.group);
          const s = byId.get(a.id);
          // A replacement that appears after the first read walks in through the door.
          if (!firstRoster && this.rosterSeen && !this.known.has(a.id) && a.status === "ACTIVE" && s && !this.reduced) {
            const to = v3(s.x, 0, s.z - s.face * 0.95);
            r.group.position.copy(DOOR);
            r.walk = { from: DOOR.clone(), mid: v3(to.x, 0, DOOR.z), to, t: 0 };
          }
        }
        this.known.add(a.id);
        const changed = r.status !== a.status || r.kind !== a.kind;
        r.status = a.status; r.kind = a.kind; r.busy = a.busy && a.status === "ACTIVE";
        if (changed) this.paintRobot(r);
        if (a.status === "ACTIVE") {
          const s = byId.get(a.id);
          if (!s) { r.group.visible = false; continue; }
          r.group.visible = true;
          r.target.set(s.x, 0, s.z - s.face * 0.95);
          r.rot = s.face === -1 ? Math.PI : 0;
          r.anchor.set(s.x, 2.55, s.z);
        } else {
          const i = resting.indexOf(a);
          r.walk = null;
          if (i >= 6) { r.group.visible = false; continue; }
          r.group.visible = true;
          const onBack = i < 3;
          const x = onBack ? LOUNGE.x - 1.05 + i * 1.05 : LOUNGE.x + 2.6;
          const z = onBack ? LOUNGE.z - 1.45 : LOUNGE.z + 1.85 - (i - 3) * 1.05;
          r.target.set(x, -0.05, z);
          r.rot = onBack ? 0 : -Math.PI / 2;
          r.anchor.set(x, 2.25, z);
        }
        if (!r.walk) { r.group.position.copy(r.target); }
        r.group.rotation.y = r.rot;
        f.anchors[a.id] = r.anchor;
      }
      f.anchors.lounge = v3(LOUNGE.x + 0.4, 0.1, LOUNGE.z + 3.1);
      f.anchors.label = v3(W / 2 + 0.6, 3.2, -D / 4);
    }
    for (const [id, r] of this.robots) {
      if (!list.some(a => a.id === id)) { r.group.removeFromParent(); this.robots.delete(id); this.scene.remove(r.hit); }
    }
    if (agents !== null) this.rosterSeen = true;
    this.dirty = true;
  }

  private robotMat(kind: Kind | "grey", part: "body" | "accent" | "head" | "eye") {
    if (kind === "grey") return this.mat(part === "accent" ? 0x6b7385 : part === "eye" ? 0x3a4152 : 0x9aa1ad);
    if (part === "accent") return this.mat(KIND_COLOR[kind]);
    if (part === "eye") return this.basic(KIND_COLOR[kind]);
    return this.mat(part === "head" ? 0xf2f5fa : 0xdde3ec);
  }

  private makeRobot(a: SceneAgent): Robot {
    const g = new THREE.Group();
    const S = this.shared;
    const body = new THREE.Group();
    const legL = new THREE.Mesh(S.leg, this.mat(0x3b4459)); legL.position.set(-0.13, 0.62, 0.18);
    const legR = legL.clone(); legR.position.x = 0.13;
    const torso = new THREE.Mesh(S.torso, this.robotMat(a.kind, "body")); torso.position.y = 0.98;
    const stripe = new THREE.Mesh(S.stripe, this.robotMat(a.kind, "accent")); stripe.position.y = 1.06;
    const head = new THREE.Group(); head.position.y = 1.56;
    const skull = new THREE.Mesh(S.head, this.robotMat(a.kind, "head"));
    const visor = new THREE.Mesh(S.visor, this.mat(0x111a2e)); visor.position.set(0, 0, 0.28);
    const eyes = [-0.13, 0.13].map(x => { const e = new THREE.Mesh(S.eye, this.robotMat(a.kind, "eye")); e.position.set(x, 0.01, 0.305); return e; });
    const ant = new THREE.Mesh(S.antenna, this.mat(0x8c96a8)); ant.position.y = 0.38;
    const ball = new THREE.Mesh(S.ball, this.robotMat(a.kind, "accent")); ball.position.y = 0.52;
    head.add(skull, visor, ...eyes, ant, ball);
    const arm = (x: number) => {
      const pivot = new THREE.Group(); pivot.position.set(x, 1.2, 0);
      const m = new THREE.Mesh(S.arm, this.robotMat(a.kind, "body")); m.position.y = -0.2;
      pivot.add(m); return pivot;
    };
    const armL = arm(-0.39), armR = arm(0.39);
    body.add(legL, legR, torso, stripe, head, armL, armR);
    const blob = new THREE.Mesh(S.plane, this.basic(0x0a1020, 0.25)); blob.rotation.x = -Math.PI / 2; blob.scale.set(0.9, 0.9, 1); blob.position.y = 0.02;
    body.scale.setScalar(1.15);
    g.add(body, blob);
    const hit = new THREE.Mesh(S.unit, new THREE.MeshBasicMaterial({ visible: false }));
    hit.scale.set(1.6, 2.4, 1.8); hit.position.set(0, 1.1, 0.4);
    g.add(hit);
    this.disposables.push(hit.material as THREE.Material);
    hit.userData.agent = a.id;
    return { id: a.id, group: g, armL, armR, head, eyes, floor: a.floor, status: a.status, kind: a.kind, busy: false, phase: Math.random() * 6, target: v3(0, 0, 0), rot: 0, walk: null, anchor: v3(0, 0, 0), hit };
  }

  private paintRobot(r: Robot) {
    const k: Kind | "grey" = r.status === "ELIMINATED" ? "grey" : r.kind;
    r.group.traverse(o => {
      if (!(o instanceof THREE.Mesh) || o === r.hit) return;
      const m = o.material as THREE.Material;
      for (const kind of ["own", "tradingview", "grey"] as const) {
        for (const part of ["body", "accent", "head", "eye"] as const) {
          if (m === this.robotMat(kind, part)) { o.material = this.robotMat(k, part); return; }
        }
      }
    });
    r.armL.rotation.x = r.armR.rotation.x = r.status === "ACTIVE" ? -1.15 : 0;
    r.head.rotation.x = r.status === "ACTIVE" ? 0 : 0.25;
  }

  setFloorDim(id: FloorId, dim: boolean) {
    const f = this.floors.get(id);
    if (!f || f.dim === dim) return;
    f.dim = dim;
    for (const [k, m] of Object.entries(f.mats) as [keyof Mats, THREE.MeshLambertMaterial][]) {
      m.color.setHex(BASE[k]);
      if (dim) m.color.lerp(DIM, k === "floor" ? 0.72 : 0.55);
    }
    this.dirty = true;
  }

  setBoards(id: FloorId, result: ResultBoard, limits: LimitBoard) {
    const f = this.floors.get(id);
    if (!f) return;
    const draw = (b: Board, data: unknown, paint: () => void) => {
      const key = JSON.stringify(data);
      if (b.key === key) return;
      b.key = key; paint(); b.tex.needsUpdate = true; this.dirty = true;
    };
    draw(f.result, result, () => drawResult(f.result.g, f.result.c.width, f.result.c.height, result));
    draw(f.limits, limits, () => drawLimits(f.limits.g, f.limits.c.width, f.limits.c.height, limits));
  }

  setView(view: View, instant = false) {
    this.view = view;
    const top = (FLOORS.length - 1) * GAP;
    if (view === "building") { this.goal.target.set(0, top / 2 + 2.6, 0); this.goal.viewH = top + H + 15; }
    else { const f = this.floors.get(view); this.goal.target.set(0.6, (f?.y ?? 0) + 2.1, -0.4); this.goal.viewH = 22.5; }
    for (const f of this.floors.values()) f.group.visible = view === "building" || f.id === view || f.y < (this.floors.get(view as FloorId)?.y ?? 0);
    this.sign.visible = view === "building";
    this.core.visible = view === "building";
    if (instant || this.reduced) { this.target.copy(this.goal.target); this.viewH = this.goal.viewH; }
    this.dirty = true;
  }

  setAnimate(on: boolean) { this.animate = on && !this.reduced; this.dirty = true; }
  setRunning(on: boolean) {
    if (on === this.running) return;
    this.running = on;
    if (on) { this.clock.update(); this.loop(); } else cancelAnimationFrame(this.raf);
  }
  resize(w: number, h: number) {
    this.width = Math.max(1, w); this.height = Math.max(1, h);
    this.renderer.setSize(this.width, this.height, false);
    this.dirty = true;
    if (!this.running) this.render();
  }
  orbit(dx: number, dy: number) {
    this.az = THREE.MathUtils.clamp(this.az - dx * 0.006, AZ_MIN, AZ_MAX);
    this.elev = THREE.MathUtils.clamp(this.elev + dy * 0.004, ELEV_MIN, ELEV_MAX);
    this.dirty = true;
  }
  zoom(f: number) { this.zoomF = THREE.MathUtils.clamp(this.zoomF / f, 0.7, 3); this.dirty = true; }
  resetZoom() { this.zoomF = 1; this.az = 0.64; this.elev = 0.6; this.dirty = true; }

  pick(x: number, y: number): Pick {
    this.ray.setFromCamera(new THREE.Vector2(x, y), this.camera);
    if (this.view !== "building") {
      const hits = [...this.robots.values()].filter(r => r.group.visible && r.floor === this.view).map(r => r.hit);
      const h = this.ray.intersectObjects(hits, false)[0];
      return h ? { type: "agent", id: String(h.object.userData.agent) } : null;
    }
    const slabs = [...this.floors.values()].map(f => f.slab);
    // In the building, any part of a floor picks it: test each floor's whole group.
    let best: { d: number; id: FloorId } | null = null;
    for (const f of this.floors.values()) {
      const h = this.ray.intersectObject(f.group, true)[0];
      if (h && (!best || h.distance < best.d)) best = { d: h.distance, id: f.id };
    }
    void slabs;
    return best ? { type: "floor", id: best.id } : null;
  }

  private loop = () => {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.loop);
    this.clock.update(); const dt = Math.min(this.clock.getDelta(), 0.1);
    const moving = this.step(dt);
    if (moving || this.animate || this.dirty) this.render();
  };

  /** Advances camera easing and characters; true while something still moves. */
  private step(dt: number): boolean {
    let moving = false;
    const k = 1 - Math.exp(-dt * 6);
    if (this.target.distanceTo(this.goal.target) > 0.01 || Math.abs(this.viewH - this.goal.viewH) > 0.01) {
      this.target.lerp(this.goal.target, k); this.viewH += (this.goal.viewH - this.viewH) * k; moving = true;
    }
    const t = performance.now() / 1000;
    for (const r of this.robots.values()) {
      if (!r.group.visible) continue;
      if (r.walk) {
        moving = true;
        r.walk.t = Math.min(1, r.walk.t + dt / 3.2);
        const p = r.walk.t < 0.5 ? r.walk.from.clone().lerp(r.walk.mid, r.walk.t * 2) : r.walk.mid.clone().lerp(r.walk.to, (r.walk.t - 0.5) * 2);
        const next = r.walk.t < 0.5 ? r.walk.mid : r.walk.to;
        r.group.position.set(p.x, Math.abs(Math.sin(t * 10)) * 0.08, p.z);
        r.group.rotation.y = Math.atan2(next.x - p.x, next.z - p.z);
        r.armL.rotation.x = Math.sin(t * 10) * 0.6; r.armR.rotation.x = -Math.sin(t * 10) * 0.6;
        if (r.walk.t >= 1) { r.walk = null; r.group.position.copy(r.target); r.group.rotation.y = r.rot; this.paintRobot(r); }
        continue;
      }
      if (!this.animate) continue;
      if (r.status === "ACTIVE" && r.busy) {
        r.armL.rotation.x = -1.15 + Math.sin(t * 16 + r.phase) * 0.12;
        r.armR.rotation.x = -1.15 + Math.sin(t * 16 + r.phase + 1.7) * 0.12;
        r.head.rotation.y = Math.sin(t * 1.3 + r.phase) * 0.08;
      } else if (r.status === "ACTIVE") {
        r.armL.rotation.x = r.armR.rotation.x = -1.15;
        r.head.rotation.y = Math.sin(t * 0.4 + r.phase) * 0.25;
      } else {
        r.head.rotation.x = 0.25 + Math.sin(t * 0.5 + r.phase) * 0.05;
      }
    }
    return moving;
  }

  private render() {
    this.dirty = false;
    const aspect = this.width / this.height;
    const needW = this.view === "building" ? 38 : 29;
    const h = Math.max(this.viewH, needW / aspect) / this.zoomF;
    const cam = this.camera;
    cam.left = (-h * aspect) / 2; cam.right = (h * aspect) / 2; cam.top = h / 2; cam.bottom = -h / 2;
    const dir = v3(Math.sin(this.az) * Math.cos(this.elev), Math.sin(this.elev), Math.cos(this.az) * Math.cos(this.elev));
    cam.position.copy(this.target).addScaledVector(dir, 120);
    cam.lookAt(this.target);
    cam.updateProjectionMatrix();
    this.renderer.render(this.scene, cam);
    if (this.first) { this.first = false; this.onFirstFrame?.(); }
    if (this.onAnchors) {
      const out: Record<string, Anchor> = {};
      const p = new THREE.Vector3();
      for (const f of this.floors.values()) {
        const show = f.group.visible;
        const inFloor = this.view === f.id;
        for (const [id, a] of Object.entries(f.anchors)) {
          const isLabel = id === "label";
          p.copy(a); p.y += f.y;
          if (!isLabel && this.robots.has(id)) p.copy(this.robots.get(id)!.group.position).setY(a.y + f.y);
          p.project(cam);
          const visible = show && (isLabel ? this.view === "building" : inFloor) && Math.abs(p.x) < 1.05 && Math.abs(p.y) < 1.05;
          out[`${f.id}:${id}`] = { x: ((p.x + 1) / 2) * this.width, y: ((1 - p.y) / 2) * this.height, visible };
        }
      }
      this.onAnchors(out);
    }
  }

  dispose() {
    this.setRunning(false);
    this.disposables.forEach(d => d.dispose());
    this.robotMats.forEach(m => m.dispose());
    this.floors.forEach(f => f.slab.geometry.dispose());
    this.scene.traverse(o => { if (o instanceof THREE.Mesh && !Object.values(this.shared).includes(o.geometry)) o.geometry.dispose(); });
    this.renderer.dispose();
  }
}
