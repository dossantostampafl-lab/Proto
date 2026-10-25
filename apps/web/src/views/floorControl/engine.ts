// "Controle": a top-down mission-control room of the trading floor. A holographic globe in a sunken
// ring of consoles at the centre, glowing rings on a glossy floor, desk blocks behind glass
// partitions with amber light strips (Binance testnet left and right, XP / B3 demo bottom left) and
// one robot per agent of the roster. Screens glow but carry no numbers: every value on this view is
// drawn by the React overlay from the API. The engine draws only what it is given: the agents, their
// floor and whether a pipeline station is working on them right now.
import * as THREE from "three";
import { canvas } from "../floorBuilding/boards";

export type ControlAgent = { id: string; floor: "binance" | "xp"; active: boolean; busy: boolean };
export type Anchor = { x: number; y: number; visible: boolean };

const BG = 0x050b18;
const AMBER = 0xf59e0b;
const CYAN = 0x38bdf8;

// Room: x in [-32, 32] (left to right), z in [-22, 22] (far to near). The overlay maps 32 px per unit.
const W = 64, D = 44, S = 32;
const px = (x: number, z: number): [number, number] => [(x + W / 2) * S, (z + D / 2) * S];

// Desk blocks: pairs of facing desk rows. Binance on both sides, the XP / B3 demo bottom left.
type Block = { x: number; z: number; cols: number; pairs: number; floor: "binance" | "xp" };
const BLOCKS: Block[] = [
  { x: -23, z: -12.5, cols: 4, pairs: 2, floor: "binance" },
  { x: 23, z: -12.5, cols: 4, pairs: 2, floor: "binance" },
  { x: -23, z: 2.2, cols: 4, pairs: 1, floor: "binance" },
  { x: 23, z: 2.2, cols: 4, pairs: 1, floor: "binance" },
  { x: -23, z: 14.5, cols: 4, pairs: 1, floor: "xp" },
];
// Glass partitions [x1, z1, x2, z2] with an amber strip on top.
const WALLS: [number, number, number, number][] = [
  [-15, -21, -15, -6], [15, -21, 15, -6], [-15, 6, -15, 20], [15, 6, 15, 20],
  [-31, -3.5, -15, -3.5], [15, -3.5, 31, -3.5], [-31, 8, -15, 8], [15, 8, 31, 8],
  [-12, 19.5, -4, 19.5], [4, 19.5, 12, 19.5],
];
const PLANTS: [number, number][] = [[-15, -5], [15, -5], [-15, 5], [15, 5], [-14, 20.5], [14, 20.5], [-9, -17], [9, -17], [-30.5, -20.5], [30.5, -20.5], [30, 20], [-4.5, 18.5], [4.5, 18.5]];

// Very rough continent outlines (longitude, latitude), enough to read as Earth on a small globe.
const LAND: [number, number][][] = [
  [[-168, 66], [-140, 70], [-95, 72], [-75, 62], [-60, 50], [-80, 30], [-97, 18], [-83, 9], [-105, 22], [-118, 33], [-125, 48], [-150, 60]],
  [[-80, 10], [-62, 11], [-35, -6], [-40, -22], [-58, -38], [-70, -55], [-75, -40], [-70, -18], [-81, -5]],
  [[-18, 15], [-5, 36], [12, 37], [32, 31], [43, 12], [51, 11], [40, -15], [30, -34], [18, -34], [12, -16], [9, 4], [-8, 5]],
  [[-10, 36], [-9, 44], [0, 51], [10, 58], [25, 70], [60, 70], [100, 77], [140, 72], [175, 66], [160, 58], [140, 50], [122, 40], [122, 25], [105, 10], [98, 18], [80, 8], [72, 20], [57, 25], [48, 30], [35, 36], [25, 40], [12, 44]],
  [[113, -22], [122, -14], [136, -12], [145, -15], [153, -27], [146, -39], [135, -35], [115, -34]],
  [[-50, 60], [-30, 70], [-22, 82], [-55, 82], [-70, 76]],
];

function texture(c: OffscreenCanvas, srgb = true): THREE.Texture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

