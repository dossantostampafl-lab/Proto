// Procedural people for the 3D room. They are decoration; the only thing they express is the
// verified activity of their desk: an operator types and leans in only while the desk is working.
import * as THREE from "three";

export type Look = { skin: string; hair: string; suit: string; long?: boolean };

const Y = new THREE.Vector3(0, 1, 0);
const tmp = new THREE.Vector3();

/** A capsule that is re-aimed every frame from point a to point b. */
class Limb {
  readonly mesh: THREE.Mesh;
  private readonly length: number;
  constructor(radius: number, length: number, material: THREE.Material) {
    this.length = length;
    this.mesh = new THREE.Mesh(new THREE.CapsuleGeometry(radius, length, 4, 10), material);
    this.mesh.castShadow = true;
  }
  aim(a: THREE.Vector3, b: THREE.Vector3) {
    tmp.subVectors(b, a);
    const len = tmp.length();
    this.mesh.position.copy(a).addScaledVector(tmp, 0.5);
    this.mesh.quaternion.setFromUnitVectors(Y, tmp.normalize());
    this.mesh.scale.set(1, Math.max(0.2, len / (this.length + 0.0001)), 1);
  }
}

const mat = (color: string, roughness: number, metalness = 0) => new THREE.MeshStandardMaterial({ color, roughness, metalness });

function chair(): THREE.Group {
  const g = new THREE.Group();
  const black = mat("#141619", 0.55, 0.2), metal = mat("#5a6067", 0.35, 0.85), mesh = mat("#1b1e22", 0.85);
  for (let i = 0; i < 5; i++) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.03, 0.3), metal);
    leg.position.set(Math.sin((i * 2 * Math.PI) / 5) * 0.15, 0.05, Math.cos((i * 2 * Math.PI) / 5) * 0.15);
    leg.rotation.y = (i * 2 * Math.PI) / 5;
    g.add(leg);
  }
  const lift = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.03, 0.38, 12), metal);
  lift.position.y = 0.26;
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.52, 0.09, 0.5), black);
  seat.position.y = 0.48;
  const back = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.72, 0.07), mesh);
  back.position.set(0, 0.9, 0.26); back.rotation.x = 0.08;
  const frame = new THREE.Mesh(new THREE.BoxGeometry(0.54, 0.05, 0.09), black);
  frame.position.set(0, 1.26, 0.29);
  const spine = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.4, 0.04), metal);
  spine.position.set(0, 0.62, 0.3);
  for (const side of [-1, 1]) {
    const arm = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.04, 0.3), black);
    arm.position.set(side * 0.28, 0.7, 0.02);
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.2, 0.03), metal);
    post.position.set(side * 0.28, 0.6, 0.1);
    g.add(arm, post);
  }
  g.add(lift, seat, back, frame, spine);
  g.traverse(o => { if (o instanceof THREE.Mesh) { o.castShadow = true; o.receiveShadow = true; } });
  return g;
}

function head(look: Look) {
  const skin = mat(look.skin, 0.55), hair = mat(look.hair, 0.7);
  const g = new THREE.Group();
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.1, 24, 18), skin);
  skull.scale.set(0.92, 1.15, 1);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.106, 24, 18, 0, Math.PI * 2, 0, Math.PI * 0.62), hair);
  cap.scale.set(0.95, 1.15, 1.05); cap.position.set(0, 0.012, 0.01); cap.rotation.x = 0.35;
  g.add(skull, cap);
  for (const side of [-1, 1]) {
    const ear = new THREE.Mesh(new THREE.SphereGeometry(0.022, 10, 8), skin);
    ear.scale.set(0.5, 1, 0.8); ear.position.set(side * 0.092, -0.005, 0.01);
    g.add(ear);
  }
  if (look.long) {
    const fall = new THREE.Mesh(new THREE.CapsuleGeometry(0.09, 0.16, 4, 12), hair);
    fall.position.set(0, -0.12, 0.05); fall.scale.set(1.15, 1, 0.55);
    g.add(fall);
  }
  g.traverse(o => { if (o instanceof THREE.Mesh) o.castShadow = true; });
  return g;
}

