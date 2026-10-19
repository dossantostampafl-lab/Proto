// Rigged people for the 3D room: Quaternius Universal Base Characters (CC0) animated with the
// Universal Animation Library (CC0) through an AnimationMixer. They are dressed in the shader: every
// vertex knows how much of it is shirt, trousers or shoes from the bones that move it, and those
// parts take a fabric colour and a woven normal instead of the skin. What they do is decided by the
// engine: operators carry work between desks only when a live floor event arrives, roster agents sit
// at the trading bench, floor staff walk between desks so the room is alive.
import * as THREE from "three";
import type { FloorAssets } from "./Floor3DAssets";

export type Look = {
  sex: "male" | "female";
  light: boolean;
  hair: string; // name of the hair mesh to show
  beard?: boolean;
  hairColor: string;
  shirt: string;
  trousers: string;
  shoes: string;
};

const SHIRT = /^(spine_0[1-3]|clavicle_[lr]|upperarm_[lr]|lowerarm_[lr])$/;
const TROUSERS = /^(pelvis|thigh_[lr]|calf_[lr])$/;
const SHOES = /^(foot_[lr]|ball_[lr]|ball_leaf_[lr])$/;
const dressed = new WeakSet<THREE.BufferGeometry>();

/** Per-vertex clothing weights (shirt, trousers, shoes) from the skin weights of the body mesh. */
function dress(mesh: THREE.SkinnedMesh) {
  const geo = mesh.geometry;
  if (dressed.has(geo)) return;
  dressed.add(geo);
  const idx = geo.getAttribute("skinIndex"), wts = geo.getAttribute("skinWeight");
  const names = mesh.skeleton.bones.map(b => b.name);
  const out = new Float32Array(idx.count * 3);
  for (let i = 0; i < idx.count; i++) {
    for (let k = 0; k < 4; k++) {
      const name = names[idx.getComponent(i, k)] ?? "", w = wts.getComponent(i, k);
      if (SHIRT.test(name)) out[i * 3] += w;
      else if (TROUSERS.test(name)) out[i * 3 + 1] += w;
      else if (SHOES.test(name)) out[i * 3 + 2] += w;
    }
  }
  geo.setAttribute("cloth", new THREE.BufferAttribute(out, 3));
}

/** The body material with clothes: fabric colour, roughness and a woven normal where clothed. */
function clothed(base: THREE.MeshStandardMaterial, look: Look, skin: THREE.Texture | null, fabric: THREE.Texture) {
  const m = base.clone();
  if (skin) m.map = skin;
  m.envMapIntensity = 0.7;
  const uniforms = {
    uShirt: { value: new THREE.Color(look.shirt) },
    uTrousers: { value: new THREE.Color(look.trousers) },
    uShoes: { value: new THREE.Color(look.shoes) },
    uFabric: { value: fabric },
  };
  m.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 cloth;\nvarying vec3 vCloth;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvCloth = cloth;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vCloth;\nuniform vec3 uShirt;\nuniform vec3 uTrousers;\nuniform vec3 uShoes;\nuniform sampler2D uFabric;\nfloat clothMask;\nvec3 clothShare;")
      .replace("#include <map_fragment>", `#include <map_fragment>
        float clothSum = vCloth.x + vCloth.y + vCloth.z;
        clothShare = vCloth / max(clothSum, 1e-3);
        clothMask = smoothstep(0.45, 0.55, clothSum);
        // A crisp waistband between shirt and trousers, and between trousers and shoes.
        vec3 clothColor = mix(uTrousers, uShirt, smoothstep(-0.06, 0.06, vCloth.x - vCloth.y));
        clothColor = mix(clothColor, uShoes, smoothstep(0.45, 0.6, clothShare.z));
        diffuseColor.rgb = mix(diffuseColor.rgb, clothColor, clothMask);`)
      .replace("#include <roughnessmap_fragment>", `#include <roughnessmap_fragment>
        roughnessFactor = mix(roughnessFactor, mix(0.86, 0.32, clothShare.z), clothMask);`)
      .replace("#include <normal_fragment_maps>", `#include <normal_fragment_maps>
        #ifdef USE_NORMALMAP_TANGENTSPACE
          vec3 fabN = texture2D(uFabric, vNormalMapUv * 7.0).xyz * 2.0 - 1.0;
          fabN.xy *= 0.55;
          normal = normalize(mix(normal, normalize(tbn * fabN), clothMask));
        #endif`);
  };
  m.customProgramCacheKey = () => "proto-clothed-v1";
  return m;
}