function globeTexture(): THREE.Texture {
  const { c, g } = canvas(2048, 1024);
  const sea = g.createLinearGradient(0, 0, 0, c.height);
  sea.addColorStop(0, "#0b3a7a"); sea.addColorStop(0.5, "#0e5bb5"); sea.addColorStop(1, "#0b3a7a");
  g.fillStyle = sea; g.fillRect(0, 0, c.width, c.height);
  const toXY = (lon: number, lat: number): [number, number] => [((lon + 180) / 360) * c.width, ((90 - lat) / 180) * c.height];
  for (const shape of LAND) {
    g.beginPath();
    shape.forEach(([lon, lat], i) => { const [x, y] = toXY(lon, lat); if (i) g.lineTo(x, y); else g.moveTo(x, y); });
    g.closePath();
    const land = g.createLinearGradient(0, 0, c.width, c.height);
    land.addColorStop(0, "#16a34a"); land.addColorStop(0.5, "#4ade80"); land.addColorStop(1, "#15803d");
    g.fillStyle = land; g.shadowColor = "#a7f3d0"; g.shadowBlur = 24; g.fill();
    g.shadowBlur = 0; g.strokeStyle = "rgba(187,247,208,0.7)"; g.lineWidth = 2; g.stroke();
  }
  g.strokeStyle = "rgba(186,230,253,0.18)"; g.lineWidth = 1.5;
  for (let lon = -180; lon <= 180; lon += 15) { const [x] = toXY(lon, 0); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, c.height); g.stroke(); }
  for (let lat = -75; lat <= 75; lat += 15) { const [, y] = toXY(0, lat); g.beginPath(); g.moveTo(0, y); g.lineTo(c.width, y); g.stroke(); }
  return texture(c);
}

function radial(size: number, stops: [number, string][]): THREE.Texture {
  const { c, g } = canvas(size, size);
  const grd = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [at, color] of stops) grd.addColorStop(at, color);
  g.fillStyle = grd; g.fillRect(0, 0, size, size);
  return texture(c);
}

function floorTexture(): THREE.Texture {
  const { c, g } = canvas(512, 512);
  g.fillStyle = "#0c1c3a"; g.fillRect(0, 0, 512, 512);
  g.strokeStyle = "rgba(147,197,253,0.16)"; g.lineWidth = 3; g.strokeRect(0, 0, 512, 512);
  g.strokeStyle = "rgba(147,197,253,0.07)"; g.lineWidth = 2; g.strokeRect(256, 0, 0.1, 512); g.strokeRect(0, 256, 512, 0.1);
  const t = texture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(W / 4, D / 4);
  return t;
}

/** Everything that glows on the floor, painted once: light pools, rings, walkways and strip spill. */
function glowTexture(): THREE.Texture {
  const { c, g } = canvas(W * S, D * S);
  const [cx, cy] = px(0, 0);
  const pool = (x: number, y: number, r: number, inner: string) => {
    const grd = g.createRadialGradient(x, y, 0, x, y, r);
    grd.addColorStop(0, inner); grd.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grd; g.fillRect(x - r, y - r, 2 * r, 2 * r);
  };
  pool(cx, cy, 17 * S, "rgba(59,130,246,0.55)");
  for (const b of BLOCKS) { const [x, y] = px(b.x, b.z); pool(x, y, 9 * S, "rgba(96,165,250,0.18)"); }
  const ring = (r: number, w: number, color: string, blur: number, dash?: number[]) => {
    g.save(); g.beginPath(); g.arc(cx, cy, r * S, 0, Math.PI * 2);
    g.lineWidth = w * S; g.strokeStyle = color; g.shadowColor = color; g.shadowBlur = blur;
    if (dash) g.setLineDash(dash.map(d => d * S));
    g.stroke(); g.stroke(); g.restore();
  };
  ring(7.1, 0.14, "rgba(165,243,252,1)", 40);
  ring(9.0, 0.32, "rgba(96,165,250,0.95)", 70);
  ring(9.8, 0.06, "rgba(191,219,254,0.8)", 18);
  ring(11.0, 0.1, "rgba(125,211,252,0.8)", 16, [0.7, 1.1]);
  ring(13.2, 0.18, "rgba(96,165,250,0.95)", 60);
  ring(14.0, 0.05, "rgba(191,219,254,0.6)", 12);
  // Walkways from the hub to the four sides, dashed light lines.
  g.save(); g.strokeStyle = "rgba(147,197,253,0.45)"; g.lineWidth = 0.08 * S; g.setLineDash([0.5 * S, 0.8 * S]); g.shadowColor = "#60a5fa"; g.shadowBlur = 10;
  for (const [x1, z1, x2, z2] of [[-14, 0, -31, 0], [14, 0, 31, 0], [0, -14, 0, -21], [0, 14, 0, 21]]) {
    const [a, b] = px(x1, z1), [e, f] = px(x2, z2); g.beginPath(); g.moveTo(a, b); g.lineTo(e, f); g.stroke();
  }
  g.restore();
  // Warm spill under every amber strip.
  g.save(); g.strokeStyle = "rgba(245,158,11,0.35)"; g.lineCap = "round"; g.shadowColor = "#f59e0b"; g.shadowBlur = 28; g.lineWidth = 0.22 * S;
  for (const [x1, z1, x2, z2] of WALLS) { const [a, b] = px(x1, z1), [e, f] = px(x2, z2); g.beginPath(); g.moveTo(a, b); g.lineTo(e, f); g.stroke(); }
  g.restore();
  return texture(c);
}

