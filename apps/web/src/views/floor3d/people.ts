// Low-poly people for the 3D room. A person sits at a desk, stands, and walks a route of points.
// What they do is decided by the engine: operators carry work between desks only when a live floor
// event arrives; floor staff wander between desks so the room is alive, and say nothing about data.
import * as THREE from "three";

export type Look = { skin: string; hair: string; suit: string; shirt?: string; long?: boolean };

const Y = new THREE.Vector3(0, 1, 0);
const tmp = new THREE.Vector3();
const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();

/** Shared low-poly geometry and one material per colour, so ten people cost little. */
export class Kit {
  private readonly mats = new Map<string, THREE.MeshStandardMaterial>();
  readonly limb = new THREE.CapsuleGeometry(0.5, 1, 2, 7);
  readonly ball = new THREE.SphereGeometry(1, 10, 8);
  readonly box = new THREE.BoxGeometry(1, 1, 1);
  readonly cyl = new THREE.CylinderGeometry(1, 1, 1, 8);
  readonly blob: THREE.MeshBasicMaterial;
  readonly blobGeo = new THREE.PlaneGeometry(1, 1);

  constructor(blob: THREE.Texture) {
    this.blob = new THREE.MeshBasicMaterial({ map: blob, transparent: true, depthWrite: false, opacity: 0.55, color: "#000000" });
  }
  mat(color: string, roughness = 0.7, metalness = 0) {
    const key = `${color}|${roughness}|${metalness}`;
    let m = this.mats.get(key);
    if (!m) { m = new THREE.MeshStandardMaterial({ color, roughness, metalness }); this.mats.set(key, m); }
    return m;
  }
  /** A soft contact shadow lying on the floor: a baked radial blob instead of a shadow map. */
  shadow(w: number, d: number, opacity = 0.55) {
    const m = new THREE.Mesh(this.blobGeo, opacity === 0.55 ? this.blob : this.blob.clone());
    if (opacity !== 0.55) (m.material as THREE.MeshBasicMaterial).opacity = opacity;
    m.rotation.x = -Math.PI / 2;
    m.scale.set(w, d, 1);
    m.position.y = 0.006;
    m.renderOrder = -1;
    return m;
  }
}

/** A capsule re-aimed every frame from point a to point b. */
class Limb {
  readonly mesh: THREE.Mesh;
  constructor(kit: Kit, private readonly radius: number, material: THREE.Material) {
    this.mesh = new THREE.Mesh(kit.limb, material);
  }
  aim(from: THREE.Vector3, to: THREE.Vector3) {
    tmp.subVectors(to, from);
    const len = Math.max(0.02, tmp.length());
    this.mesh.position.copy(from).addScaledVector(tmp, 0.5);
    this.mesh.quaternion.setFromUnitVectors(Y, tmp.normalize());
    // The shared capsule is 1 wide and 2 tall.
    this.mesh.scale.set(this.radius * 2, len / 2, this.radius * 2);
  }
}

export function chair(kit: Kit): THREE.Group {
  const g = new THREE.Group();
  const black = kit.mat("#16181b", 0.6, 0.1), metal = kit.mat("#7b8087", 0.35, 0.8), fabric = kit.mat("#24282d", 0.9);
  const part = (geo: THREE.BufferGeometry, m: THREE.Material, s: [number, number, number], p: [number, number, number], rx = 0) => {
    const mesh = new THREE.Mesh(geo, m);
    mesh.scale.set(...s); mesh.position.set(...p); mesh.rotation.x = rx;
    g.add(mesh);
    return mesh;
  };
  part(kit.cyl, metal, [0.2, 0.03, 0.2], [0, 0.06, 0]);
  part(kit.cyl, metal, [0.028, 0.36, 0.028], [0, 0.26, 0]);
  part(kit.box, black, [0.5, 0.08, 0.48], [0, 0.48, 0]);
  part(kit.box, fabric, [0.48, 0.62, 0.06], [0, 0.88, 0.25], 0.1);
  for (const side of [-1, 1]) part(kit.box, black, [0.05, 0.04, 0.28], [side * 0.27, 0.68, 0.02]);
  g.add(kit.shadow(0.9, 0.9, 0.45));
  return g;
}

type Step =
  | { kind: "walk"; to: THREE.Vector3 }
  | { kind: "face"; heading: number }
  | { kind: "sit"; on: boolean }
  | { kind: "wait"; seconds: number }
  | { kind: "carry"; color: string | null }
  | { kind: "call"; fn: () => void };

const SPEED = 1.15; // m/s, an unhurried walk

