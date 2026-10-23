// "Controle": a top-down control room of the trading floor. A holographic globe in a ring of
// consoles at the centre, desk islands around it (Binance testnet left and right, XP / B3 demo at
// the bottom) and one robot per agent of the roster. The engine draws only what it is given: the
// agents, their floor and whether a pipeline station is working on them right now.
import * as THREE from "three";
import { canvas } from "../floorBuilding/boards";

export type ControlAgent = { id: string; floor: "binance" | "xp"; active: boolean; busy: boolean };
export type Anchor = { x: number; y: number; visible: boolean };

const NAVY = 0x0a1630;
const BLUE = 0x3b82f6;
const CYAN = 0x38bdf8;
const AMBER = 0xf59e0b;

// Very rough continent outlines (longitude, latitude), enough to read as Earth on a small globe.
const LAND: [number, number][][] = [
  [[-168, 66], [-140, 70], [-95, 72], [-75, 62], [-60, 50], [-80, 30], [-97, 18], [-83, 9], [-105, 22], [-118, 33], [-125, 48], [-150, 60]],
  [[-80, 10], [-62, 11], [-35, -6], [-40, -22], [-58, -38], [-70, -55], [-75, -40], [-70, -18], [-81, -5]],
  [[-18, 15], [-5, 36], [12, 37], [32, 31], [43, 12], [51, 11], [40, -15], [30, -34], [18, -34], [12, -16], [9, 4], [-8, 5]],
  [[-10, 36], [-9, 44], [0, 51], [10, 58], [25, 70], [60, 70], [100, 77], [140, 72], [175, 66], [160, 58], [140, 50], [122, 40], [122, 25], [105, 10], [98, 18], [80, 8], [72, 20], [57, 25], [48, 30], [35, 36], [25, 40], [12, 44]],
  [[113, -22], [122, -14], [136, -12], [145, -15], [153, -27], [146, -39], [135, -35], [115, -34]],
  [[-50, 60], [-30, 70], [-22, 82], [-55, 82], [-70, 76]],
];

function globeTexture(): THREE.Texture {
  const { c, g } = canvas(1024, 512);
  g.fillStyle = "#06213f"; g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = "rgba(125,211,252,0.25)"; g.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 20) { const x = ((lon + 180) / 360) * c.width; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, c.height); g.stroke(); }
  for (let lat = -80; lat <= 80; lat += 20) { const y = ((90 - lat) / 180) * c.height; g.beginPath(); g.moveTo(0, y); g.lineTo(c.width, y); g.stroke(); }
  for (const shape of LAND) {
    g.beginPath();
    shape.forEach(([lon, lat], i) => { const x = ((lon + 180) / 360) * c.width, y = ((90 - lat) / 180) * c.height; if (i) g.lineTo(x, y); else g.moveTo(x, y); });
    g.closePath();
    g.fillStyle = "rgba(74,222,128,0.55)"; g.fill();
    g.strokeStyle = "rgba(186,230,253,0.9)"; g.lineWidth = 2; g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function floorTexture(): THREE.Texture {
  const { c, g } = canvas(256, 256);
  g.fillStyle = "#0b1a33"; g.fillRect(0, 0, 256, 256);
  g.strokeStyle = "rgba(59,130,246,0.18)"; g.lineWidth = 2; g.strokeRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(16, 12);
  return t;
}

function labelTexture(text: string): THREE.Texture {
  const { c, g } = canvas(512, 96);
  g.fillStyle = "rgba(8,20,45,0.85)"; g.fillRect(0, 0, 512, 96);
  g.strokeStyle = "rgba(56,189,248,0.8)"; g.lineWidth = 3; g.strokeRect(2, 2, 508, 92);
  g.fillStyle = "#e0f2fe"; g.font = "600 40px ui-monospace, Menlo, Consolas, monospace"; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, 256, 50);
  return new THREE.CanvasTexture(c);
}

type Desk = { at: THREE.Vector3; floor: "binance" | "xp" };
type Bot = { group: THREE.Group; body: THREE.Mesh; desk: Desk; target: THREE.Vector3; t: number; busy: boolean; active: boolean; path: THREE.Line; marker: THREE.Mesh };

