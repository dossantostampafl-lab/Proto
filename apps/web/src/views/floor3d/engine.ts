// The 3D trading room: camera high behind the central desk, looking at a curved wall of screens.
// Screens are live canvas textures; operators animate from their desk's verified activity.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { type Sym, SYMBOLS } from "../../api";
import type { Shared } from "../../main";
import { type FloorAction, type FloorState, STATIONS, type StationId, TONE_COLOR } from "../floorModel";
import { type Look, Operator, Walker } from "./people";
import * as screens from "./screens";

export type FloorData = {
  market: Shared["market"];
  state: FloorState | null;
  actions: FloorAction[];
  latest: Partial<Record<StationId, FloorAction>>;
  busy: Record<StationId, boolean>;
};

type DeskKind = "hub" | "desk" | "console";
type DeskDef = { id: StationId; x: number; z: number; kind: DeskKind; look: Look };
const WALL_TARGET = new THREE.Vector3(0, 0, -9.5);
const DESKS: DeskDef[] = [
  { id: "tradingview", x: -5.3, z: -1.9, kind: "desk", look: { skin: "#c49a82", hair: "#2a1a12", suit: "#1d2126", long: true } },
  { id: "fusion", x: -3.0, z: -3.8, kind: "desk", look: { skin: "#d2a98f", hair: "#141414", suit: "#22262c" } },
  { id: "reviewer", x: 0, z: 0, kind: "hub", look: { skin: "#c79c80", hair: "#111111", suit: "#191c20" } },
  { id: "risk", x: 3.0, z: -3.8, kind: "desk", look: { skin: "#a87a5f", hair: "#1b1410", suit: "#20242a" } },
  { id: "oms", x: 5.3, z: -1.9, kind: "desk", look: { skin: "#d7b29a", hair: "#3a2416", suit: "#24272c" } },
  { id: "reconciliation", x: 0, z: -5.7, kind: "console", look: { skin: "#b98c70", hair: "#4e321b", suit: "#1f2328" } },
];
const SPEC: Record<DeskKind, { halfW: number; front: number; back: number; operatorZ: number; monitorZ: number; spread: number; monitor: [number, number] }> = {
  desk: { halfW: 1.15, front: 0.45, back: -0.45, operatorZ: 0.8, monitorZ: -0.2, spread: 0.66, monitor: [0.62, 0.38] },
  hub: { halfW: 1.3, front: 1.25, back: -1.25, operatorZ: 1.62, monitorZ: 0.72, spread: 0.84, monitor: [0.8, 0.48] },
  console: { halfW: 0.95, front: 0.36, back: -0.36, operatorZ: 0.7, monitorZ: -0.12, spread: 0.55, monitor: [0.54, 0.34] },
};

const WALL_C = new THREE.Vector3(0, 0, 2.5), WALL_R = 12;
const PANELS: { key: "btc" | "eth" | "sol" | "equity"; width: number }[] = [
  { key: "btc", width: 4.3 }, { key: "eth", width: 2.5 }, { key: "sol", width: 2.5 }, { key: "equity", width: 2.4 },
];
const PANEL_GAP = 0.12, PANEL_BOTTOM = 2.62, PANEL_H = 2.6, STRIP_H = 0.4, PX_PER_M = 300;

type Face = { c: OffscreenCanvas; g: screens.Ctx; tex: THREE.CanvasTexture<OffscreenCanvas>; w: number; h: number };
type Station = {
  def: DeskDef;
  operator: Operator;
  monitors: Face[];
  monitorMats: THREE.MeshBasicMaterial[];
  plate: Face;
  glow: THREE.MeshBasicMaterial[];
  line: THREE.MeshBasicMaterial | null;
  world: THREE.Vector3;
};

const std = (color: string, roughness: number, metalness = 0) => new THREE.MeshStandardMaterial({ color, roughness, metalness });
const LED = new THREE.Color("#f2e9da").multiplyScalar(1.5);