/** A person facing local -z. Origin on the floor; seated, the origin is the chair centre. */
export class Person {
  readonly group = new THREE.Group();
  private readonly upper = new THREE.Group();
  private readonly head: THREE.Group;
  private readonly legs: { thigh: Limb; shin: Limb; shoe: THREE.Mesh; side: number }[] = [];
  private readonly arms: { upper: Limb; fore: Limb; hand: THREE.Mesh; side: number }[] = [];
  private readonly ticket: THREE.Mesh;
  private readonly ticketMat: THREE.MeshBasicMaterial;
  private readonly steps: Step[] = [];
  private stride = 0;
  private speed = 0;
  private waited = 0;
  private sit = 0; // 0 standing .. 1 seated, eased
  private sitTarget = 0;
  private work = 0; // 0 idle .. 1 typing, eased
  private heading = 0;

  constructor(kit: Kit, look: Look, private readonly phase: number) {
    const suit = kit.mat(look.suit, 0.85), skin = kit.mat(look.skin, 0.6), shirt = kit.mat(look.shirt ?? "#d9dde0", 0.8);
    const hair = kit.mat(look.hair, 0.75), shoes = kit.mat("#0d0e10", 0.45, 0.2);
    const mesh = (geo: THREE.BufferGeometry, m: THREE.Material, s: [number, number, number], p: [number, number, number]) => {
      const x = new THREE.Mesh(geo, m); x.scale.set(...s); x.position.set(...p); return x;
    };
    // Upper body is built around a 0.95 m hip and dropped while seated.
    const torso = mesh(kit.limb, suit, [0.38, 0.31, 0.26], [0, 1.25, 0]);
    const collar = mesh(kit.box, shirt, [0.1, 0.1, 0.02], [0, 1.5, -0.12]);
    const shoulders = mesh(kit.limb, suit, [0.17, 0.24, 0.17], [0, 1.53, 0]);
    shoulders.rotation.z = Math.PI / 2;
    const neck = mesh(kit.cyl, skin, [0.045, 0.1, 0.045], [0, 1.65, 0]);
    this.head = new THREE.Group();
    this.head.position.set(0, 1.79, 0);
    this.head.add(mesh(kit.ball, skin, [0.092, 0.115, 0.1], [0, 0, 0]));
    const cap = mesh(kit.ball, hair, [0.097, 0.1, 0.105], [0, 0.03, 0.012]);
    this.head.add(cap);
    if (look.long) this.head.add(mesh(kit.limb, hair, [0.18, 0.12, 0.1], [0, -0.1, 0.06]));
    this.upper.add(torso, collar, shoulders, neck, this.head);
    this.group.add(this.upper);
    for (const side of [-1, 1]) {
      const thigh = new Limb(kit, 0.075, suit), shin = new Limb(kit, 0.06, suit);
      const shoe = mesh(kit.box, shoes, [0.1, 0.07, 0.25], [0, 0, 0]);
      this.group.add(thigh.mesh, shin.mesh, shoe);
      this.legs.push({ thigh, shin, shoe, side });
      const upper = new Limb(kit, 0.055, suit), fore = new Limb(kit, 0.047, suit);
      const hand = mesh(kit.ball, skin, [0.04, 0.03, 0.05], [0, 0, 0]);
      this.upper.add(upper.mesh, fore.mesh, hand);
      this.arms.push({ upper, fore, hand, side });
    }
    // The work being carried: a small glowing card in the tone of the event.
    this.ticketMat = new THREE.MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
    this.ticket = mesh(kit.box, this.ticketMat, [0.16, 0.11, 0.015], [0, 0, 0]);
    this.ticket.visible = false;
    this.upper.add(this.ticket);
    this.group.add(kit.shadow(0.75, 0.75));
  }

  get busy() { return this.steps.length > 0; }
  get queued() { return this.steps.length; }

  place(at: THREE.Vector3, heading: number, seated: boolean) {
    this.group.position.copy(at);
    this.heading = heading;
    this.group.rotation.y = heading;
    this.sit = this.sitTarget = seated ? 1 : 0;
  }

  /** Adds steps to the end of this person's plan. */
  plan(...steps: Step[]) { this.steps.push(...steps); }
  walk(points: THREE.Vector3[]) { this.plan(...points.map(p => ({ kind: "walk" as const, to: p.clone() }))); }