type Step =
  | { kind: "walk"; to: THREE.Vector3 }
  | { kind: "face"; heading: number }
  | { kind: "sit"; on: boolean }
  | { kind: "wait"; seconds: number }
  | { kind: "show"; on: boolean }
  | { kind: "call"; fn: () => void };

type Clip = "idle" | "talk" | "walk" | "walkFormal" | "sitIdle" | "sitTalk" | "sitEnter" | "sitExit";
const CLIPS: Record<Clip, string> = {
  idle: "Idle_Loop", talk: "Idle_Talking_Loop", walk: "Walk_Loop", walkFormal: "Walk_Formal_Loop",
  sitIdle: "Sitting_Idle_Loop", sitTalk: "Sitting_Talking_Loop", sitEnter: "Sitting_Enter", sitExit: "Sitting_Exit",
};
const SPEED = 1.25; // m/s, matched to the walk cycle
const tmp = new THREE.Vector3();

export class Human {
  readonly group = new THREE.Group();
  private readonly mixer: THREE.AnimationMixer;
  private readonly actions = {} as Record<Clip, THREE.AnimationAction>;
  private current: Clip | null = null;
  private readonly steps: Step[] = [];
  private seated = false;
  private heading = 0;
  private waited = 0;
  private transition = 0; // seconds left of a sit/stand clip
  private readonly materials: THREE.Material[] = [];
  private tag: THREE.Sprite | null = null;
  private readonly walkClip: Clip;

  constructor(assets: FloorAssets, look: Look, private readonly phase: number) {
    const src = look.sex === "male" ? assets.male : assets.female;
    const model = assets.cloneSkinned(src.scene);
    const hairColor = new THREE.Color(look.hairColor);
    model.traverse(o => {
      if (!(o instanceof THREE.Mesh)) return;
      o.castShadow = true;
      o.receiveShadow = true;
      o.frustumCulled = false; // skinned bounds are the bind pose; walking people must never pop out
      const name = o.name;
      const isHair = /^Hair_/.test(name);
      if (isHair) {
        o.visible = name === look.hair || (look.beard === true && name === "Hair_Beard");
        const mat = (o.material as THREE.MeshStandardMaterial).clone();
        mat.color.copy(hairColor);
        mat.envMapIntensity = 0.5;
        o.material = mat;
        this.materials.push(mat);
        return;
      }
      const mat = o.material as THREE.MeshStandardMaterial;
      if (o instanceof THREE.SkinnedMesh && /^MI_Superhero/.test(mat.name)) {
        dress(o);
        const skin = look.light ? (look.sex === "male" ? assets.skins.male : assets.skins.female) : null;
        o.material = clothed(mat, look, skin, assets.fabric.normal);
        this.materials.push(o.material as THREE.Material);
      } else if (/Eyebrows/.test(name) || /Hair/.test(mat.name)) {
        const m = mat.clone();
        m.color.copy(hairColor);
        o.material = m;
        this.materials.push(m);
      }
    });
    this.group.add(model);
    this.mixer = new THREE.AnimationMixer(model);
    for (const [key, name] of Object.entries(CLIPS) as [Clip, string][]) {
      const clip = assets.clips.find(c => c.name === name);
      if (!clip) throw new Error(`animation ${name} missing`);
      const action = this.mixer.clipAction(clip);
      if (key === "sitEnter" || key === "sitExit") { action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true; }
      this.actions[key] = action;
    }
    this.walkClip = look.sex === "female" || phase % 2 > 1 ? "walk" : "walkFormal";
    this.play("idle", 0);
    this.mixer.setTime(phase * 1.3);
  }

