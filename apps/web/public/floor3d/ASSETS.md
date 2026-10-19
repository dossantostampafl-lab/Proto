# 3D floor assets

Loaded only by the 3D trading floor (`src/views/floor3d/Floor3DAssets.ts`), never up front.
Every file here is CC0 1.0 (public domain); redistribution in this repository is allowed.

| Files | Source | Author | Licence |
|---|---|---|---|
| `people/male.gltf`, `male.bin`, `male_*.webp`, `people/female.gltf`, `female.bin`, `female_*.webp`, `male_light.webp`, `female_light.webp` | Universal Base Characters [Standard] (Superhero male/female bodies, eyes, eyebrows; hairstyles Hair_SimpleParted, Hair_Buzzed, Hair_Beard, Hair_Long, Hair_Buns, Hair_BuzzedFemale) — https://quaternius.itch.io/universal-base-characters (also https://quaternius.com/packs/universalbasecharacters.html) | Quaternius | CC0 1.0 (`License_Standard.txt` in the pack) |
| `people/anims.glb` | Universal Animation Library [Standard], clips Idle_Loop, Idle_Talking_Loop, Walk_Loop, Walk_Formal_Loop, Sitting_Enter, Sitting_Exit, Sitting_Idle_Loop, Sitting_Talking_Loop — https://quaternius.itch.io/universal-animation-library | Quaternius | CC0 1.0 (`License.txt` in the pack) |
| `textures/floor_*.webp` | Wood Floor 051 — https://ambientcg.com/view?id=WoodFloor051 | ambientCG (Lennart Demes) | CC0 1.0 |
| `textures/desk_*.webp` | Wood 049 — https://ambientcg.com/view?id=Wood049 | ambientCG | CC0 1.0 |
| `textures/fabric_*.webp` | Fabric 030 — https://ambientcg.com/view?id=Fabric030 | ambientCG | CC0 1.0 |
| `textures/metal_*.webp` | Metal 009 — https://ambientcg.com/view?id=Metal009 | ambientCG | CC0 1.0 |
| `textures/wall_*.webp` | Concrete 034 — https://ambientcg.com/view?id=Concrete034 | ambientCG | CC0 1.0 |

## How they were prepared

- Hair meshes were bound to each body's own skeleton (same rig, same joint names).
- Geometry: welded, deduplicated, `KHR_mesh_quantization` (no decoder needed).
- Textures: resized to 1024 px (fabric 512 px) and re-encoded as WebP.
- Animations: only the eight clips above, rotation tracks only (translation on the root bones), no
  finger tracks, resampled.
- Files are plain `.gltf` + `.bin` + `.webp`: no WebAssembly decoder and no `blob:` URLs, so the
  dashboard's Content-Security-Policy (`script-src 'self'`, `img-src 'self' data:`) holds.

The people are dressed in the shader (`people.ts`): shirt, trousers and shoes come from the bones
that move each vertex, so no clothing asset is needed. Budget: `scripts/bundle-budget-check.mjs`.