  update(t: number, dt: number, working: boolean, reduced: boolean) {
    this.speed = 0;
    // Run through instant steps, then advance the current timed one.
    for (let guard = 0; guard < 8 && this.steps.length; guard++) {
      const s = this.steps[0];
      if (s.kind === "sit") {
        this.sitTarget = s.on ? 1 : 0;
        if (!reduced && Math.abs(this.sit - this.sitTarget) > 0.02) break;
        this.sit = this.sitTarget;
      } else if (s.kind === "face") {
        this.heading = s.heading;
        if (reduced) this.group.rotation.y = s.heading;
      } else if (s.kind === "carry") {
        this.ticket.visible = s.color !== null;
        if (s.color) this.ticketMat.color.set(s.color).multiplyScalar(1.6);
      } else if (s.kind === "call") {
        s.fn();
      } else if (s.kind === "wait") {
        this.waited += dt;
        if (this.waited < s.seconds) break;
        this.waited = 0;
      } else {
        const to = tmp.subVectors(s.to, this.group.position).setY(0);
        const dist = to.length();
        if (reduced || dist < 0.06) {
          // Reduced motion: people are placed, never animated across the room.
          if (reduced) this.group.position.set(s.to.x, 0, s.to.z);
        } else {
          this.speed = Math.min(SPEED, 0.35 + dist * 2);
          to.normalize();
          this.group.position.addScaledVector(to, Math.min(dist, this.speed * dt));
          this.heading = Math.atan2(-to.x, -to.z);
          break;
        }
      }
      this.steps.shift();
    }
    if (reduced) this.group.rotation.y = this.heading;
    else {
      let d = this.heading - this.group.rotation.y;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      this.group.rotation.y += d * Math.min(1, dt * 6);
    }
    this.sit += (this.sitTarget - this.sit) * (reduced ? 1 : Math.min(1, dt * 4));
    this.work += ((working && this.sit > 0.9 ? 1 : 0) - this.work) * Math.min(1, dt * 3);
    this.pose(t, dt, reduced);
  }

  private pose(t: number, dt: number, reduced: boolean) {
    const s = this.sit, w = this.work;
    this.stride += this.speed * dt * 5.4;
    const swing = Math.sin(this.stride) * Math.min(1, this.speed * 1.4);
    const drop = -0.4 * s;
    this.upper.position.set(0, drop, 0.05 * s);
    this.upper.rotation.x = s * (-0.04 - 0.1 * w);
    const breathe = reduced ? 0 : Math.sin(t * 1.7 + this.phase) * 0.006;
    this.upper.scale.y = 1 + breathe;
    const look = reduced ? 0 : (1 - w) * 0.35 * Math.sin(t * 0.23 + this.phase) * Math.sin(t * 0.09 + this.phase * 2);
    this.head.rotation.y = this.speed > 0.05 ? 0 : look;
    this.head.rotation.x = s * (0.12 + 0.1 * w) + (this.speed > 0.05 ? 0.05 : 0);
    for (const { thigh, shin, shoe, side } of this.legs) {
      const k = swing * side;
      // standing / walking
      a.set(side * 0.1, 0.95, 0);
      b.set(side * 0.1, 0.52, -0.2 * k - 0.02);
      c.set(side * 0.1, 0.1 + Math.max(0, k) * 0.06, -0.28 * k);
      // seated: thighs forward over the seat, shins down
      const hip = tmp.set(side * 0.1, 0.56, 0.05);
      a.lerp(hip, s);
      b.lerp(tmp.set(side * 0.12, 0.56, -0.36), s);
      c.lerp(tmp.set(side * 0.12, 0.1, -0.42), s);
      thigh.aim(a, b);
      shin.aim(b, c);
      shoe.position.set(c.x, 0.04, c.z - 0.06);
    }
    const mouse = !reduced && w < 0.5 && s > 0.9 && Math.sin(t * 0.37 + this.phase) > 0.6;
    const carrying = this.ticket.visible;
    for (const { upper, fore, hand, side } of this.arms) {
      const k = -swing * side;
      a.set(side * 0.23, 1.52, 0);
      // walking arms swing; the right hand holds the card in front while carrying
      b.set(side * 0.27, 1.24, -0.12 * k);
      c.set(side * 0.25, 0.98, -0.2 * k - 0.06);
      if (carrying && side > 0) { b.set(0.26, 1.22, -0.1); c.set(0.16, 1.18, -0.34); }
      // seated arms type on the desk
      const tap = reduced ? 0 : w * 0.014 * Math.max(0, Math.sin(t * 15 + (side > 0 ? 0 : Math.PI) + this.phase));
      const reach = mouse && side > 0 ? 0.16 : 0;
      b.lerp(tmp.set(side * 0.27, 1.26, -0.16), s);
      c.lerp(tmp.set(side * (0.13 + reach), 1.2 + tap, -0.4 - 0.04 * w), s);
      upper.aim(a, b);
      fore.aim(b, c);
      hand.position.copy(c).add(tmp.set(0, -0.01, -0.04));
      if (carrying && side > 0) this.ticket.position.copy(c).add(tmp.set(-0.02, 0.05, -0.06));
    }
  }
}