export class ControlEngine {
  onFirstFrame: (() => void) | null = null;
  onAnchors: ((anchors: Record<string, Anchor>) => void) | null = null;
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(38, 1, 0.5, 300);
  private disposables: { dispose: () => void }[] = [];
  private globe: THREE.Mesh;
  private rings: THREE.Mesh[] = [];
  private desks: Desk[] = [];
  private bots = new Map<string, Bot>();
  private geo: { body: THREE.BufferGeometry; visor: THREE.BufferGeometry; antenna: THREE.BufferGeometry; marker: THREE.BufferGeometry };
  private mat: { body: THREE.Material; off: THREE.Material; visor: THREE.Material; path: THREE.LineBasicMaterial; marker: THREE.Material };
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
    this.renderer.setClearColor(NAVY);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.animate = !reduced;
    const own = <T extends { dispose: () => void }>(x: T): T => { this.disposables.push(x); return x; };

    this.scene.add(new THREE.HemisphereLight(0x93c5fd, 0x0b1a33, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 0.9); key.position.set(10, 30, 15); this.scene.add(key);

    const floor = new THREE.Mesh(own(new THREE.PlaneGeometry(64, 48)), own(new THREE.MeshLambertMaterial({ map: own(floorTexture()) })));
    floor.rotation.x = -Math.PI / 2; this.scene.add(floor);

    // Concentric glowing rings around the hub.
    const ringMat = own(new THREE.MeshBasicMaterial({ color: BLUE, transparent: true, opacity: 0.55, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
    for (const [r, w] of [[6.2, 0.12], [8.6, 0.18], [9.4, 0.06], [12.5, 0.1]] as const) {
      const ring = new THREE.Mesh(own(new THREE.RingGeometry(r, r + w, 96)), ringMat);
      ring.rotation.x = -Math.PI / 2; ring.position.y = 0.02; this.scene.add(ring); this.rings.push(ring);
    }
    const disc = new THREE.Mesh(own(new THREE.CircleGeometry(6.2, 64)), own(new THREE.MeshLambertMaterial({ color: 0x0e2547 })));
    disc.rotation.x = -Math.PI / 2; disc.position.y = 0.015; this.scene.add(disc);

    // Ring of consoles with blue screens facing the globe; the entrance faces the camera.
    const consoleGeo = own(new THREE.BoxGeometry(1.5, 0.7, 0.7));
    const screenGeo = own(new THREE.PlaneGeometry(1.2, 0.5));
    const consoleMat = own(new THREE.MeshLambertMaterial({ color: 0x1e293b }));
    const screenMat = own(new THREE.MeshBasicMaterial({ color: 0x60a5fa }));
    const n = 22;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      if (Math.abs(a - Math.PI / 2) < 0.35) continue;
      const g = new THREE.Group();
      const box = new THREE.Mesh(consoleGeo, consoleMat); box.position.y = 0.35; g.add(box);
      const scr = new THREE.Mesh(screenGeo, screenMat); scr.position.set(0, 0.95, 0.1); scr.rotation.x = -0.5; g.add(scr);
      g.position.set(Math.cos(a) * 5.2, 0, Math.sin(a) * 5.2);
      g.lookAt(0, 0, 0);
      this.scene.add(g);
    }
    const label = new THREE.Mesh(own(new THREE.PlaneGeometry(4, 0.75)), own(new THREE.MeshBasicMaterial({ map: own(labelTexture("PROTO CONTROL")), transparent: true })));
    label.position.set(0, 1.6, -5.4); label.rotation.x = -0.55; this.scene.add(label);

    // The globe and its glow.
    this.globe = new THREE.Mesh(own(new THREE.SphereGeometry(2.6, 48, 32)), own(new THREE.MeshBasicMaterial({ map: own(globeTexture()) })));
    this.globe.position.y = 2.8; this.globe.rotation.z = 0.35; this.scene.add(this.globe);
    const glow = new THREE.Mesh(own(new THREE.SphereGeometry(2.95, 32, 24)), own(new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending, side: THREE.BackSide, depthWrite: false })));
    glow.position.copy(this.globe.position); this.scene.add(glow);

    // Desk islands: Binance testnet on the left and right, XP / B3 demo at the bottom.
    const deskGeo = own(new THREE.BoxGeometry(2.2, 0.75, 1.1));
    const monGeo = own(new THREE.BoxGeometry(1.1, 0.6, 0.06));
    const chairGeo = own(new THREE.BoxGeometry(0.6, 0.5, 0.6));
    const deskMat = own(new THREE.MeshLambertMaterial({ color: 0x172554 }));
    const monMat = own(new THREE.MeshBasicMaterial({ color: 0x93c5fd }));
    const chairMat = own(new THREE.MeshLambertMaterial({ color: 0x0f172a }));
    const addDesk = (x: number, z: number, floorId: "binance" | "xp") => {
      const d = new THREE.Mesh(deskGeo, deskMat); d.position.set(x, 0.375, z); this.scene.add(d);
      const m = new THREE.Mesh(monGeo, monMat); m.position.set(x, 1.05, z - 0.35); this.scene.add(m);
      const ch = new THREE.Mesh(chairGeo, chairMat); ch.position.set(x, 0.25, z + 0.95); this.scene.add(ch);
      this.desks.push({ at: new THREE.Vector3(x, 0, z + 1.5), floor: floorId });
    };
    for (const x of [-20, -16.5]) for (const z of [-8, -4.5, -1, 2.5]) addDesk(x, z, "binance");
    for (const x of [16.5, 20]) for (const z of [-8, -4.5, -1, 2.5]) addDesk(x, z, "binance");
    for (const x of [-6, -2.5, 1, 4.5]) addDesk(x, 15, "xp");

    // Glass partitions with an amber strip, and potted plants.
    const glassMat = own(new THREE.MeshLambertMaterial({ color: 0x60a5fa, transparent: true, opacity: 0.12 }));
    const stripMat = own(new THREE.MeshBasicMaterial({ color: AMBER }));
    const wall = (x: number, z: number, w: number, d: number) => {
      const g = new THREE.Mesh(own(new THREE.BoxGeometry(w, 1.6, d)), glassMat); g.position.set(x, 0.8, z); this.scene.add(g);
      const s = new THREE.Mesh(own(new THREE.BoxGeometry(w, 0.06, d + 0.02)), stripMat); s.position.set(x, 1.62, z); this.scene.add(s);
    };
    wall(-13.8, -3, 0.12, 14); wall(13.8, -3, 0.12, 14); wall(-1, 12, 13, 0.12);
    const potGeo = own(new THREE.CylinderGeometry(0.35, 0.28, 0.6, 12));
    const leafGeo = own(new THREE.IcosahedronGeometry(0.6, 0));
    const potMat = own(new THREE.MeshLambertMaterial({ color: 0xe5e7eb }));
    const leafMat = own(new THREE.MeshLambertMaterial({ color: 0x16a34a }));
    for (const [x, z] of [[-11, -10], [11, -10], [-11, 9], [11, 9], [-8.5, 13], [7.5, 13], [0, -12]] as const) {
      const p = new THREE.Mesh(potGeo, potMat); p.position.set(x, 0.3, z); this.scene.add(p);
      const l = new THREE.Mesh(leafGeo, leafMat); l.position.set(x, 1.1, z); this.scene.add(l);
    }

    // Robot parts, shared by every agent (PROTO's own robots, not any known character).
    this.geo = {
      body: own(new THREE.CapsuleGeometry(0.42, 0.55, 6, 12)),
      visor: own(new THREE.BoxGeometry(0.62, 0.16, 0.3)),
      antenna: own(new THREE.CylinderGeometry(0.03, 0.03, 0.35, 6)),
      marker: own(new THREE.RingGeometry(0.25, 0.4, 24)),
    };
    this.mat = {
      body: own(new THREE.MeshLambertMaterial({ color: 0xfacc15 })),
      off: own(new THREE.MeshLambertMaterial({ color: 0x64748b })),
      visor: own(new THREE.MeshBasicMaterial({ color: CYAN })),
      path: own(new THREE.LineBasicMaterial({ color: AMBER })),
      marker: own(new THREE.MeshBasicMaterial({ color: CYAN, side: THREE.DoubleSide })),
    };
    this.renderer.setAnimationLoop(() => this.tick());
  }