/** Desk outline in desk-local x/z: an octagon for the hub, chamfered rectangles otherwise. */
function outline(kind: DeskKind, scale = 1): THREE.Vector2[] {
  const s = SPEC[kind];
  if (kind === "hub") {
    const r = (s.front / Math.cos(Math.PI / 8)) * scale;
    return Array.from({ length: 8 }, (_, i) => {
      const a = Math.PI / 8 + (i * Math.PI) / 4;
      return new THREE.Vector2(Math.sin(a) * r * (s.halfW / s.front), Math.cos(a) * r);
    });
  }
  const w = s.halfW * scale, f = s.front * scale, b = s.back * scale, c = 0.28 * scale;
  return [new THREE.Vector2(-w, f), new THREE.Vector2(w, f), new THREE.Vector2(w, b + c), new THREE.Vector2(w - c, b), new THREE.Vector2(-w + c, b), new THREE.Vector2(-w, b + c)];
}
/** Shape in the XY plane whose y is -z, so a mesh rotated -90° about X lies on the floor. */
const shapeOf = (pts: THREE.Vector2[]) => new THREE.Shape(pts.map(p => new THREE.Vector2(p.x, -p.y)));
function ring(pts: THREE.Vector2[], width: number) {
  const outer = shapeOf(pts);
  const cx = pts.reduce((a, p) => a + p.x, 0) / pts.length, cz = pts.reduce((a, p) => a + p.y, 0) / pts.length;
  const hole = new THREE.Path(pts.map(p => {
    const d = new THREE.Vector2(p.x - cx, p.y - cz);
    const k = Math.max(0, 1 - width / Math.max(d.length(), 1e-6));
    return new THREE.Vector2(cx + d.x * k, -(cz + d.y * k));
  }));
  outer.holes.push(hole);
  return new THREE.ShapeGeometry(outer);
}