/** A rotating ring of bright arc segments, laid flat around the hub. */
function arcsTexture(): THREE.Texture {
  const size = 1024, { c, g } = canvas(size, size), m = size / 2;
  g.save(); g.translate(m, m);
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2;
    g.beginPath(); g.arc(0, 0, m * 0.9, a, a + 0.55);
    g.lineWidth = 14; g.strokeStyle = "rgba(186,230,253,0.95)"; g.shadowColor = "#38bdf8"; g.shadowBlur = 30; g.stroke();
  }
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * Math.PI * 2;
    g.beginPath(); g.moveTo(Math.cos(a) * m * 0.8, Math.sin(a) * m * 0.8); g.lineTo(Math.cos(a) * m * 0.84, Math.sin(a) * m * 0.84);
    g.lineWidth = 4; g.strokeStyle = "rgba(147,197,253,0.6)"; g.shadowBlur = 0; g.stroke();
  }
  g.restore();
  return texture(c);
}

/** A glowing screen without data: panels and bars only, so no number is ever invented. */
function screenTexture(seed: number): THREE.Texture {
  const { c, g } = canvas(256, 160);
  const bg = g.createLinearGradient(0, 0, 256, 160);
  bg.addColorStop(0, "#1e40af"); bg.addColorStop(1, "#0c4a6e");
  g.fillStyle = bg; g.fillRect(0, 0, 256, 160);
  g.fillStyle = "rgba(186,230,253,0.9)"; g.fillRect(0, 0, 256, 14);
  const r = (n: number) => ((Math.sin(seed * 97.1 + n * 13.7) + 1) / 2);
  for (let i = 0; i < 4; i++) {
    g.fillStyle = `rgba(${i % 2 ? "125,211,252" : "191,219,254"},${0.35 + r(i) * 0.4})`;
    g.fillRect(10 + (i % 2) * 124, 24 + Math.floor(i / 2) * 66, 112, 56);
  }
  g.fillStyle = "rgba(255,255,255,0.18)"; g.fillRect(0, 0, 256, 160 * 0.45);
  return texture(c);
}

type Seat = { at: THREE.Vector3; face: number; floor: "binance" | "xp" };
type Bot = { group: THREE.Group; body: THREE.Mesh; seat: Seat; target: THREE.Vector3; t: number; busy: boolean; active: boolean; path: THREE.Line; marker: THREE.Mesh };