  setAgents(list: ControlAgent[] | null) {
    const wanted = new Set((list ?? []).map(a => a.id));
    for (const [id, bot] of this.bots) if (!wanted.has(id)) { this.scene.remove(bot.group, bot.path, bot.marker); bot.path.geometry.dispose(); this.bots.delete(id); }
    const taken = new Set([...this.bots.values()].map(b => b.desk));
    for (const a of list ?? []) {
      let bot = this.bots.get(a.id);
      if (!bot) {
        const desk = this.desks.find(d => d.floor === a.floor && !taken.has(d));
        if (!desk) continue; // more agents than desks on this floor: the tags list still shows them
        taken.add(desk);
        const group = new THREE.Group();
        const body = new THREE.Mesh(this.geo.body, this.mat.body); body.position.y = 0.7; group.add(body);
        const visor = new THREE.Mesh(this.geo.visor, this.mat.visor); visor.position.set(0, 0.95, 0.32); group.add(visor);
        const ant = new THREE.Mesh(this.geo.antenna, this.mat.visor); ant.position.y = 1.55; group.add(ant);
        group.position.copy(desk.at);
        // The hub console this agent walks to while a pipeline station works on its symbol.
        const angle = Math.atan2(desk.at.z, desk.at.x);
        const target = new THREE.Vector3(Math.cos(angle) * 7, 0, Math.sin(angle) * 7);
        const path = new THREE.Line(new THREE.BufferGeometry().setFromPoints([desk.at.clone().setY(0.05), target.clone().setY(0.05)]), this.mat.path);
        const marker = new THREE.Mesh(this.geo.marker, this.mat.marker);
        marker.rotation.x = -Math.PI / 2; marker.position.copy(target).setY(0.06);
        this.scene.add(group, path, marker);
        bot = { group, body, desk, target, t: 0, busy: false, active: true, path, marker };
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

  zoom(factor: number) { this.zoomLevel = Math.min(1.8, Math.max(0.55, this.zoomLevel * factor)); }
  orbit(dx: number) { this.yaw = Math.max(-0.6, Math.min(0.6, this.yaw - dx * 0.004)); }

  private placeCamera() {
    const fit = this.width < this.height ? 1.45 : 1; // portrait screens back off to keep the room in view
    const dist = 46 * this.zoomLevel * fit;
    this.camera.position.set(Math.sin(this.yaw) * dist * 0.55, dist * 0.82, Math.cos(this.yaw) * dist * 0.55 + 4);
    this.camera.lookAt(0, 0, 2);
  }

  private tick() {
    if (!this.running) return;
    const dt = Math.min(this.clock.getDelta(), 0.1);
    if (this.animate) {
      this.phase += dt;
      this.globe.rotation.y += dt * 0.15;
      this.rings.forEach((r, i) => { r.rotation.z += dt * (i % 2 ? -0.05 : 0.08); });
    }
    for (const bot of this.bots.values()) {
      // Busy agents walk to their hub console and back; with motion off they jump there.
      const step = this.animate ? dt * 0.25 : 1;
      bot.t = Math.min(1, Math.max(0, bot.t + (bot.busy ? step : -step)));
      bot.group.position.lerpVectors(bot.desk.at, bot.target, bot.t);
      const walking = bot.t > 0 && bot.t < 1;
      bot.group.position.y = bot.active && this.animate ? Math.abs(Math.sin(this.phase * 7)) * (walking ? 0.12 : 0.03) : 0;
      bot.group.lookAt(bot.busy ? bot.target.x : bot.desk.at.x, bot.group.position.y, bot.busy ? bot.target.z : bot.desk.at.z - 1);
    }
    this.placeCamera();
    this.renderer.render(this.scene, this.camera);
    if (this.onAnchors) {
      const out: Record<string, Anchor> = {};
      const v = new THREE.Vector3();
      for (const [id, bot] of this.bots) {
        v.copy(bot.group.position).setY(bot.group.position.y + 1.9).project(this.camera);
        out[id] = { x: ((v.x + 1) / 2) * this.width, y: ((1 - v.y) / 2) * this.height, visible: v.z < 1 };
      }
      this.onAnchors(out);
    }
    if (this.first) { this.first = false; this.onFirstFrame?.(); }
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    for (const bot of this.bots.values()) bot.path.geometry.dispose();
    this.disposables.forEach(d => d.dispose());
    this.renderer.dispose();
  }
}