/** An operator seated at a desk, facing -z (towards the desk). Origin: chair centre on the floor. */
export class Operator {
  readonly group = new THREE.Group();
  private readonly body = new THREE.Group();
  private readonly head: THREE.Group;
  private readonly arms: { upper: Limb; fore: Limb; hand: THREE.Mesh; side: number }[] = [];
  private readonly torso: THREE.Mesh;
  private level = 0; // 0 idle .. 1 working, eased
  private readonly phase: number;

  constructor(look: Look, phase: number) {
    this.phase = phase;
    const suit = mat(look.suit, 0.82), skin = mat(look.skin, 0.55);
    this.group.add(chair(), this.body);
    this.torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.32, 6, 16), suit);
    this.torso.scale.set(1.15, 1, 0.8); this.torso.position.set(0, 0.86, 0.04); this.torso.castShadow = true;
    const shoulders = new THREE.Mesh(new THREE.CapsuleGeometry(0.08, 0.32, 4, 12), suit);
    shoulders.rotation.z = Math.PI / 2; shoulders.position.set(0, 1.1, 0.04); shoulders.castShadow = true;
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.05, 0.1, 12), skin);
    neck.position.set(0, 1.2, 0.03);
    this.head = head(look);
    this.head.position.set(0, 1.34, 0.02);
    this.body.add(this.torso, shoulders, neck, this.head);
    for (const side of [-1, 1]) {
      const thigh = new Limb(0.07, 0.36, suit);
      thigh.aim(new THREE.Vector3(side * 0.1, 0.55, 0.05), new THREE.Vector3(side * 0.12, 0.55, -0.35));
      this.body.add(thigh.mesh);
      const upper = new Limb(0.055, 0.26, suit), fore = new Limb(0.045, 0.24, suit);
      const hand = new THREE.Mesh(new THREE.SphereGeometry(0.04, 12, 10), skin);
      hand.scale.set(1, 0.6, 1.3); hand.castShadow = true;
      this.body.add(upper.mesh, fore.mesh, hand);
      this.arms.push({ upper, fore, hand, side });
    }
    this.pose(0, false);
  }

  private pose(t: number, reduced: boolean) {
    const w = this.level, idle = reduced ? 0 : 1 - w;
    const breathe = reduced ? 0 : Math.sin(t * 1.7 + this.phase) * 0.008;
    this.torso.scale.y = 1 + breathe;
    this.body.rotation.x = -0.05 - 0.1 * w; // lean into the desk while working
    this.head.rotation.y = idle * 0.38 * Math.sin(t * 0.21 + this.phase) * Math.sin(t * 0.07 + this.phase * 2);
    this.head.rotation.x = 0.1 + 0.12 * w + (reduced ? 0 : w * 0.03 * Math.sin(t * 3.1 + this.phase));
    const mouse = !reduced && w < 0.5 && Math.sin(t * 0.37 + this.phase) > 0.6;
    for (const { upper, fore, hand, side } of this.arms) {
      const shoulder = new THREE.Vector3(side * 0.22, 1.08, 0.04);
      const tap = reduced ? 0 : w * 0.014 * Math.max(0, Math.sin(t * 15 + (side > 0 ? 0 : Math.PI) + this.phase));
      const reach = mouse && side > 0 ? 0.16 : 0;
      const wrist = new THREE.Vector3(side * (0.13 + reach), 0.8 + tap, -0.34 - 0.04 * w);
      const elbow = new THREE.Vector3(side * 0.27, 0.86, -0.12);
      upper.aim(shoulder, elbow);
      fore.aim(elbow, wrist);
      hand.position.copy(wrist).add(new THREE.Vector3(0, -0.01, -0.04));
    }
  }

  update(t: number, dt: number, busy: boolean, reduced: boolean) {
    this.level += ((busy ? 1 : 0) - this.level) * Math.min(1, dt * 3);
    this.group.rotation.y = reduced ? 0 : (1 - this.level) * 0.06 * Math.sin(t * 0.17 + this.phase);
    this.pose(t, reduced);
  }
}