  private play(clip: Clip, fade = 0.35) {
    if (this.current === clip) return;
    const next = this.actions[clip];
    next.reset().setEffectiveWeight(1).play();
    if (this.current) this.actions[this.current].crossFadeTo(next, fade, false);
    this.current = clip;
  }

  /** A small label over the head: agent code and virtual equity, or nothing. */
  setTag(texture: THREE.Texture | null) {
    if (!texture) {
      if (this.tag) { this.group.remove(this.tag); this.tag.material.dispose(); this.tag = null; }
      return;
    }
    if (!this.tag) {
      this.tag = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, toneMapped: false }));
      this.tag.scale.set(0.95, 0.2375, 1);
      this.tag.renderOrder = 12;
      this.group.add(this.tag);
    } else this.tag.material.map = texture;
  }

  get busy() { return this.steps.length > 0; }
  get queued() { return this.steps.length; }
  get isSeated() { return this.seated; }

  place(at: THREE.Vector3, heading: number, seated: boolean) {
    this.group.position.copy(at);
    this.heading = heading;
    this.group.rotation.y = heading;
    this.seated = seated;
    this.play(seated ? "sitIdle" : "idle", 0);
  }

  plan(...steps: Step[]) { this.steps.push(...steps); }
  walk(points: THREE.Vector3[]) { this.plan(...points.map(p => ({ kind: "walk" as const, to: p.clone() }))); }

  update(dt: number, working: boolean, talking: boolean, reduced: boolean) {
    let moving = false;
    if (this.transition > 0) this.transition -= dt;
    for (let guard = 0; guard < 10 && this.steps.length && this.transition <= 0; guard++) {
      const s = this.steps[0];
      if (s.kind === "sit") {
        if (s.on !== this.seated) {
          this.seated = s.on;
          if (!reduced) {
            this.play(s.on ? "sitEnter" : "sitExit", 0.25);
            this.transition = this.actions[s.on ? "sitEnter" : "sitExit"].getClip().duration * 0.92;
            this.steps.shift();
            break;
          }
        }
      } else if (s.kind === "face") {
        this.heading = s.heading;
        if (reduced) this.group.rotation.y = s.heading;
      } else if (s.kind === "show") {
        this.group.visible = s.on;
      } else if (s.kind === "call") {
        s.fn();
      } else if (s.kind === "wait") {
        this.waited += dt;
        if (this.waited < s.seconds) break;
        this.waited = 0;
      } else {
        const to = tmp.subVectors(s.to, this.group.position).setY(0);
        const dist = to.length();
        if (reduced || dist < 0.05) {
          // Reduced motion: people are placed at their destination, never animated across the room.
          if (reduced) this.group.position.set(s.to.x, 0, s.to.z);
        } else {
          moving = true;
          to.normalize();
          this.group.position.addScaledVector(to, Math.min(dist, SPEED * dt));
          this.heading = Math.atan2(to.x, to.z);
          break;
        }
      }
      this.steps.shift();
    }
    if (reduced) this.group.rotation.y = this.heading;
    else {
      let d = this.heading - this.group.rotation.y;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      this.group.rotation.y += d * Math.min(1, dt * 7);
    }
    if (this.transition <= 0) {
      if (moving) this.play(this.walkClip, 0.25);
      else if (this.seated) this.play(working || talking ? "sitTalk" : "sitIdle", 0.5);
      else this.play(talking ? "talk" : "idle", 0.35);
    }
    if (this.tag) this.tag.position.set(0, this.seated && this.transition <= 0 ? 1.62 : 2.02, 0);
    this.mixer.update(reduced && this.transition <= 0 ? 0 : dt);
  }

  dispose() {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.mixer.getRoot());
    this.materials.forEach(m => m.dispose());
    this.tag?.material.dispose();
  }
}
