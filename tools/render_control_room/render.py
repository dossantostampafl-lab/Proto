"""Render the "Controle" mission-control room for the web dashboard.

Offline, reproducible art pipeline (not part of the API or CI):

    pip install bpy==5.2.3 pillow
    python tools/render_control_room/render.py            # final quality
    python tools/render_control_room/render.py --preview  # fast, low samples

It models the whole room in code (no third-party assets), renders it with Cycles on the CPU,
adds bloom, and writes into apps/web:

    public/floorcontrol/room.webp            landscape background (no people, no text, no numbers)
    public/floorcontrol/room-portrait.webp   portrait background for phones
    public/floorcontrol/robot.webp           PROTO's own robot, 4 walk frames, transparent
    src/views/floorControl/anchors.json      where seats, hub targets and the globe land on each image

Screens glow with abstract panels only; every value on the view is drawn live by the web app.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
# The globe floats clear of the floor and the console ring; anchors project the same numbers.
GLOBE_Z, GLOBE_R = 5.4, 4.4
RING_R = 11.6  # between the second and third glowing rings
WEB = ROOT / "apps" / "web"
PUBLIC = WEB / "public" / "floorcontrol"
ANCHORS = WEB / "src" / "views" / "floorControl" / "anchors.json"
WORK = Path(__file__).resolve().parent / "build"

random.seed(7)


def lin(hex_color: str) -> tuple[float, float, float]:
    """sRGB hex to linear RGB."""
    h = hex_color.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i : i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out[0], out[1], out[2]


# ---------- textures painted with Pillow ----------

LAND = [
    [(-168, 66), (-140, 70), (-95, 72), (-75, 62), (-60, 50), (-80, 30), (-97, 18), (-83, 9), (-105, 22), (-118, 33), (-125, 48), (-150, 60)],
    [(-80, 10), (-62, 11), (-35, -6), (-40, -22), (-58, -38), (-70, -55), (-75, -40), (-70, -18), (-81, -5)],
    [(-18, 15), (-5, 36), (12, 37), (32, 31), (43, 12), (51, 11), (40, -15), (30, -34), (18, -34), (12, -16), (9, 4), (-8, 5)],
    [(-10, 36), (-9, 44), (0, 51), (10, 58), (25, 70), (60, 70), (100, 77), (140, 72), (175, 66), (160, 58), (140, 50), (122, 40), (122, 25), (105, 10), (98, 18), (80, 8), (72, 20), (57, 25), (48, 30), (35, 36), (25, 40), (12, 44)],
    [(113, -22), (122, -14), (136, -12), (145, -15), (153, -27), (146, -39), (135, -35), (115, -34)],
    [(-50, 60), (-30, 70), (-22, 82), (-55, 82), (-70, 76)],
    [(95, -5), (120, -2), (140, -6), (128, -9), (105, -8)],
    [(44, -12), (50, -16), (47, -25), (43, -20)],
    [(130, 31), (141, 41), (145, 44), (138, 35)],
    [(-6, 50), (2, 51), (-2, 58), (-6, 57)],
]


def land_rings() -> list[list[tuple[float, float]]]:
    """Coastlines of Natural Earth 1:110m land (public domain, naturalearthdata.com), lon/lat rings."""
    data = json.loads((Path(__file__).resolve().parent / "ne_110m_land.geojson").read_text())
    rings: list[list[tuple[float, float]]] = []
    for feature in data["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        rings.extend([(float(lon), float(lat)) for lon, lat in polygon[0]] for polygon in polygons)
    return rings


def earth_texture(path: Path) -> None:
    w, h = 2048, 1024
    img = Image.new("RGB", (w, h))
    grad = np.linspace(0, 1, h)[:, None]
    sea = (np.array([8, 40, 110]) * (1 - np.abs(grad - 0.5)) + np.array([4, 22, 70]) * np.abs(grad - 0.5) * 2)
    img = Image.fromarray(np.repeat(sea[:, None, :], w, axis=1).reshape(h, w, 3).astype(np.uint8))
    mask = Image.new("L", (w, h))
    d = ImageDraw.Draw(mask)
    to = lambda lon, lat: ((lon + 180) / 360 * w, (90 - lat) / 180 * h)  # noqa: E731
    for ring in land_rings():
        d.polygon([to(*p) for p in ring], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    land = Image.new("RGB", (w, h), (24, 170, 171))
    tone = Image.effect_noise((w, h), 60).filter(ImageFilter.GaussianBlur(10)).convert("RGB")
    land = Image.blend(land, Image.merge("RGB", [tone.getchannel(0).point(lambda v: v // 3), tone.getchannel(0), tone.getchannel(0).point(lambda v: v // 2)]), 0.25)
    img.paste(land, (0, 0), mask)
    coast = mask.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(1.5))
    img.paste(Image.new("RGB", (w, h), (151, 255, 243)), (0, 0), coast.point(lambda v: min(255, v * 3)))
    grid = ImageDraw.Draw(img)
    for lon in range(-180, 181, 15):
        x = (lon + 180) / 360 * w
        grid.line([(x, 0), (x, h)], fill=(90, 160, 230), width=1)
    for lat in range(-75, 76, 15):
        y = (90 - lat) / 180 * h
        grid.line([(0, y), (w, y)], fill=(90, 160, 230), width=1)
    img.save(path)


def map_screen_texture(path: Path, seed: int) -> None:
    """A glowing world-map screen (Natural Earth coastlines) with scattered pins; no text, no numbers."""
    rnd = random.Random(seed)
    w, h = 512, 300
    img = Image.new("RGB", (w, h), (8, 30, 86))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(8, 30 + y // 10, 90 + y // 6))
    to = lambda lon, lat: ((lon + 180) / 360 * w, (84 - lat) / 144 * h)  # noqa: E731
    for ring in land_rings():
        d.polygon([to(*p) for p in ring], fill=(40, 150, 230), outline=(150, 225, 255))
    for _ in range(26):
        x, y = rnd.randint(20, w - 20), rnd.randint(30, h - 30)
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(110, 255, 200))
    d.rectangle([0, 0, w - 1, h - 1], outline=(140, 210, 255), width=3)
    img.filter(ImageFilter.GaussianBlur(0.5)).save(path)


def screen_texture(path: Path, seed: int) -> None:
    """A glowing dashboard screen: panels, bars and sweeps, no characters and no numbers."""
    rnd = random.Random(seed)
    w, h = 512, 300
    img = Image.new("RGB", (w, h), (10, 40, 110))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(12, 45 + y // 8, 120 + y // 5))
    d.rectangle([0, 0, w, 22], fill=(120, 200, 255))
    for col in range(3):
        for row in range(2):
            x0, y0 = 14 + col * 166, 34 + row * 132
            d.rectangle([x0, y0, x0 + 152, y0 + 118], fill=(30, 90, 190), outline=(140, 210, 255), width=2)
            kind = rnd.choice(["bars", "wave", "ring", "map"])
            if kind == "bars":
                for i in range(8):
                    bh = rnd.randint(15, 95)
                    d.rectangle([x0 + 10 + i * 17, y0 + 110 - bh, x0 + 22 + i * 17, y0 + 110], fill=(110, 220, 255))
            elif kind == "wave":
                pts = [(x0 + 6 + i * 7, y0 + 60 + math.sin(i * 0.6 + seed) * 30 * rnd.random()) for i in range(21)]
                d.line(pts, fill=(160, 240, 255), width=3)
            elif kind == "ring":
                d.ellipse([x0 + 36, y0 + 18, x0 + 116, y0 + 98], outline=(130, 230, 255), width=8)
                d.arc([x0 + 36, y0 + 18, x0 + 116, y0 + 98], 0, rnd.randint(90, 300), fill=(90, 255, 170), width=8)
            else:
                for _ in range(14):
                    cx, cy = x0 + rnd.randint(10, 140), y0 + rnd.randint(10, 108)
                    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(120, 255, 190))
    img.filter(ImageFilter.GaussianBlur(0.6)).save(path)


def floor_texture(path: Path) -> None:
    w = 1024
    img = Image.new("RGB", (w, w), (20, 52, 118))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, w - 1], outline=(52, 96, 170), width=4)
    d.line([(w // 2, 0), (w // 2, w)], fill=(34, 70, 140), width=3)
    d.line([(0, w // 2), (w, w // 2)], fill=(34, 70, 140), width=3)
    noise = Image.effect_noise((w, w), 12).convert("RGB")
    Image.blend(img, noise, 0.04).save(path)


# ---------- scene helpers ----------

class Kit:
    def __init__(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.meshes: dict[str, bpy.types.Mesh] = {}
        self.mats: dict[str, bpy.types.Material] = {}
        for name, op, kw in [
            ("cube", bpy.ops.mesh.primitive_cube_add, {"size": 1}),
            ("cyl", bpy.ops.mesh.primitive_cylinder_add, {"radius": 0.5, "depth": 1, "vertices": 32}),
            ("cyl64", bpy.ops.mesh.primitive_cylinder_add, {"radius": 0.5, "depth": 1, "vertices": 96}),
            ("sphere", bpy.ops.mesh.primitive_uv_sphere_add, {"radius": 1, "segments": 64, "ring_count": 32}),
            ("plane", bpy.ops.mesh.primitive_plane_add, {"size": 1}),
            ("cone", bpy.ops.mesh.primitive_cone_add, {"radius1": 0.5, "radius2": 0, "depth": 1, "vertices": 16}),
        ]:
            op(**kw)
            obj = bpy.context.active_object
            self.meshes[name] = obj.data
            if name in ("sphere", "cyl", "cyl64"):
                for p in obj.data.polygons:
                    p.use_smooth = True
            bpy.data.objects.remove(obj)

    def obj(self, mesh: str, mat: str, loc, scale=(1, 1, 1), rot=(0, 0, 0), name: str = "o") -> bpy.types.Object:
        o = bpy.data.objects.new(name, self.meshes[mesh])
        o.location = loc
        o.scale = scale
        o.rotation_euler = rot
        if mat:
            if len(o.data.materials) == 0:
                o.data.materials.append(None)
            o.material_slots[0].link = "OBJECT"
            o.material_slots[0].material = self.mats[mat]
        self.scene.collection.objects.link(o)
        return o

    def principled(self, name, color, rough=0.5, metal=0.0, emit=None, strength=0.0, transmission=0.0, coat=0.0):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*lin(color), 1)
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
        if emit:
            b.inputs["Emission Color"].default_value = (*lin(emit), 1)
            b.inputs["Emission Strength"].default_value = strength
        if transmission:
            b.inputs["Transmission Weight"].default_value = transmission
            b.inputs["IOR"].default_value = 1.45
        if coat:
            b.inputs["Coat Weight"].default_value = coat
        self.mats[name] = m
        return m

    def emissive(self, name, color="#ffffff", strength=5.0, image: Path | None = None, alpha: float | None = None):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Strength"].default_value = strength
        em.inputs["Color"].default_value = (*lin(color), 1)
        if image:
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(str(image))
            nt.links.new(tex.outputs["Color"], em.inputs["Color"])
        shader = em.outputs["Emission"]
        if alpha is not None:
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            mix = nt.nodes.new("ShaderNodeMixShader")
            mix.inputs["Fac"].default_value = alpha
            nt.links.new(tr.outputs[0], mix.inputs[1])
            nt.links.new(shader, mix.inputs[2])
            shader = mix.outputs[0]
        nt.links.new(shader, out.inputs["Surface"])
        self.mats[name] = m
        return m


# ---------- the room ----------

BLOCKS = [  # desk blocks: centre x, y (y grows away from the camera), columns, row pairs, floor
    (-23, 11.5, 5, 2, "binance"),
    (23, 11.5, 5, 2, "binance"),
    (-23, -2.5, 5, 1, "binance"),
    (23, -2.5, 5, 1, "binance"),
    (-23, -14.5, 5, 1, "xp"),
]
WALLS = [
    (-15, 20, -15, 5.5), (15, 20, 15, 5.5), (-15, -6, -15, -19.5), (15, -6, 15, -19.5),
    (-31, 3.5, -15, 3.5), (15, 3.5, 31, 3.5), (-31, -8.5, -15, -8.5), (15, -8.5, 31, -8.5),
    (-12, -19.5, -4.5, -19.5), (4.5, -19.5, 12, -19.5),
]
PLANTS = [(-15, 4.6), (15, 4.6), (-15, -5), (15, -5), (-14, -20.3), (14, -20.3), (-9, 17), (9, 17),
          (-30.5, 20.3), (30.5, 20.3), (30, -20), (-4.8, -18.6), (4.8, -18.6), (-30.5, -7.6), (30.5, 2.6)]


def build_room(k: Kit) -> list[dict]:
    WORK.mkdir(parents=True, exist_ok=True)
    floor_png, earth_png = WORK / "floor.png", WORK / "earth.png"
    floor_texture(floor_png)
    earth_texture(earth_png)
    screens = []
    for i in range(4):
        p = WORK / f"screen{i}.png"
        # Half the screens show the world map, as on the reference room's monitor walls.
        (map_screen_texture if i % 2 == 0 else screen_texture)(p, i + 3)
        screens.append(p)

    # Materials.
    fl = k.principled("floor", "#123a7a", rough=0.14, coat=0.7)
    nt = fl.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(floor_png))
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (16, 10, 1)
    nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    k.principled("well", "#081631", rough=0.12, coat=0.8)
    k.principled("rim", "#36557f", rough=0.3, metal=0.7)
    k.principled("desk", "#2a3442", rough=0.3, metal=0.3)
    k.principled("deskbody", "#151c27", rough=0.6)
    k.principled("black", "#0b0e14", rough=0.45)
    k.principled("operator", "#121b2b", rough=0.74)
    k.principled("operatorFace", "#886b62", rough=0.85)
    k.principled("console", "#2b3a52", rough=0.3, metal=0.5)
    k.principled("glass", "#bfe3ff", rough=0.03, transmission=1.0)
    k.principled("pot", "#e8ecef", rough=0.35)
    k.principled("leaf", "#1f7a3a", rough=0.6)
    k.principled("leaf2", "#2fa34f", rough=0.55)
    k.principled("sofa", "#3a4252", rough=0.8)
    k.principled("wall", "#0a1222", rough=0.5)
    k.emissive("amber", "#ff9a1f", 3.5)
    k.emissive("cyan", "#1e90ff", 2.2)
    k.emissive("cyanSoft", "#2b7de9", 1.6)
    k.emissive("ringBright", "#4cc3ff", 2.6)
    for i, p in enumerate(screens):
        k.emissive(f"screen{i}", strength=1.65, image=p)
    k.emissive("beam", "#38bdf8", 1.2, alpha=0.06)
    k.emissive("wire", "#38bdf8", 2.0, alpha=0.1)

    # Earth: its own texture plus a rim of cyan light.
    earth = bpy.data.materials.new("earth")
    earth.use_nodes = True
    nt = earth.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(str(earth_png))
    e1 = nt.nodes.new("ShaderNodeEmission")
    e1.inputs["Strength"].default_value = 0.95
    nt.links.new(t.outputs["Color"], e1.inputs["Color"])
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.2
    pw = nt.nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 2.0
    nt.links.new(lw.outputs["Facing"], pw.inputs[0])
    e2 = nt.nodes.new("ShaderNodeEmission")
    e2.inputs["Color"].default_value = (*lin("#7dd3fc"), 1)
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = 2.2
    nt.links.new(pw.outputs[0], mul.inputs[0])
    nt.links.new(mul.outputs[0], e2.inputs["Strength"])
    add = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(e1.outputs[0], add.inputs[0])
    nt.links.new(e2.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    k.mats["earth"] = earth

    # Floor, walls.
    k.obj("plane", "floor", (0, 0, 0), (66, 42, 1))
    for x1, y1, x2, y2 in [(-32, 20.5, 32, 20.5), (-32, -20.5, 32, -20.5), (-32.5, -20.5, -32.5, 20.5), (32.5, -20.5, 32.5, 20.5)]:
        ln, rz = math.hypot(x2 - x1, y2 - y1), math.atan2(y2 - y1, x2 - x1)
        k.obj("cube", "wall", ((x1 + x2) / 2, (y1 + y2) / 2, 0.8), (ln, 0.5, 1.6), (0, 0, rz))
        k.obj("cube", "cyanSoft", ((x1 + x2) / 2, (y1 + y2) / 2, 1.62), (ln, 0.12, 0.05), (0, 0, rz))


    # Dense elevated rear monitor gallery; all screens show abstract artwork,
    # never baked-in counts or status strings.
    for x in range(-27, 28, 6):
        k.obj("cube", "rim", (x, 19.6, 2.5), (5.0, 0.24, 3.25))
        k.obj("cube", "black", (x, 19.42, 2.5), (4.58, 0.1, 2.85))
        k.obj("plane", f"screen{(x + 27) // 6 % 4}", (x, 19.33, 2.5),
              (4.38, 2.67, 1), (math.pi / 2, 0, 0))
        k.obj("cube", "cyanSoft", (x, 19.25, 4.03), (4.75, 0.08, 0.04))
    for x in (-30.8, 30.8):
        for y in (-14.0, -5.0, 8.0, 16.0):
            k.obj("cube", "cyanSoft", (x, y, 0.035), (0.09, 5.5, 0.025))
    # Fine luminous floor seams orient the viewer to the central command hub.
    for side in (-1, 1):
        for y in (-17.5, -9.5, 7.5, 15.5):
            k.obj("cube", "amber", (side * 15.4, y, 0.05), (0.055, 5.5, 0.025))

    # Two tiers of side galleries frame the central holographic room.  The raised
    # desks are architectural details, not invented agents or API events.
    # Keep each platform outside the hub (inner edge x=16.4) and preserve
    # open walkways between the front and back sections.
    for side in (-1, 1):
        for gallery_y in (-12.3, 9.6):
            deck_x, deck_length = side * 24.3, 9.0
            k.obj("cube", "rim", (deck_x, gallery_y, 2.62), (15.8, deck_length, 0.34))
            k.obj("cube", "console", (deck_x, gallery_y, 2.82), (15.3, deck_length - 0.3, 0.08))
            # Thin glazed balustrade, a bright cyan cap and an amber step edge.
            inner_x = side * 16.45
            k.obj("cube", "glass", (inner_x, gallery_y, 3.26), (0.10, deck_length, 0.88))
            k.obj("cube", "cyanSoft", (inner_x, gallery_y, 3.72), (0.13, deck_length, 0.065))
            k.obj("cube", "amber", (deck_x, gallery_y - deck_length * 0.5, 2.83),
                  (15.8, 0.09, 0.07))
            # Console banks viewed from an elevated, oblique camera angle.
            for col in range(5):
                x = side * (18.35 + col * 2.65)
                for row in (-1, 1):
                    y = gallery_y + row * 1.5
                    k.obj("cube", "desk", (x, y, 3.31), (2.20, 1.00, 0.12))
                    k.obj("cube", "deskbody", (x, y, 3.10), (2.14, 0.92, 0.34))
                    k.obj("cube", "black", (x, y + 0.18, 3.93), (1.70, 0.09, 0.69),
                          (math.radians(-12), 0, 0))
                    k.obj("plane", f"screen{(col + (row > 0) + (side > 0)) % 4}",
                          (x, y + 0.10, 3.94), (1.60, 0.62, 1),
                          (math.radians(75), 0, 0))
                    k.obj("cube", "black", (x, y - 0.74, 3.22), (0.67, 0.54, 0.20))
                    k.obj("cube", "black", (x, y - 1.03, 3.49), (0.67, 0.10, 0.68))
            # Visible posts beneath the catwalk make the extra level read
            # as architecture rather than floating screens.
            for support_x in (side * 17.0, side * 31.4):
                for support_y in (gallery_y - 3.9, gallery_y + 3.9):
                    k.obj("cube", "rim", (support_x, support_y, 1.20),
                          (0.24, 0.24, 2.4))
            bpy.ops.object.light_add(type="AREA", location=(deck_x, gallery_y, 7.1))
            gallery_light = bpy.context.active_object
            gallery_light.data.shape = "RECTANGLE"
            gallery_light.data.size, gallery_light.data.size_y = 13.5, 7.0
            gallery_light.data.energy = 480
            gallery_light.data.color = (0.66, 0.83, 1.0)

    # Hub: sunken well, rim, glowing rings, dashed ring, arc segments.
    k.obj("cyl64", "well", (0, 0, 0.005), (11.9, 11.9, 0.01))
    k.obj("cyl64", "rim", (0, 0, 0.12), (12.3, 12.3, 0.24))
    k.obj("cyl64", "well", (0, 0, 0.13), (11.8, 11.8, 0.25))
    for r, w, mat in [(7.1, 0.09, "ringBright"), (8.4, 0.22, "cyan"), (9.1, 0.05, "ringBright"), (10.6, 0.14, "cyan"), (11.5, 0.04, "cyanSoft")]:
        bpy.ops.mesh.primitive_torus_add(major_radius=r, minor_radius=w, major_segments=192, minor_segments=8, location=(0, 0, 0.3 if r < 7.5 else 0.02))
        o = bpy.context.active_object
        o.scale.z = 0.25
        o.data.materials.append(k.mats[mat])
    for i in range(80):
        a = i / 80 * math.tau
        k.obj("cube", "cyanSoft", (math.cos(a) * 9.9, math.sin(a) * 9.9, 0.02), (0.55, 0.1, 0.02), (0, 0, a + math.pi / 2))
    for i in range(6):
        start = i / 6 * math.tau
        for j in range(14):
            a = start + j * 0.04
            k.obj("cube", "ringBright", (math.cos(a) * 10.75, math.sin(a) * 10.75, 0.02), (0.52, 0.16, 0.02), (0, 0, a + math.pi / 2))

    # Ring of consoles around the globe (entrance towards the camera) with tilted screens.
    n = 24
    for i in range(n):
        a = i / n * math.tau
        if abs(((a + math.pi / 2) % math.tau) - math.pi) > math.pi - 0.3:
            continue  # leave the entrance at -y open
        x, y = math.cos(a) * 5.7, math.sin(a) * 5.7
        rz = a + math.pi / 2
        k.obj("cube", "console", (x, y, 0.75), (1.45, 0.8, 0.9), (0, 0, rz))
        sx, sy = math.cos(a) * 5.25, math.sin(a) * 5.25
        k.obj("plane", f"screen{i % 4}", (sx, sy, 1.35), (1.3, 0.75, 1), (math.radians(60), 0, a - math.pi / 2))
    # Curved command desk beyond the hub, screens towards the camera.
    for i in range(13):
        a = math.pi / 2 + (i / 12 - 0.5) * 1.1
        x, y = math.cos(a) * 14.0, math.sin(a) * 14.0
        rz = a + math.pi / 2
        k.obj("cube", "console", (x, y, 0.45), (1.5, 0.9, 0.9), (0, 0, rz))
        k.obj("plane", f"screen{(i + 1) % 4}", (math.cos(a) * 13.55, math.sin(a) * 13.55, 1.15), (1.4, 0.8, 1), (math.radians(60), 0, a - math.pi / 2))

    # Detailed rear operator rows between the circular hub and video wall.
    # Consoles are unlabelled environment geometry. They neither imply a
    # connected agent nor report an executed order; those remain live DOM data.
    for rear_row, rear_y in enumerate((16.1, 18.35)):
        for col, console_x in enumerate((-10.5, -7.0, -3.5, 0.0, 3.5, 7.0, 10.5)):
            k.obj("cube", "desk", (console_x, rear_y, 0.75), (3.15, 1.12, 0.10))
            k.obj("cube", "deskbody", (console_x, rear_y, 0.42), (3.04, 1.03, 0.60))
            for monitor in (-1, 0, 1):
                mx = console_x + monitor * 0.86
                k.obj("cube", "black", (mx, rear_y + 0.32, 1.35),
                      (0.78, 0.10, 0.54), (math.radians(-12), 0, 0))
                k.obj("plane", f"screen{(col + rear_row + monitor + 4) % 4}",
                      (mx, rear_y + 0.26, 1.35), (0.73, 0.51, 1),
                      (math.radians(75), 0, 0))
            k.obj("cube", "black", (console_x, rear_y - 0.91, 0.57),
                  (0.70, 0.56, 0.16))
            k.obj("cube", "black", (console_x, rear_y - 1.15, 0.95),
                  (0.70, 0.11, 0.67))
    for marker_x in (-12.6, 12.6):
        k.obj("cube", "amber", (marker_x, 17.2, 0.05),
              (0.065, 6.2, 0.04))

    # Segmented cyan architectural floor arcs around the central hub.
    for radius, count, width in [(10.8, 96, 0.22), (14.8, 120, 0.24), (18.0, 144, 0.18)]:
        for segment in range(count):
            angle = (segment + 0.5) * math.tau / count
            if -2.05 < angle - math.tau * (angle > math.pi) < -1.1:
                continue  # keep the near-side entrance open
            k.obj("cube", "cyanSoft",
                  (math.cos(angle) * radius, math.sin(angle) * radius, 0.035),
                  (math.tau * radius / count * 0.64, width, 0.028),
                  (0, 0, angle + math.pi / 2))

    # Globe: enlarge the focal point while keeping projection anchors in sync.
    # Re-render both variants after changing geometry; never hand-edit anchors.json.
    # Globe: earth, wire shell, light column.
    k.obj("sphere", "earth", (0, 0, GLOBE_Z), (GLOBE_R, GLOBE_R, GLOBE_R), (-0.12, 0, -0.37))
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=GLOBE_R + 0.4, location=(0, 0, GLOBE_Z))
    wire = bpy.context.active_object
    mod = wire.modifiers.new("w", "WIREFRAME")
    mod.thickness = 0.025
    wire.data.materials.append(k.mats["wire"])
    k.obj("cone", "beam", (0, 0, 1.6), (8.2, 8.2, 3.2), (math.pi, 0, 0))

    # Desk blocks. Every seat is recorded for the robots.
    seats: list[dict] = []
    for bx, by, cols, pairs, floor in BLOCKS:
        block_seats = []
        for p in range(pairs):
            yc = by + (p - (pairs - 1) / 2) * 6.4
            for c in range(cols):
                x = bx + (c - (cols - 1) / 2) * 2.5
                for side in (-1, 1):
                    dy = yc + side * 0.6
                    k.obj("cube", "desk", (x, dy, 0.76), (2.35, 1.1, 0.06))
                    k.obj("cube", "deskbody", (x, dy, 0.38), (2.25, 1.0, 0.72))
                    for kx in (-0.55, 0.0, 0.55)[:: 1]:
                        my = yc + side * 0.15
                        # Monitor leans back, away from its seat; the screen faces the seat.
                        k.obj("cube", "black", (x + kx * 1.25, my, 1.22), (0.7, 0.04, 0.46), (math.radians(15) * side, 0, 0))
                        k.obj("plane", f"screen{(c + p + int(kx * 2) + (side > 0)) % 4}", (x + kx * 1.25, my + side * 0.03, 1.22), (0.66, 0.42, 1),
                              (math.radians(55) * -side, 0, 0))
                    cy = yc + side * 1.6
                    k.obj("cube", "black", (x, cy, 0.5), (0.6, 0.6, 0.1))
                    k.obj("cube", "black", (x, cy + side * 0.3, 0.85), (0.6, 0.08, 0.65))
                    k.obj("cyl", "black", (x, cy, 0.25), (0.12, 0.12, 0.5))
                    # Anonymous seated silhouettes approximate the dense occupancy
                    # of the approved preview; no identifiable faces or live values.
                    if (c + 2 * p + (1 if side > 0 else 0)) % 3 != 0:
                        k.obj("sphere", "operator", (x, cy - side * 0.18, 1.10), (0.37, 0.31, 0.51))
                        k.obj("sphere", "operatorFace", (x, cy - side * 0.22, 1.64), (0.21, 0.20, 0.23))
                        k.obj("sphere", "operator", (x, cy - side * 0.17, 1.76), (0.23, 0.22, 0.10))
                        for dx in (-0.44, 0.44):
                            k.obj("sphere", "operator", (x + dx, cy - side * 0.42, 1.07), (0.13, 0.35, 0.16))

                    block_seats.append({"x": x, "y": yc + side * 2.25, "floor": floor})
        # Spread a block's seats: outer columns first.
        block_seats.sort(key=lambda s: -abs(s["x"] - bx))
        seats.append(block_seats)  # type: ignore[arg-type]
        # Warm light over the block.
        bpy.ops.object.light_add(type="AREA", location=(bx, by, 9))
        lt = bpy.context.active_object
        lt.data.shape = "RECTANGLE"
        lt.data.size, lt.data.size_y = cols * 2.6 + 2, pairs * 6.4 + 2
        lt.data.energy = 220 * pairs
        lt.data.color = (0.82, 0.9, 1.0)

    # Glass partitions with amber strips.
    for x1, y1, x2, y2 in WALLS:
        ln, rz = math.hypot(x2 - x1, y2 - y1), math.atan2(y2 - y1, x2 - x1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        k.obj("cube", "glass", (cx, cy, 0.8), (ln, 0.1, 1.6), (0, 0, rz))
        k.obj("cube", "amber", (cx, cy, 1.64), (ln, 0.16, 0.07), (0, 0, rz))
        k.obj("cube", "amber", (cx, cy, 0.02), (ln, 0.08, 0.02), (0, 0, rz))

    # Plants and the lounge corner, plus a ring of planters around the hub (open towards the camera).
    hub_plants = [(math.cos(a) * 15.3, math.sin(a) * 15.3) for a in (i / 18 * math.tau for i in range(18))
                  if abs(math.sin(a) + 1) > 0.15]
    for px_, py_ in PLANTS + hub_plants:
        k.obj("cyl", "pot", (px_, py_, 0.4), (0.9, 0.9, 0.8))
        for j in range(11):
            a = j / 11 * math.tau + random.random() * 0.4
            tilt = 0.5 + random.random() * 0.5
            k.obj("sphere", "leaf" if j % 2 else "leaf2", (px_ + math.cos(a) * 0.35, py_ + math.sin(a) * 0.35, 1.25 + random.random() * 0.3),
                  (0.1, 0.22, 0.75), (tilt, 0, a + math.pi / 2))
    for sx, sy, rz in [(23, -12, math.pi), (23, -17.6, 0), (19, -14.8, -math.pi / 2)]:
        k.obj("cube", "sofa", (sx, sy, 0.3), (3.4, 1.1, 0.6), (0, 0, rz))
        bx_, by_ = -math.sin(rz) * -0.45, math.cos(rz) * -0.45  # backrest on the far side of the seat
        k.obj("cube", "sofa", (sx + bx_, sy + by_, 0.75), (3.4, 0.25, 0.6), (0, 0, rz))
    k.obj("cyl", "desk", (23, -14.8, 0.22), (1.6, 1.6, 0.44))

    # Hub light and world.
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 9))
    hub = bpy.context.active_object
    hub.data.energy = 6000
    hub.data.color = lin("#60a5fa")
    hub.data.shadow_soft_size = 3
    bpy.ops.object.light_add(type="SUN", rotation=(0.6, 0.2, 0.4))
    sun = bpy.context.active_object
    sun.data.energy = 0.6
    sun.data.color = lin("#93c5fd")
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (*lin("#0a1a3a"), 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    k.scene.world = world

    # Interleave blocks so a small roster fills the room evenly: left, right, left, right...
    ordered: list[dict] = []
    for r in range(max(len(s) for s in seats)):  # type: ignore[arg-type]
        for s in seats:  # type: ignore[assignment]
            if r < len(s):
                ordered.append(s[r])
    return ordered


def camera(k: Kit, loc, target, lens) -> bpy.types.Object:
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = lens
    cam = bpy.data.objects.new("cam", cam_data)
    k.scene.collection.objects.link(cam)
    cam.location = loc
    direction = Vector(target) - Vector(loc)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    k.scene.camera = cam
    return cam


def bloom(src: Path, dst: Path, quality: int, size: tuple[int, int] | None = None) -> None:
    img = Image.open(src).convert("RGB")
    a = np.asarray(img).astype(np.float32) / 255
    lum = a.max(axis=2, keepdims=True)
    bright = np.clip((lum - 0.62) / 0.38, 0, 1) * a
    base = Image.fromarray((bright * 255).astype(np.uint8))
    glow = np.zeros_like(a)
    for radius, weight in [(6, 0.55), (18, 0.45), (48, 0.35)]:
        glow += np.asarray(base.filter(ImageFilter.GaussianBlur(radius))).astype(np.float32) / 255 * weight
    out = 1 - (1 - a) * (1 - np.clip(glow, 0, 1))  # screen blend
    res = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    if size:
        res = res.resize(size, Image.LANCZOS)
    res.save(dst, "WEBP", quality=quality, method=6)


def render(k: Kit, path: Path, res, samples: int) -> None:
    s = k.scene
    s.render.engine = "CYCLES"
    s.cycles.device = "CPU"
    s.cycles.samples = samples
    s.cycles.use_denoising = True
    s.cycles.max_bounces = 6
    s.render.resolution_x, s.render.resolution_y = res
    s.render.resolution_percentage = 100
    s.render.filepath = str(path)
    s.render.image_settings.file_format = "PNG"
    s.view_settings.view_transform = "Standard"
    s.view_settings.exposure = 0.0
    bpy.ops.render.render(write_still=True)


def project(k: Kit, cam, x: float, y: float, z: float) -> tuple[float, float]:
    v = world_to_camera_view(k.scene, cam, Vector((x, y, z)))
    return round(v.x, 4), round(1 - v.y, 4)


def anchors_for(k: Kit, cam, seats: list[dict]) -> dict:
    out_seats = []
    for s in seats:
        ang = math.atan2(s["y"], s["x"])
        hx, hy = math.cos(ang) * 10.2, math.sin(ang) * 10.2
        foot = project(k, cam, s["x"], s["y"], 0)
        head = project(k, cam, s["x"], s["y"], 1.9)
        out_seats.append({"floor": s["floor"], "seat": foot, "hub": project(k, cam, hx, hy, 0), "height": round(foot[1] - head[1], 4)})
    gc = project(k, cam, 0, 0, GLOBE_Z)
    gt = project(k, cam, 0, 0, GLOBE_Z + GLOBE_R)
    # Posts on the floor ring around the globe, where agents stand when the panels cover their desks.
    ring = [project(k, cam, math.cos(a) * RING_R, math.sin(a) * RING_R, 0) for a in (i / 36 * math.tau for i in range(36))]
    return {"seats": out_seats, "ring": ring, "globe": {"center": gc, "radius": round(gc[1] - gt[1], 4)}}


def robot_sheet(samples: int, view_dir: Vector, dst: Path) -> None:
    """PROTO's own robot (yellow shell, cyan visor band, blue base), four walk frames."""
    k = Kit()
    k.principled("shell", "#facc15", rough=0.25, coat=0.6)
    k.principled("base", "#1d4ed8", rough=0.4)
    k.principled("dark", "#0f172a", rough=0.3)
    k.emissive("visor", "#38bdf8", 6)
    bpy.ops.object.light_add(type="AREA", location=(2, -3, 5))
    bpy.context.active_object.data.energy = 600
    bpy.context.active_object.data.size = 4
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (*lin("#3b5b9a"), 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
    k.scene.world = world
    parts = [
        k.obj("cyl", "base", (0, 0, 0.25), (0.78, 0.78, 0.5)),
        k.obj("cyl", "shell", (0, 0, 0.85), (0.86, 0.86, 0.8)),
        k.obj("sphere", "shell", (0, 0, 1.25), (0.43, 0.43, 0.43)),
        k.obj("cyl", "dark", (0, 0, 1.18), (0.9, 0.9, 0.2)),
        k.obj("cyl", "visor", (0, 0, 1.18), (0.91, 0.91, 0.12)),
        k.obj("cyl", "dark", (0, 0, 1.75), (0.04, 0.04, 0.3)),
        k.obj("sphere", "visor", (0, 0, 1.92), (0.08, 0.08, 0.08)),
    ]
    arms = [k.obj("sphere", "shell", (side * 0.5, 0, 0.8), (0.12, 0.12, 0.3)) for side in (-1, 1)]
    cam_data = bpy.data.cameras.new("c")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 2.6
    cam = bpy.data.objects.new("c", cam_data)
    k.scene.collection.objects.link(cam)
    cam.location = Vector((0, 0, 1.0)) - view_dir.normalized() * 12
    cam.rotation_euler = view_dir.to_track_quat("-Z", "Y").to_euler()
    k.scene.camera = cam
    k.scene.render.film_transparent = True
    frames = []
    for f in range(4):
        bob = [0, 0.06, 0, 0.06][f]
        swing = [0.35, 0, -0.35, 0][f]
        for p in parts:
            p.location.z += bob
        for side, a in zip((-1, 1), arms):
            a.location.z += bob
            a.rotation_euler = (swing * side, 0, 0)
        path = WORK / f"robot{f}.png"
        render(k, path, (256, 256), samples)
        frames.append(Image.open(path).convert("RGBA"))
        for p in parts:
            p.location.z -= bob
        for a in arms:
            a.location.z -= bob
    sheet = Image.new("RGBA", (256 * 4, 256))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (i * 256, 0))
    sheet.save(dst, "WEBP", quality=88, method=6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--samples", type=int, default=None, help="Cycles samples; defaults to 192 for final render")
    ap.add_argument("--only", choices=["landscape", "portrait", "robot"])
    ap.add_argument("--anchors-only", action="store_true", help="rebuild anchors.json without rendering")
    args = ap.parse_args()
    samples = args.samples if args.samples is not None else (24 if args.preview else 192)
    if samples < 16 or samples > 512:
        ap.error("--samples must be between 16 and 512")
    scale = 0.5 if args.preview else 1.0
    PUBLIC.mkdir(parents=True, exist_ok=True)
    anchors = json.loads(ANCHORS.read_text()) if ANCHORS.exists() else {}
    view = Vector((0, 0, 0))
    for variant, loc, target, lens, res, size in [
        ("landscape", (0, -46, 41), (0, 0, 0), 38, (1920, 1080), (1920, 1080)),
        ("portrait", (0, -26, 46), (0, 1.5, 0), 34, (1080, 1920), (900, 1600)),
    ]:
        if variant == "landscape":
            view = Vector(target) - Vector(loc)
        if args.only and args.only != variant:
            continue
        k = Kit()
        seats = build_room(k)
        cam = camera(k, loc, target, lens)
        raw = WORK / f"{variant}.png"
        if args.anchors_only:
            k.scene.render.resolution_x, k.scene.render.resolution_y = res
            bpy.context.view_layer.update()  # a render would refresh the camera matrices; do it by hand
            anchors[variant] = anchors_for(k, cam, seats)
            continue
        render(k, raw, (int(res[0] * scale), int(res[1] * scale)), samples)
        anchors[variant] = anchors_for(k, cam, seats)
        bloom(raw, PUBLIC / ("room.webp" if variant == "landscape" else "room-portrait.webp"), 80 if variant == "landscape" else 76,
              None if args.preview else size)
    if not args.anchors_only and (not args.only or args.only == "robot"):
        robot_sheet(24 if args.preview else min(samples, 96), view, PUBLIC / "robot.webp")
    ANCHORS.write_text(json.dumps(anchors, indent=1) + "\n")


if __name__ == "__main__":
    main()