/** A standing supervisor who walks a route and goes to a desk that raised an alert. */
export class Walker {
  readonly group = new THREE.Group();
  private readonly legs: { thigh: Limb; shin: Limb; shoe: THREE.Mesh; side: number }[] = [];
  private readonly arms: { upper: Limb; fore: Limb; side: number }[] = [];
  private readonly head: THREE.Group;
  private stride = 0;
  private pause = 0;
  private leg = 0;
  private readonly route: THREE.Vector3[];

  constructor(look: Look, route: THREE.Vector3[]) {
    this.route = route;
    const suit = mat(look.suit, 0.8), shoes = mat("#0b0c0d", 0.4, 0.2);
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.42, 6, 16), suit);
    torso.scale.set(1.15, 1, 0.75); torso.position.y = 1.3; torso.castShadow = true;
    const shoulders = new THREE.Mesh(new THREE.CapsuleGeometry(0.08, 0.32, 4, 12), suit);
    shoulders.rotation.z = Math.PI / 2; shoulders.position.y = 1.56; shoulders.castShadow = true;
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.05, 0.1, 12), mat(look.skin, 0.55));
    neck.position.y = 1.66;
    this.head = head(look);
    this.head.position.y = 1.8;
    this.group.add(torso, shoulders, neck, this.head);
    for (const side of [-1, 1]) {
      const thigh = new Limb(0.075, 0.38, suit), shin = new Limb(0.06, 0.38, suit);
      const shoe = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.07, 0.26), shoes);
      shoe.castShadow = true;
      this.group.add(thigh.mesh, shin.mesh, shoe);
      this.legs.push({ thigh, shin, shoe, side });
      const upper = new Limb(0.055, 0.28, suit), fore = new Limb(0.047, 0.26, suit);
      this.group.add(upper.mesh, fore.mesh);
      this.arms.push({ upper, fore, side });
    }
    this.group.position.copy(route[0]);
  }

  update(t: number, dt: number, target: THREE.Vector3 | null, reduced: boolean) {
    const goal = target ?? this.route[this.leg % this.route.length];
    const to = tmp.subVectors(goal, this.group.position).setY(0);
    const dist = to.length();
    let speed = 0;
    if (dist > 0.12 && this.pause <= 0 && !reduced) {
      speed = Math.min(0.85, dist * 1.5);
      to.normalize();
      this.group.position.addScaledVector(to, speed * dt);
      const heading = Math.atan2(to.x, to.z);
      let delta = heading - this.group.rotation.y;
      delta = Math.atan2(Math.sin(delta), Math.cos(delta));
      this.group.rotation.y += delta * Math.min(1, dt * 4);
    } else if (!target && dist <= 0.12) {
      if (this.pause <= 0) this.pause = 4;
      this.pause -= dt;
      if (this.pause <= 0) this.leg++;
    }
    this.stride += speed * dt * 5.2;
    const swing = Math.sin(this.stride) * Math.min(1, speed * 1.6);
    for (const { thigh, shin, shoe, side } of this.legs) {
      const s = swing * side;
      const hip = new THREE.Vector3(side * 0.1, 0.95, 0);
      const knee = new THREE.Vector3(side * 0.1, 0.52, 0.22 * s + 0.02);
      const ankle = new THREE.Vector3(side * 0.1, 0.1 + Math.max(0, -s) * 0.06, 0.3 * s);
      thigh.aim(hip, knee);
      shin.aim(knee, ankle);
      shoe.position.set(side * 0.1, 0.04, ankle.z + 0.06);
    }
    for (const { upper, fore, side } of this.arms) {
      const s = -swing * side;
      const shoulder = new THREE.Vector3(side * 0.23, 1.53, 0);
      const elbow = new THREE.Vector3(side * 0.27, 1.25, 0.12 * s);
      const wrist = new THREE.Vector3(side * 0.25, 0.98, 0.2 * s + 0.06);
      upper.aim(shoulder, elbow);
      fore.aim(elbow, wrist);
    }
    // Look at the wall screens when standing still.
    this.head.rotation.y = speed > 0.05 ? 0 : (reduced ? 0 : 0.25 * Math.sin(t * 0.4));
    this.head.rotation.x = speed > 0.05 ? 0.05 : -0.06;
  }
}