export class ControlEngine {
  onFirstFrame: (() => void) | null = null;
  onAnchors: ((anchors: Record<string, Anchor>) => void) | null = null;
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(34, 1, 0.5, 300);
  private disposables: { dispose: () => void }[] = [];
  private globe: THREE.Mesh;
  private grid: THREE.Mesh;
  private arcs: THREE.Mesh;
  private halo: THREE.Sprite;
  private seats: Seat[] = [];
  private bots = new Map<string, Bot>();
  private geo: { body: THREE.BufferGeometry; legs: THREE.BufferGeometry; visor: THREE.BufferGeometry; antenna: THREE.BufferGeometry; marker: THREE.BufferGeometry };
  private mat: { body: THREE.Material; off: THREE.Material; legs: THREE.Material; visor: THREE.Material; path: THREE.LineDashedMaterial; marker: THREE.Material };
  private running = true;
  private animate = true;
  private phase = 0;
  private first = true;
  private width = 1;
  private height = 1;
  private zoomLevel = 1;
  private yaw = 0;
  private clock = new THREE.Clock();

  constructor(canvasEl: HTMLCanvasElement, private reduced: boolean) {
    this.renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.setClearColor(BG);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.animate = !reduced;
    const own = <T extends { dispose: () => void }>(x: T): T => { this.disposables.push(x); return x; };
    const add = <T extends THREE.Object3D>(o: T): T => { this.scene.add(o); return o; };

    add(new THREE.HemisphereLight(0xbfdbfe, 0x1e3a8a, 1.15));
    const key = new THREE.DirectionalLight(0xdbeafe, 1.1); key.position.set(-8, 30, 12); add(key);
    const hub = new THREE.PointLight(0x60a5fa, 60, 30, 1.2); hub.position.set(0, 6, 0); add(hub);

    // Glossy floor, then the painted glow layer on top of it.
    const floor = add(new THREE.Mesh(own(new THREE.PlaneGeometry(W, D)), own(new THREE.MeshPhongMaterial({ map: own(floorTexture()), shininess: 80, specular: 0x3b5b8f }))));
    floor.rotation.x = -Math.PI / 2;
    const glow = add(new THREE.Mesh(own(new THREE.PlaneGeometry(W, D)), own(new THREE.MeshBasicMaterial({ map: own(glowTexture()), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }))));
    glow.rotation.x = -Math.PI / 2; glow.position.y = 0.03;
    this.arcs = add(new THREE.Mesh(own(new THREE.PlaneGeometry(23, 23)), own(new THREE.MeshBasicMaterial({ map: own(arcsTexture()), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }))));
    this.arcs.rotation.x = -Math.PI / 2; this.arcs.position.y = 0.05;
    // The sunken hub: a darker well inside the first ring.
    const well = add(new THREE.Mesh(own(new THREE.CircleGeometry(7, 72)), own(new THREE.MeshPhongMaterial({ color: 0x0a1a38, shininess: 90, specular: 0x2b4c80 }))));
    well.rotation.x = -Math.PI / 2; well.position.y = 0.02;
    const rim = add(new THREE.Mesh(own(new THREE.TorusGeometry(7, 0.18, 8, 96)), own(new THREE.MeshLambertMaterial({ color: 0x1e3a6e }))));
    rim.rotation.x = Math.PI / 2; rim.position.y = 0.15;

    // Instanced furniture: one draw call per kind of part.
    const batches = new Map<string, { geo: THREE.BufferGeometry; mat: THREE.Material; m: THREE.Matrix4[] }>();
    const dummy = new THREE.Object3D(); dummy.rotation.order = "YXZ";
    const put = (key: string, geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, ry = 0, rx = 0) => {
      dummy.position.set(x, y, z); dummy.rotation.set(rx, ry, 0); dummy.scale.set(1, 1, 1); dummy.updateMatrix();
      let b = batches.get(key); if (!b) { b = { geo, mat, m: [] }; batches.set(key, b); }
      b.m.push(dummy.matrix.clone());
    };
    const G = {
      desk: own(new THREE.BoxGeometry(2.3, 0.12, 1.05)), leg: own(new THREE.BoxGeometry(2.2, 0.62, 0.9)),
      mon: own(new THREE.BoxGeometry(0.95, 0.58, 0.05)), scr: own(new THREE.PlaneGeometry(0.88, 0.5)), stand: own(new THREE.BoxGeometry(0.08, 0.35, 0.08)),
      seat: own(new THREE.BoxGeometry(0.62, 0.12, 0.6)), back: own(new THREE.BoxGeometry(0.62, 0.55, 0.1)), base: own(new THREE.CylinderGeometry(0.06, 0.25, 0.42, 8)),
      console: own(new THREE.BoxGeometry(1.7, 0.85, 0.75)), cscr: own(new THREE.PlaneGeometry(1.5, 0.62)),
      pot: own(new THREE.CylinderGeometry(0.42, 0.32, 0.7, 14)), leaf: own(new THREE.ConeGeometry(0.42, 1.3, 6)),
      glass: own(new THREE.BoxGeometry(1, 1.5, 0.1)), strip: own(new THREE.BoxGeometry(1, 0.07, 0.16)),
      sofa: own(new THREE.BoxGeometry(3.2, 0.55, 1.1)), table: own(new THREE.CylinderGeometry(0.7, 0.7, 0.4, 20)),
      wall: own(new THREE.BoxGeometry(1, 1.4, 0.4)),
    };
    const M = {
      desk: own(new THREE.MeshPhongMaterial({ color: 0x475569, shininess: 50 })), leg: own(new THREE.MeshLambertMaterial({ color: 0x1e293b })),
      mon: own(new THREE.MeshLambertMaterial({ color: 0x0f172a })), dark: own(new THREE.MeshLambertMaterial({ color: 0x111827 })),
      scr: [0, 1, 2].map(i => own(new THREE.MeshBasicMaterial({ map: own(screenTexture(i + 1)) }))),
      console: own(new THREE.MeshPhongMaterial({ color: 0x334155, shininess: 60 })),
      pot: own(new THREE.MeshLambertMaterial({ color: 0xe5e7eb })), leaf: own(new THREE.MeshLambertMaterial({ color: 0x15803d })), leaf2: own(new THREE.MeshLambertMaterial({ color: 0x22c55e })),
      glass: own(new THREE.MeshPhongMaterial({ color: 0x93c5fd, transparent: true, opacity: 0.16, shininess: 100, depthWrite: false })),
      strip: own(new THREE.MeshBasicMaterial({ color: AMBER })), sofa: own(new THREE.MeshLambertMaterial({ color: 0x374151 })),
      wall: own(new THREE.MeshLambertMaterial({ color: 0x0b1424 })), cyan: own(new THREE.MeshBasicMaterial({ color: CYAN })),
    };

    // Desk blocks. A pair is two facing rows sharing a monitor spine; a seat is where an agent stands.
    const blockSeats: Seat[][] = [];
    for (const b of BLOCKS) {
      const seats: Seat[] = [];
      for (let p = 0; p < b.pairs; p++) {
        const zc = b.z + (p - (b.pairs - 1) / 2) * 6.2;
        for (let i = 0; i < b.cols; i++) {
          const x = b.x + (i - (b.cols - 1) / 2) * 2.45;
          for (const side of [-1, 1]) {
            const dz = zc + side * 0.6, face = side < 0 ? 0 : Math.PI; // the seat looks at the spine
            put("desk", G.desk, M.desk, x, 0.74, dz); put("leg", G.leg, M.leg, x, 0.37, dz);
            for (const k of [-0.5, 0.5]) {
              const mx = x + k, mz = zc + side * 0.12;
              // Screens face their seat, tilted back so the high camera sees them glow.
              const ry = side > 0 ? 0 : Math.PI;
              put("mon", G.mon, M.mon, mx, 1.22, mz, ry, -0.45); put("stand", G.stand, M.mon, mx, 0.95, mz);
              const s = (i + p + (side > 0 ? 1 : 0) + (k > 0 ? 1 : 0)) % 3;
              put(`scr${s}`, G.scr, M.scr[s], mx, 1.23, mz + side * 0.04, ry, -0.45);
            }
            const cz = zc + side * 1.55;
            put("seat", G.seat, M.dark, x, 0.5, cz); put("back", G.back, M.dark, x, 0.82, cz + side * 0.3); put("base", G.base, M.dark, x, 0.21, cz);
            seats.push({ at: new THREE.Vector3(x, 0, zc + side * 2.2), face: face + Math.PI, floor: b.floor });
          }
        }
      }
      // Spread a block's seats: first, last, then the middle ones.
      seats.sort((a, c) => Math.abs(a.at.x - b.x) - Math.abs(c.at.x - b.x)).reverse();
      blockSeats.push(seats);
    }
    // Seat order alternates blocks (left, right, ...), so a small roster fills the room evenly.
    for (let round = 0; blockSeats.some(s => s.length > round); round++) for (const s of blockSeats) if (s[round]) this.seats.push(s[round]);

    // Ring of consoles in the hub, screens towards the globe; the entrance faces the camera.
    const n = 26;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      if (Math.abs(a - Math.PI / 2) < 0.3) continue;
      const x = Math.cos(a) * 5.7, z = Math.sin(a) * 5.7, ry = Math.atan2(-x, -z);
      put("console", G.console, M.console, x, 0.42, z, ry);
      put(`cscr${i % 3}`, G.cscr, M.scr[i % 3], Math.cos(a) * 5.3, 0.9, Math.sin(a) * 5.3, ry, -1.05);
    }
    // The curved command desk beyond the hub, facing it.
    for (let i = 0; i <= 10; i++) {
      const a = -Math.PI / 2 + (i / 10 - 0.5) * 1.15, x = Math.cos(a) * 16.2, z = Math.sin(a) * 16.2, ry = Math.atan2(-x, -z);
      put("console", G.console, M.console, x, 0.42, z, ry);
      put(`cscr${i % 3}`, G.cscr, M.scr[i % 3], Math.cos(a) * 15.8, 1.1, Math.sin(a) * 15.8, ry, -0.35);
      put("mon", G.mon, M.mon, Math.cos(a) * 16.5, 1.35, Math.sin(a) * 16.5, ry);
    }
    // Glass partitions with amber strips, and the room's outer wall with a cyan line.
    for (const [x1, z1, x2, z2] of WALLS) {
      const len = Math.hypot(x2 - x1, z2 - z1), ry = -Math.atan2(z2 - z1, x2 - x1);
      dummy.position.set((x1 + x2) / 2, 0.75, (z1 + z2) / 2); dummy.rotation.set(0, ry, 0); dummy.scale.set(len, 1, 1); dummy.updateMatrix();
      const glass = batches.get("glass") ?? { geo: G.glass, mat: M.glass, m: [] }; glass.m.push(dummy.matrix.clone()); batches.set("glass", glass);
      dummy.position.y = 1.52; dummy.updateMatrix();
      const strip = batches.get("strip") ?? { geo: G.strip, mat: M.strip, m: [] }; strip.m.push(dummy.matrix.clone()); batches.set("strip", strip);
    }
    for (const [x1, z1, x2, z2] of [[-32, -22, 32, -22], [-32, 22, 32, 22], [-32, -22, -32, 22], [32, -22, 32, 22]]) {
      const len = Math.hypot(x2 - x1, z2 - z1), ry = -Math.atan2(z2 - z1, x2 - x1);
      dummy.position.set((x1 + x2) / 2, 0.7, (z1 + z2) / 2); dummy.rotation.set(0, ry, 0); dummy.scale.set(len, 1, 1); dummy.updateMatrix();
      const wall = batches.get("wall") ?? { geo: G.wall, mat: M.wall, m: [] }; wall.m.push(dummy.matrix.clone()); batches.set("wall", wall);
    }
    // Plants, and a lounge corner bottom right.
    for (const [x, z] of PLANTS) {
      put("pot", G.pot, M.pot, x, 0.35, z);
      for (let k = 0; k < 5; k++) { const a = (k / 5) * Math.PI * 2; put(k % 2 ? "leaf" : "leaf2", G.leaf, k % 2 ? M.leaf : M.leaf2, x + Math.cos(a) * 0.3, 1.2, z + Math.sin(a) * 0.3, a, 0.5); }
    }
    put("sofa", G.sofa, M.sofa, 22, 0.28, 12); put("sofa", G.sofa, M.sofa, 22, 0.28, 17.5); put("sofa", G.sofa, M.sofa, 18.2, 0.28, 14.8, Math.PI / 2);
    put("table", G.table, M.desk, 22, 0.2, 14.8);
    for (const [key, b] of batches) {
      const mesh = new THREE.InstancedMesh(b.geo, b.mat, b.m.length);
      b.m.forEach((m, i) => mesh.setMatrixAt(i, m));
      mesh.name = key; add(mesh);
    }

