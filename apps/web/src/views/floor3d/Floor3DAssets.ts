// Loads the 3D floor's people, animations and PBR textures from /floor3d/ (apps/web/public/floor3d,
// see ASSETS.md there for sources and CC0 licences). Its own on-demand chunk, imported only by the
// engine. Plain .gltf + .bin + .webp files: no WebAssembly decoder and no blob: URLs, so the
// dashboard's Content-Security-Policy ("script-src 'self'", "img-src 'self' data:") holds.
import * as THREE from "three";
import { type GLTF, GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";

export type Pbr = { color: THREE.Texture; normal: THREE.Texture; roughness: THREE.Texture | null };
export type FloorAssets = {
  male: GLTF;
  female: GLTF;
  clips: THREE.AnimationClip[];
  skins: { male: THREE.Texture; female: THREE.Texture };
  floor: Pbr;
  desk: Pbr;
  fabric: Pbr;
  metal: Pbr;
  wall: Pbr;
  cloneSkinned: (o: THREE.Object3D) => THREE.Object3D;
};

const BASE = `${import.meta.env.BASE_URL}floor3d/`;

/** Loads everything; `progress` gets 0..1 as files arrive. Rejects if any file fails. */
export async function loadFloorAssets(progress: (fraction: number) => void, anisotropy: number): Promise<FloorAssets> {
  const manager = new THREE.LoadingManager();
  manager.onProgress = (_url, loaded, total) => progress(total ? loaded / total : 0);
  const gltf = new GLTFLoader(manager);
  const tex = new THREE.TextureLoader(manager);
  const texture = (path: string, color: boolean, repeat = 1) =>
    tex.loadAsync(BASE + path).then(t => {
      t.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      t.repeat.set(repeat, repeat);
      t.anisotropy = anisotropy;
      return t;
    });
  const pbr = async (key: string, roughness: boolean): Promise<Pbr> => ({
    color: await texture(`textures/${key}_color.webp`, true),
    normal: await texture(`textures/${key}_normal.webp`, false),
    roughness: roughness ? await texture(`textures/${key}_roughness.webp`, false) : null,
  });
  const skin = (path: string) => tex.loadAsync(BASE + path).then(t => { t.colorSpace = THREE.SRGBColorSpace; t.flipY = false; return t; });
  const [male, female, anims, maleLight, femaleLight, floor, desk, fabric, metal, wall] = await Promise.all([
    gltf.loadAsync(`${BASE}people/male.gltf`),
    gltf.loadAsync(`${BASE}people/female.gltf`),
    gltf.loadAsync(`${BASE}people/anims.glb`),
    skin("people/male_light.webp"),
    skin("people/female_light.webp"),
    pbr("floor", true), pbr("desk", true), pbr("fabric", false), pbr("metal", true), pbr("wall", true),
  ]);
  return { male, female, clips: anims.animations, skins: { male: maleLight, female: femaleLight }, floor, desk, fabric, metal, wall, cloneSkinned };
}