function face(w: number, h: number, anisotropy: number): Face {
  const { c, g } = screens.canvas(w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = anisotropy;
  return { c, g, tex, w, h };
}

export class FloorEngine {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(44, 16 / 9, 0.1, 80);
  private readonly composer: EffectComposer;
  private readonly bloom: UnrealBloomPass;
  private readonly clock = new THREE.Clock();
  private readonly stations: Station[] = [];
  private readonly wall: Record<"btc" | "eth" | "sol" | "equity" | "strip", Face>;
  private readonly walker: Walker;
  private readonly reduced: boolean;
  private readonly anisotropy: number;
  private data: FloorData | null = null;
  private dirty = true;
  private lastDraw = -1;
  private disposed = false;

  constructor(canvas: HTMLCanvasElement, reduced: boolean) {
    this.reduced = reduced;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.anisotropy = this.renderer.capabilities.getMaxAnisotropy();

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.34;
    pmrem.dispose();
    this.scene.background = new THREE.Color("#07080a");
    this.scene.fog = new THREE.Fog("#0a0b0d", 16, 34);

    this.camera.position.set(0, 5.1, 8.3);
    this.camera.lookAt(0, 1.55, -4.2);

    this.buildRoom();
    this.wall = this.buildWall();
    for (const def of DESKS) this.stations.push(this.buildStation(def));
    this.buildLines();
    this.walker = new Walker({ skin: "#c09479", hair: "#1a1a1a", suit: "#2a2e33" }, [
      new THREE.Vector3(-6.2, 0, -7.6), new THREE.Vector3(6.2, 0, -7.6), new THREE.Vector3(7.8, 0, -4.4), new THREE.Vector3(6.2, 0, -7.6),
      new THREE.Vector3(-6.2, 0, -7.6), new THREE.Vector3(-7.8, 0, -4.4),
    ]);
    this.scene.add(this.walker.group);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1280, 720), 0.32, 0.55, 0.82);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    if (document.fonts) void document.fonts.ready.then(() => { this.dirty = true; });
    this.renderer.setAnimationLoop(() => this.frame());
  }

  private buildRoom() {
    const { scene } = this;
    const floorFace = face(1024, 1024, this.anisotropy);
    screens.drawFloor(floorFace.g, 1024, 1024);
    floorFace.tex.wrapS = floorFace.tex.wrapT = THREE.RepeatWrapping;
    floorFace.tex.repeat.set(7, 7);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(28, 28), new THREE.MeshStandardMaterial({ map: floorFace.tex, color: "#8d9196", roughness: 0.3, metalness: 0.2, envMapIntensity: 0.35 }));
    floor.rotation.x = -Math.PI / 2; floor.position.z = -3; floor.receiveShadow = true;
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(18, 16), std("#0b0c0e", 0.9));
    ceiling.rotation.x = Math.PI / 2; ceiling.position.set(0, 6, -2.4);
    const back = new THREE.Mesh(new THREE.PlaneGeometry(13.6, 6), std("#0f1113", 0.7, 0.3));
    back.position.set(0, 3, -10.4);
    scene.add(floor, ceiling, back);

    const city = face(2048, 640, this.anisotropy);
    screens.drawCity(city.g, 2048, 640);
    for (const side of [-1, 1]) {
      const sky = new THREE.Mesh(new THREE.PlaneGeometry(30, 10), new THREE.MeshBasicMaterial({ map: city.tex, fog: false }));
      sky.position.set(side * 17, 4.2, -4); sky.rotation.y = -side * Math.PI / 2;
      const corner = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 6), new THREE.MeshStandardMaterial({ color: "#9aa0a6", roughness: 0.08, metalness: 0.9, transparent: true, opacity: 0.1 }));
      corner.position.set(side * 7.9, 3, -10.4);
      scene.add(corner);
      const glass = new THREE.Mesh(new THREE.PlaneGeometry(16, 6), new THREE.MeshStandardMaterial({ color: "#9aa0a6", roughness: 0.08, metalness: 0.9, transparent: true, opacity: 0.1 }));
      glass.position.set(side * 9, 3, -3); glass.rotation.y = -side * Math.PI / 2;
      scene.add(sky, glass);
      const frame = std("#0b0c0e", 0.5, 0.6);
      for (let z = -10.4; z <= 4.4; z += 2.1) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, 6, 0.14), frame);
        post.position.set(side * 9, 3, z);
        scene.add(post);
      }
      for (const y of [0.25, 3.2, 5.95]) {
        const rail = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.1, 16), frame);
        rail.position.set(side * 9, y, -3);
        scene.add(rail);
      }
    }

    const far = new THREE.Mesh(new THREE.PlaneGeometry(50, 12), new THREE.MeshBasicMaterial({ map: city.tex, fog: false }));
    far.position.set(0, 4.6, -24);
    scene.add(far);
    scene.add(new THREE.HemisphereLight("#cfd3d8", "#121416", 0.26));
    // The wall of screens lights the room softly; an ambient term carries it without a floor glare.
    scene.add(new THREE.AmbientLight("#dfe3e8", 0.32));
    const disc = new THREE.MeshBasicMaterial({ color: new THREE.Color("#fff3e0").multiplyScalar(2.2) });
    for (let x = -6; x <= 6; x += 3) for (let z = -8; z <= 4; z += 3) {
      const d = new THREE.Mesh(new THREE.CircleGeometry(0.09, 20), disc);
      d.rotation.x = Math.PI / 2; d.position.set(x, 5.98, z);
      scene.add(d);
    }
  }

  private buildWall() {
    const total = PANELS.reduce((a, p) => a + p.width, 0) + PANEL_GAP * (PANELS.length - 1);
    let s = -total / 2;
    const out = {} as Record<"btc" | "eth" | "sol" | "equity" | "strip", Face>;
    const backing = std("#08090b", 0.45, 0.7);
    const stripFace = face(Math.round(total * PX_PER_M * 0.7), Math.round(STRIP_H * PX_PER_M * 0.7), this.anisotropy);
    out.strip = stripFace;
    for (const p of PANELS) {
      const mid = s + p.width / 2;
      const phi = mid / WALL_R;
      const pos = new THREE.Vector3(WALL_C.x + Math.sin(phi) * WALL_R, 0, WALL_C.z - Math.cos(phi) * WALL_R);
      const f = face(Math.round(p.width * PX_PER_M), Math.round(PANEL_H * PX_PER_M), this.anisotropy);
      out[p.key] = f;
      const panel = new THREE.Mesh(new THREE.PlaneGeometry(p.width, PANEL_H), new THREE.MeshBasicMaterial({ map: f.tex, toneMapped: false }));
      panel.position.set(pos.x, PANEL_BOTTOM + PANEL_H / 2, pos.z);
      panel.rotation.y = -phi;
      const frame = new THREE.Mesh(new THREE.BoxGeometry(p.width + 0.08, PANEL_H + 0.08, 0.12), backing);
      frame.position.copy(panel.position).add(new THREE.Vector3(Math.sin(phi), 0, -Math.cos(phi)).multiplyScalar(0.07));
      frame.rotation.y = -phi;
      // The strip under the panels shows its part of one long texture.
      const strip = stripFace.tex.clone();
      strip.repeat.set(p.width / total, 1);
      strip.offset.set((s + total / 2) / total, 0);
      const stripMesh = new THREE.Mesh(new THREE.PlaneGeometry(p.width + PANEL_GAP, STRIP_H), new THREE.MeshBasicMaterial({ map: strip, toneMapped: false }));
      stripMesh.position.set(pos.x, PANEL_BOTTOM - 0.1 - STRIP_H / 2, pos.z);
      stripMesh.rotation.y = -phi;
      stripMesh.userData.stripTexture = strip;
      this.scene.add(frame, panel, stripMesh);
      s += p.width + PANEL_GAP;
    }
    return out;
  }

  private buildStation(def: DeskDef): Station {
    const spec = SPEC[def.kind];
    const group = new THREE.Group();
    group.position.set(def.x, 0, def.z);
    group.rotation.y = Math.atan2(-(WALL_TARGET.x - def.x), -(WALL_TARGET.z - def.z));
    this.scene.add(group);

    const top = new THREE.Mesh(new THREE.ExtrudeGeometry(shapeOf(outline(def.kind)), { depth: 0.05, bevelEnabled: true, bevelSize: 0.012, bevelThickness: 0.012, bevelSegments: 2 }), std("#2b2f34", 0.42, 0.25));
    top.rotation.x = -Math.PI / 2; top.position.y = 0.72; top.castShadow = top.receiveShadow = true;
    const metal = std("#4a4f56", 0.32, 0.85);
    const body = def.kind === "hub"
      ? new THREE.Mesh(new THREE.ExtrudeGeometry(shapeOf(outline(def.kind, 0.74)), { depth: 0.68, bevelEnabled: false }), metal)
      : new THREE.Mesh(new THREE.ExtrudeGeometry(shapeOf([new THREE.Vector2(-spec.halfW + 0.08, 0.02), new THREE.Vector2(spec.halfW - 0.08, 0.02), new THREE.Vector2(spec.halfW - 0.08, spec.back + 0.08), new THREE.Vector2(-spec.halfW + 0.08, spec.back + 0.08)]), { depth: 0.68, bevelEnabled: false }), metal);
    body.rotation.x = -Math.PI / 2; body.position.y = 0.02; body.castShadow = body.receiveShadow = true;
    group.add(top, body);
    if (def.kind !== "hub") {
      for (const side of [-1, 1]) {
        const ped = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.68, spec.front - spec.back - 0.12), metal);
        ped.position.set(side * (spec.halfW - 0.3), 0.36, (spec.front + spec.back) / 2);
        ped.castShadow = ped.receiveShadow = true;
        group.add(ped);
      }
    }
    // LED: under the desk edge and around the base. Lit in the state colour while the desk works.
    const glow = [new THREE.MeshBasicMaterial({ color: LED.clone(), transparent: true, opacity: 0.55 }), new THREE.MeshBasicMaterial({ color: LED.clone(), transparent: true, opacity: 0.4 })];
    const under = new THREE.Mesh(ring(outline(def.kind, 0.98), 0.03), glow[0]);
    under.rotation.x = -Math.PI / 2; under.position.y = 0.705;
    const base = new THREE.Mesh(ring(outline(def.kind, def.kind === "hub" ? 0.76 : 0.9), 0.04), glow[1]);
    base.rotation.x = -Math.PI / 2; base.position.y = 0.012;
    group.add(under, base);

    const [mw, mh] = spec.monitor;
    const monitors: Face[] = [], monitorMats: THREE.MeshBasicMaterial[] = [];
    const bezel = std("#0c0d0f", 0.4, 0.5);
    [-1, 0, 1].forEach(slot => {
      const f = face(Math.round(mw * 520), Math.round(mh * 520), this.anisotropy);
      monitors.push(f);
      const m = new THREE.MeshBasicMaterial({ map: f.tex, toneMapped: false });
      monitorMats.push(m);
      const mon = new THREE.Group();
      mon.position.set(slot * spec.spread, 0, spec.monitorZ + Math.abs(slot) * 0.12);
      mon.rotation.y = -slot * 0.32;
      const screen = new THREE.Mesh(new THREE.PlaneGeometry(mw, mh), m);
      screen.position.set(0, 1.0 + mh / 2, 0.012);
      const back = new THREE.Mesh(new THREE.BoxGeometry(mw + 0.03, mh + 0.03, 0.02), bezel);
      back.position.set(0, 1.0 + mh / 2, 0);
      const stand = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.26, 0.03), metal);
      stand.position.set(0, 0.88, -0.03);
      const foot = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.012, 0.14), metal);
      foot.position.set(0, 0.776, -0.03);
      back.castShadow = true;
      mon.add(screen, back, stand, foot);
      group.add(mon);
    });
    const keyboard = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.015, 0.13), std("#15171a", 0.5));
    keyboard.position.set(0, 0.785, spec.operatorZ - 0.42);
    group.add(keyboard);

    const operator = new Operator(def.look, DESKS.indexOf(def) * 1.7);
    operator.group.position.set(0, 0, spec.operatorZ);
    group.add(operator.group);

    const plate = face(640, 160, this.anisotropy);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: plate.tex, transparent: true, depthWrite: false, toneMapped: false }));
    sprite.scale.set(1.6, 0.4, 1);
    sprite.position.set(0, def.kind === "hub" ? 2.0 : 1.95, spec.monitorZ);
    group.add(sprite);

    const light = new THREE.SpotLight("#fff1dc", def.kind === "hub" ? 30 : def.kind === "console" ? 12 : 24, 9, 0.6, 0.75, 2);
    light.position.set(0, 5.9, spec.operatorZ * 0.4);
    light.target.position.set(0, 0, spec.operatorZ * 0.3);
    light.castShadow = def.kind !== "console";
    light.shadow.mapSize.set(1024, 1024);
    light.shadow.bias = -0.0004;
    group.add(light, light.target);

    return { def, operator, monitors, monitorMats, plate, glow, line: null, world: new THREE.Vector3(def.x, 0, def.z) };
  }

  /** Floor LED lines from the hub to every desk, as in the room's design. */
  private buildLines() {
    const hub = this.stations.find(s => s.def.kind === "hub")!;
    for (const st of this.stations) {
      if (st === hub) continue;
      const dir = new THREE.Vector3().subVectors(st.world, hub.world);
      const len = dir.length() - 2.6;
      dir.normalize();
      const mat = new THREE.MeshBasicMaterial({ color: LED.clone(), transparent: true, opacity: 0.35 });
      const line = new THREE.Mesh(new THREE.PlaneGeometry(0.035, len), mat);
      line.rotation.x = -Math.PI / 2;
      // The holder's -z points along the line; the plane lies flat along it.
      const holder = new THREE.Group();
      holder.rotation.y = Math.atan2(-dir.x, -dir.z);
      holder.position.copy(hub.world).addScaledVector(dir, 1.5 + len / 2).setY(0.008);
      holder.add(line);
      this.scene.add(holder);
      st.line = mat;
    }
  }

  setData(data: FloorData) {
    this.data = data;
    this.dirty = true;
  }

  resize(width: number, height: number) {
    if (width < 2 || height < 2) return;
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.bloom.resolution.set(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  private draw() {
    const d = this.data;
    if (!d) return;
    const { btc, eth, sol, equity, strip } = this.wall;
    screens.drawBtc(btc.g, btc.w, btc.h, d.market); btc.tex.needsUpdate = true;
    screens.drawSymbol(eth.g, eth.w, eth.h, d.market, "ETH"); eth.tex.needsUpdate = true;
    screens.drawSymbol(sol.g, sol.w, sol.h, d.market, "SOL"); sol.tex.needsUpdate = true;
    screens.drawEquity(equity.g, equity.w, equity.h, d.state); equity.tex.needsUpdate = true;
    screens.drawStrip(strip.g, strip.w, strip.h, d.state, d.actions); strip.tex.needsUpdate = true;
    this.scene.traverse(o => { if (o.userData.stripTexture) (o.userData.stripTexture as THREE.Texture).needsUpdate = true; });
    this.stations.forEach((st, i) => {
      const info = STATIONS.find(s => s.id === st.def.id)!;
      const action = d.latest[st.def.id];
      const busy = d.busy[st.def.id];
      const color = TONE_COLOR[action ? action.tone : "idle"];
      const recent = d.actions.filter(a => a.station === st.def.id);
      const symbols: Sym[] = [SYMBOLS[i % SYMBOLS.length], SYMBOLS[(i + 1) % SYMBOLS.length]];
      const [left, mid, right] = st.monitors;
      screens.drawMonitorSymbol(left.g, left.w, left.h, d.market, symbols[0], busy ? color : "#cfd4d8", busy);
      screens.drawMonitorLog(mid.g, mid.w, mid.h, recent, busy);
      screens.drawMonitorSymbol(right.g, right.w, right.h, d.market, symbols[1], busy ? color : "#cfd4d8", busy);
      st.monitors.forEach(m => { m.tex.needsUpdate = true; });
      screens.drawPlate(st.plate.g, st.plate.w, st.plate.h, info.label, info.role, action, busy);
      st.plate.tex.needsUpdate = true;
    });
  }

  private frame() {
    if (this.disposed) return;
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const t = this.clock.elapsedTime;
    if (this.dirty && t - this.lastDraw > 0.5) {
      this.dirty = false;
      this.lastDraw = t;
      this.draw();
    }
    const d = this.data;
    let alert: THREE.Vector3 | null = null;
    for (const st of this.stations) {
      const busy = d?.busy[st.def.id] ?? false;
      const action = d?.latest[st.def.id];
      st.operator.update(t, dt, busy, this.reduced);
      const tone = new THREE.Color(TONE_COLOR[action ? action.tone : "idle"]).multiplyScalar(1.8);
      const pulse = this.reduced ? 1 : 0.75 + 0.25 * Math.sin(t * 4);
      st.glow.forEach((m, i) => { m.color.copy(busy ? tone : LED); m.opacity = busy ? 0.85 * pulse : i ? 0.35 : 0.5; });
      st.monitorMats.forEach(m => m.color.setScalar(busy ? 1 : 0.86));
      if (st.line) { st.line.color.copy(busy ? tone : LED); st.line.opacity = busy ? 0.8 * pulse : 0.3; }
      if (action?.tone === "alert" && busy) {
        const side = st.def.x >= 0 ? -1 : 1;
        alert = st.world.clone().add(new THREE.Vector3(side * 1.6, 0, 1.2));
      }
    }
    this.walker.update(t, dt, alert, this.reduced);
    if (!this.reduced) {
      this.camera.position.set(Math.sin(t * 0.05) * 0.4, 5.1 + Math.sin(t * 0.07) * 0.08, 8.3 + Math.sin(t * 0.04) * 0.15);
      this.camera.lookAt(0, 1.55, -4.2);
    }
    this.composer.render(dt);
  }

  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    this.scene.traverse(o => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Sprite) {
        o.geometry.dispose();
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) { (m as THREE.MeshBasicMaterial).map?.dispose(); m.dispose(); }
      }
    });
    this.scene.environment?.dispose();
    this.composer.dispose();
    this.renderer.dispose();
  }
}