    // The globe: earth, a counter-rotating grid shell, a halo and a light column.
    this.globe = add(new THREE.Mesh(own(new THREE.SphereGeometry(3.3, 64, 40)), own(new THREE.MeshBasicMaterial({ map: own(globeTexture()) }))));
    this.globe.position.y = 3.6; this.globe.rotation.z = 0.35;
    this.grid = add(new THREE.Mesh(own(new THREE.SphereGeometry(3.55, 24, 16)), own(new THREE.MeshBasicMaterial({ color: 0x7dd3fc, wireframe: true, transparent: true, opacity: 0.06, blending: THREE.AdditiveBlending, depthWrite: false }))));
    this.grid.position.copy(this.globe.position);
    this.halo = add(new THREE.Sprite(own(new THREE.SpriteMaterial({ map: own(radial(256, [[0, "rgba(125,211,252,0.7)"], [0.35, "rgba(56,189,248,0.35)"], [1, "rgba(0,0,0,0)"]])), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }))));
    this.halo.position.copy(this.globe.position); this.halo.scale.set(13, 13, 1);
    const beam = add(new THREE.Mesh(own(new THREE.CylinderGeometry(2.2, 3.2, 3.2, 40, 1, true)), own(new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.08, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false }))));
    beam.position.y = 1.6;

    // Robot parts, shared by every agent (PROTO's own robots, not any known character).
    this.geo = {
      body: own(new THREE.CapsuleGeometry(0.42, 0.5, 6, 14)),
      legs: own(new THREE.CylinderGeometry(0.4, 0.36, 0.35, 14)),
      visor: own(new THREE.TorusGeometry(0.43, 0.07, 8, 24)),
      antenna: own(new THREE.SphereGeometry(0.09, 8, 8)),
      marker: own(new THREE.RingGeometry(0.3, 0.48, 28)),
    };
    this.mat = {
      body: own(new THREE.MeshPhongMaterial({ color: 0xfacc15, shininess: 70 })),
      off: own(new THREE.MeshLambertMaterial({ color: 0x64748b })),
      legs: own(new THREE.MeshLambertMaterial({ color: 0x1d4ed8 })),
      visor: M.cyan,
      path: own(new THREE.LineDashedMaterial({ color: AMBER, dashSize: 0.5, gapSize: 0.3 })),
      marker: own(new THREE.MeshBasicMaterial({ color: CYAN, side: THREE.DoubleSide })),
    };
    this.renderer.setAnimationLoop(() => this.tick());
  }

  setAgents(list: ControlAgent[] | null) {
    const wanted = new Set((list ?? []).map(a => a.id));
    for (const [id, bot] of this.bots) if (!wanted.has(id)) { this.scene.remove(bot.group, bot.path, bot.marker); bot.path.geometry.dispose(); this.bots.delete(id); }
    const taken = new Set([...this.bots.values()].map(b => b.seat));
    for (const a of list ?? []) {
      let bot = this.bots.get(a.id);
      if (!bot) {
        const seat = this.seats.find(s => s.floor === a.floor && !taken.has(s));
        if (!seat) continue; // more agents than seats on this floor: the legend still lists them
        taken.add(seat);
        const group = new THREE.Group();
        const legs = new THREE.Mesh(this.geo.legs, this.mat.legs); legs.position.y = 0.18; group.add(legs);
        const body = new THREE.Mesh(this.geo.body, this.mat.body); body.position.y = 0.82; group.add(body);
        const visor = new THREE.Mesh(this.geo.visor, this.mat.visor); visor.position.y = 1.05; visor.rotation.x = Math.PI / 2; group.add(visor);
        const ant = new THREE.Mesh(this.geo.antenna, this.mat.visor); ant.position.y = 1.62; group.add(ant);
        group.scale.setScalar(1.6); group.position.copy(seat.at); group.rotation.y = seat.face;
        // The hub console this agent walks to while a pipeline station works on its symbol.
        const angle = Math.atan2(seat.at.z, seat.at.x);
        const target = new THREE.Vector3(Math.cos(angle) * 8, 0, Math.sin(angle) * 8);
        const path = new THREE.Line(new THREE.BufferGeometry().setFromPoints([seat.at.clone().setY(0.08), target.clone().setY(0.08)]), this.mat.path);
        path.computeLineDistances();
        const marker = new THREE.Mesh(this.geo.marker, this.mat.marker);
        marker.rotation.x = -Math.PI / 2; marker.position.copy(target).setY(0.09);
        this.scene.add(group, path, marker);
        bot = { group, body, seat, target, t: 0, busy: false, active: true, path, marker };
        this.bots.set(a.id, bot);
      }
      bot.busy = a.busy && a.active;
      bot.active = a.active;
      bot.body.material = a.active ? this.mat.body : this.mat.off;
      bot.path.visible = bot.marker.visible = bot.busy;
    }
  }

  setAnimate(on: boolean) { this.animate = on && !this.reduced; }
  setRunning(on: boolean) { this.running = on; if (on) this.clock.getDelta(); }

  resize(w: number, h: number) {
    this.width = Math.max(1, w); this.height = Math.max(1, h);
    this.renderer.setSize(this.width, this.height, false);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
  }

  zoom(factor: number) { this.zoomLevel = Math.min(1.6, Math.max(0.45, this.zoomLevel * factor)); }
  orbit(dx: number) { this.yaw = Math.max(-0.5, Math.min(0.5, this.yaw - dx * 0.004)); }

  private placeCamera() {
    // High, steep view; narrow screens back off so the hub and both desk sides stay in frame.
    const aspect = this.width / this.height;
    const fit = aspect < 1 ? 1.35 : aspect < 1.4 ? 1.15 : 1;
    const dist = 70 * this.zoomLevel * fit;
    // On portrait screens the header and totals sit on top of the room, so aim past the hub to
    // bring the globe down into the free space below them.
    const lookZ = aspect < 1 ? -9 : 1.5;
    this.camera.position.set(Math.sin(this.yaw) * dist * 0.35, dist * 0.93, Math.cos(this.yaw) * dist * 0.35 + lookZ + 0.5);
    this.camera.lookAt(0, 0, lookZ);
  }

  private tick() {
    if (!this.running) return;
    const dt = Math.min(this.clock.getDelta(), 0.1);
    if (this.animate) {
      this.phase += dt;
      this.globe.rotation.y += dt * 0.15;
      this.grid.rotation.y -= dt * 0.05;
      this.arcs.rotation.z += dt * 0.08;
      const pulse = 12.5 + Math.sin(this.phase * 1.5) * 0.5;
      this.halo.scale.set(pulse, pulse, 1);
    }
    for (const bot of this.bots.values()) {
      // Busy agents walk to their hub console and back; with motion off they jump there.
      const step = this.animate ? dt * 0.22 : 1;
      bot.t = Math.min(1, Math.max(0, bot.t + (bot.busy ? step : -step)));
      bot.group.position.lerpVectors(bot.seat.at, bot.target, bot.t);
      const walking = bot.t > 0 && bot.t < 1;
      bot.group.position.y = bot.active && this.animate ? Math.abs(Math.sin(this.phase * 7)) * (walking ? 0.14 : 0.03) : 0;
      if (walking || bot.t === 1) bot.group.lookAt(bot.busy ? bot.target.x : bot.seat.at.x, bot.group.position.y, bot.busy ? bot.target.z : bot.seat.at.z);
      else bot.group.rotation.set(0, bot.seat.face, 0);
    }
    this.placeCamera();
    this.renderer.render(this.scene, this.camera);
    if (this.onAnchors) {
      const out: Record<string, Anchor> = {};
      const v = new THREE.Vector3();
      for (const [id, bot] of this.bots) {
        v.copy(bot.group.position).setY(bot.group.position.y + 2).project(this.camera);
        out[id] = { x: ((v.x + 1) / 2) * this.width, y: ((1 - v.y) / 2) * this.height, visible: v.z < 1 };
      }
      this.onAnchors(out);
    }
    if (this.first) { this.first = false; this.onFirstFrame?.(); }
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    for (const bot of this.bots.values()) bot.path.geometry.dispose();
    this.scene.traverse(o => { if (o instanceof THREE.InstancedMesh) o.dispose(); });
    this.disposables.forEach(d => d.dispose());
    this.renderer.dispose();
  }
}
