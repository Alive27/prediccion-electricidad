"""Build the energy city in Blender from the Kenney kits and export it.

Run headless from the repository root:

    blender --background --factory-startup --python blender/build_city.py
    blender ... --python blender/build_city.py -- --no-render     # skip renders

Outputs
    blender/energy_city.blend                      editable scene (collections per district)
    src/dashboard/static/city/energy_city.glb      optimized scene for Three.js
    src/dashboard/static/city/energy_city.json     energy routes, bounds and stats
    blender/renders/*.png                          inspection renders
    blender/validation_report.json                 automated scene checks

Coordinates: the masterplan works in native Kenney units (1 road tile = 1);
every object is placed at native * WORLD_SCALE so the city spans the 64 x 64
terrain of the dashboard.  Kenney models face -Y in Blender (+Z in glTF).
"""

import json
import math
import random
import sys
import zlib
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import city_masterplan as plan  # noqa: E402

REPO = HERE.parent
DASHBOARD = REPO / "src" / "dashboard"
STATIC_CITY = DASHBOARD / "static" / "city"
KITS = {
    "roads": DASHBOARD / "kenney_city-kit-roads",
    "commercial": DASHBOARD / "kenney_city-kit-commercial_2.1",
    "industrial": DASHBOARD / "kenney_city-kit-industrial_2.0",
    "suburban": DASHBOARD / "kenney_city-kit-suburban_20",
}
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
S = plan.WORLD_SCALE
LOT_Z = 0.02            # top of parcels and sidewalks (native units)
COLLECTIONS = ["TERRAIN", "ROADS", "COMMERCIAL_DISTRICT", "RESIDENTIAL_DISTRICT",
               "INDUSTRIAL_DISTRICT", "SUBSTATION", "VEGETATION", "STREET_LIGHTING",
               "URBAN_DETAILS"]

FOOTPRINTS = []         # solid footprints for overlap checks: (name, x0, y0, x1, y1)
PLACED = []             # every placed object with its metadata


# --------------------------------------------------------------------------- scene
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    for name in COLLECTIONS:
        coll = bpy.data.collections.new(name)
        scene.collection.children.link(coll)
    return scene


def coll(name):
    return bpy.data.collections[name]


def rng_for(name):
    return random.Random(zlib.crc32(name.encode()))


# --------------------------------------------------------------------------- materials
def make_material(name, color, roughness=0.85, metallic=0.0, emission=None,
                  strength=0.0, alpha=1.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        mat.surface_render_method = "BLENDED"
    mat.diffuse_color = (*color, alpha)
    return mat


def srgb(hex_color):
    """Hex sRGB -> linear RGB tuple for Blender material inputs."""
    hex_color = hex_color.lstrip("#")
    out = []
    for k in range(3):
        c = int(hex_color[2 * k:2 * k + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def build_materials():
    return {
        "terrain": make_material("terrain_ground", srgb("#1d3a2c"), 0.95),
        "terrain_side": make_material("terrain_side", srgb("#0d1f18"), 0.9),
        "grass": make_material("lot_grass", srgb("#3f7a4f"), 0.95),
        "park": make_material("park_lawn", srgb("#4a8a57"), 0.95),
        "paving": make_material("lot_paving", srgb("#7f8994"), 0.9),
        "plaza": make_material("plaza_stone", srgb("#c4c9c4"), 0.85),
        "concrete": make_material("industrial_concrete", srgb("#6d737a"), 0.92),
        "gravel": make_material("substation_gravel", srgb("#8b8a80"), 1.0),
        "asphalt": make_material("yard_asphalt", srgb("#4d5361"), 0.9),
        "marking": make_material("yard_marking", srgb("#e9d86a"), 0.7),
        "water": make_material("park_water", srgb("#2a7f95"), 0.15, 0.1),
        "court": make_material("sports_court", srgb("#3f8f8a"), 0.8),
        "white": make_material("line_white", srgb("#eef2ee"), 0.6),
        "steel": make_material("substation_steel", srgb("#aeb7bd"), 0.35, 0.8),
        "dark_steel": make_material("dark_steel", srgb("#4b545c"), 0.5, 0.6),
        "transformer": make_material("transformer_body", srgb("#7f8f8a"), 0.55, 0.3),
        "insulator": make_material("insulator_ceramic", srgb("#9a5b3c"), 0.35),
        "cable": make_material("power_cable", srgb("#20262b"), 0.6, 0.3),
        "fence": make_material("chainlink_fence", srgb("#9aa6a8"), 0.5, 0.6, alpha=0.55),
        "cabinet": make_material("mv_cabinet", srgb("#5f7f73"), 0.6, 0.2),
        "hazard": make_material("hazard_yellow", srgb("#f2c230"), 0.6),
        "glow": make_material("city_glow", srgb("#0d2a22"), 0.4, 0.0,
                              emission=srgb("#36f2c0"), strength=4.0),
    }


# --------------------------------------------------------------------------- Kenney assets
MESH_CACHE = {}


def load_mesh(kit, name, recenter=False):
    """Import a Kenney GLB once and return its mesh datablock.

    Transforms are baked, the base is put on z=0 and (for buildings with an
    off-centre pivot) the footprint is centred on the origin.  Materials are
    deduplicated into one shared material per kit so every instance shares the
    same texture.
    """
    key = (kit, name, recenter)
    if key in MESH_CACHE:
        return MESH_CACHE[key]
    path = KITS[kit] / "Models" / "GLB format" / f"{name}.glb"
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        o.data = o.data.copy()
        o.data.transform(o.matrix_world)
        o.matrix_world = Matrix.Identity(4)
    if len(meshes) > 1:
        with bpy.context.temp_override(active_object=meshes[0], object=meshes[0],
                                       selected_objects=meshes,
                                       selected_editable_objects=meshes):
            bpy.ops.object.join()
    mesh = meshes[0].data
    co = [v.co for v in mesh.vertices]
    mn = Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co)))
    mx = Vector((max(c.x for c in co), max(c.y for c in co), max(c.z for c in co)))
    shift = Vector((-(mn.x + mx.x) / 2, -(mn.y + mx.y) / 2, -mn.z)) if recenter \
        else Vector((0, 0, -mn.z))
    mesh.transform(Matrix.Translation(shift))
    for idx, mat in enumerate(mesh.materials):
        base = mat.name.split(".")[0]
        target = f"{kit}_{base}"
        shared = bpy.data.materials.get(target)
        if shared is None:
            mat.name = target
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    node.image.name = f"{kit}_colormap"
                    node.interpolation = "Closest"
            shared = mat
        mesh.materials[idx] = shared
    mesh.name = f"{kit}:{name}"
    for o in [o for o in bpy.data.objects if o not in before]:
        bpy.data.objects.remove(o, do_unlink=True)
    size = (mx - mn)
    MESH_CACHE[key] = (mesh, (size.x, size.y, size.z))
    return MESH_CACHE[key]


def place(kit, name, x, y, q=0, collection="URBAN_DETAILS", label=None, scale=1.0,
          z=LOT_Z, recenter=False, solid=False, rot=None, meta=None):
    """Instance a Kenney mesh at native (x, y). q = quarter turns CCW."""
    mesh, size = load_mesh(kit, name, recenter)
    label = label or f"{collection[:3]}_{name}"
    obj = bpy.data.objects.new(label, mesh)
    angle = rot if rot is not None else q * math.pi / 2
    obj.location = (x * S, y * S, z * S)
    obj.rotation_euler = (0, 0, angle)
    obj.scale = (S * scale,) * 3
    obj["district"] = collection
    obj["model"] = f"{kit}/{name}"
    coll(collection).objects.link(obj)
    sx, sy = size[0] * scale, size[1] * scale
    if q % 2:
        sx, sy = sy, sx
    rec = dict(name=label, model=f"{kit}/{name}", x=x, y=y, w=sx, d=sy,
               h=size[2] * scale, z=z, collection=collection, **(meta or {}))
    PLACED.append(rec)
    if solid:
        FOOTPRINTS.append((label, x - sx / 2, y - sy / 2, x + sx / 2, y + sy / 2))
    return obj


def model_size(kit, name, recenter=True):
    return load_mesh(kit, name, recenter)[1]


def overlaps(x0, y0, x1, y1, tol=0.015):
    for _, a0, b0, a1, b1 in FOOTPRINTS:
        if x0 < a1 - tol and x1 > a0 + tol and y0 < b1 - tol and y1 > b0 + tol:
            return True
    return False


# --------------------------------------------------------------------------- aux geometry
class MeshBuilder:
    """Accumulates boxes, cylinders and tubes into a single bmesh."""

    def __init__(self, materials):
        self.bm = bmesh.new()
        self.slots = []
        self.materials = materials

    def _slot(self, mat_key):
        if mat_key not in self.slots:
            self.slots.append(mat_key)
        return self.slots.index(mat_key)

    def _assign(self, verts, mat_key):
        idx = self._slot(mat_key)
        for f in {f for v in verts for f in v.link_faces}:
            f.material_index = idx

    def box(self, cx, cy, z0, sx, sy, sz, mat, angle=0.0):
        m = (Matrix.Translation((cx, cy, z0 + sz / 2)) @ Matrix.Rotation(angle, 4, "Z")
             @ Matrix.Diagonal((sx, sy, sz, 1)))
        verts = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)["verts"]
        self._assign(verts, mat)

    def cyl(self, cx, cy, z0, radius, height, mat, segments=10, radius_top=None):
        m = Matrix.Translation((cx, cy, z0 + height / 2))
        verts = bmesh.ops.create_cone(self.bm, cap_ends=True, segments=segments,
                                      radius1=radius, radius2=radius_top or radius,
                                      depth=height, matrix=m)["verts"]
        self._assign(verts, mat)

    def tube(self, p0, p1, radius, mat, segments=5):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        length = d.length
        if length < 1e-5:
            return
        rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
        m = Matrix.Translation((p0 + p1) / 2) @ rot
        verts = bmesh.ops.create_cone(self.bm, cap_ends=False, segments=segments,
                                      radius1=radius, radius2=radius, depth=length,
                                      matrix=m)["verts"]
        self._assign(verts, mat)

    def insulator(self, x, y, z0, discs=4, radius=0.035, pitch=0.03, hanging=False):
        """Stack of ceramic discs (post insulator, or hanging string)."""
        for k in range(discs):
            zz = z0 - (k + 1) * pitch if hanging else z0 + k * pitch
            self.cyl(x, y, zz, radius, pitch * 0.55, "insulator", segments=10)
        top = z0 if hanging else z0 + discs * pitch
        bottom = z0 - discs * pitch if hanging else z0
        self.cyl(x, y, bottom, radius * 0.35, top - bottom, "insulator", segments=6)

    def to_mesh(self, name):
        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        for key in self.slots:
            mesh.materials.append(self.materials[key])
        for poly in mesh.polygons:
            poly.use_smooth = False
        return mesh

    def to_object(self, name, collection, x=0.0, y=0.0, z=0.0, angle=0.0):
        return instance_mesh(self.to_mesh(name), name, collection, x, y, z, angle)


def instance_mesh(mesh, name, collection, x=0.0, y=0.0, z=0.0, angle=0.0):
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (x * S, y * S, z * S)
    obj.rotation_euler = (0, 0, angle)
    obj.scale = (S,) * 3
    obj["district"] = collection
    coll(collection).objects.link(obj)
    PLACED.append(dict(name=name, model="aux", x=x, y=y, z=z, collection=collection))
    return obj


# --------------------------------------------------------------------------- geometry helpers
def block_rect(block):
    _, _, i0, i1, j0, j1 = block
    return (i0 - plan.C - 0.5, j0 - plan.C - 0.5, i1 - plan.C + 0.5, j1 - plan.C + 0.5)


ROAD_CELLS = plan.road_cells()
RB_CELLS = plan.roundabout_cells()


def is_road(cell):
    return cell in ROAD_CELLS or cell in RB_CELLS


def frontage_segments(block):
    """Runs of the parcel edge that face a road: (side, a, b) along the edge."""
    _, _, i0, i1, j0, j1 = block
    sides = {
        "S": [((i, j0), (i, j0 - 1)) for i in range(i0, i1 + 1)],
        "N": [((i, j1), (i, j1 + 1)) for i in range(i0, i1 + 1)],
        "W": [((i0, j), (i0 - 1, j)) for j in range(j0, j1 + 1)],
        "E": [((i1, j), (i1 + 1, j)) for j in range(j0, j1 + 1)],
    }
    out = []
    for side, cells in sides.items():
        run = []
        for inside, outside in cells + [(None, None)]:
            if outside is not None and is_road(outside):
                run.append(inside)
                continue
            if run:
                k0 = run[0][0] if side in "SN" else run[0][1]
                k1 = run[-1][0] if side in "SN" else run[-1][1]
                out.append((side, k0 - plan.C - 0.5, k1 - plan.C + 0.5))
            run = []
    return out


SIDE_Q = {"S": 0, "E": 1, "N": 2, "W": 3}


def frontage_pose(rect, side, along, depth, setback):
    """Centre of a building whose front sits `setback` behind the parcel edge."""
    x0, y0, x1, y1 = rect
    if side == "S":
        return along, y0 + setback + depth / 2
    if side == "N":
        return along, y1 - setback - depth / 2
    if side == "W":
        return x0 + setback + depth / 2, along
    return x1 - setback - depth / 2, along


def pack_frontage(block, pools, collection, setback, gap, margin, max_depth=None,
                  extra=None, sides_order=None):
    """Line every road-facing edge of a parcel with buildings, front to the road.

    pools(side, along, rng) -> list of (kit, name) candidates in preference order.
    Returns list of (record, side) for placed buildings.
    """
    name = block[0]
    rng = rng_for(name)
    rect = block_rect(block)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    segments = frontage_segments(block)
    sides_present = {s for s, _, _ in segments}
    segments.sort(key=lambda s: (sides_order or "SNEW").index(s[0]))
    placed = []
    for side, a, b in segments:
        across = height if side in "SN" else width
        opposite = {"S": "N", "N": "S", "E": "W", "W": "E"}[side]
        limit = max_depth or (across / 2 - setback - 0.05 if opposite in sides_present
                              else across - setback - 0.35)
        pos = a + margin
        attempts = 0
        while pos < b - margin - 0.3 and attempts < 60:
            attempts += 1
            fitted = False
            for kit, model in pools(side, pos, rng):
                w, d, _ = model_size(kit, model)
                if d > limit or pos + w > b - margin:
                    continue
                along = pos + w / 2
                cx, cy = frontage_pose(rect, side, along, d, setback)
                fx, fy = (w, d) if side in "SN" else (d, w)
                if overlaps(cx - fx / 2, cy - fy / 2, cx + fx / 2, cy + fy / 2):
                    continue
                label = f"{name}_{model}_{len(placed):02d}"
                place(kit, model, cx, cy, SIDE_Q[side], collection, label,
                      recenter=True, solid=True, meta=dict(block=name, side=side))
                placed.append((PLACED[-1], side))
                if extra:
                    extra(PLACED[-1], side, rect, rng)
                pos += w + gap * (0.6 + rng.random() * 0.8)
                fitted = True
                break
            if not fitted:
                pos += 0.2
    return placed


def lot_slab(block, mat_key, materials, name=None, inset=0.0):
    x0, y0, x1, y1 = block_rect(block)
    builder = MeshBuilder(materials)
    builder.box((x0 + x1) / 2, (y0 + y1) / 2, 0.0, x1 - x0 - 2 * inset, y1 - y0 - 2 * inset,
                LOT_Z, mat_key)
    district = plan.DISTRICT_OF_ZONE[block[1]]
    return builder.to_object(name or f"{block[0]}_ground", district)


# --------------------------------------------------------------------------- terrain
def build_terrain(materials):
    half = plan.TERRAIN_HALF
    b = MeshBuilder(materials)
    b.box(0, 0, -0.35, 2 * half, 2 * half, 0.35, "terrain")
    # Darker skirt so the diorama edge reads cleanly against the dashboard.
    b.box(0, 0, -0.42, 2 * half + 0.06, 2 * half + 0.06, 0.07, "terrain_side")
    b.to_object("TERRAIN_base", "TERRAIN")


# --------------------------------------------------------------------------- roads
ROT = {"E": "N", "N": "W", "W": "S", "S": "E"}   # +90 deg CCW


def rotate_mask(mask, q):
    for _ in range(q):
        mask = {ROT[d] for d in mask}
    return mask


def tile_for(cell, mask):
    """Choose a Kenney road tile and quarter turns for a connectivity mask."""
    driveways = {d[:2]: d[2] for d in plan.DRIVEWAYS}
    if len(mask) == 4:
        return "road-crossroad", 0
    bases = {
        3: ("road-intersection", {"E", "W", "S"}),
        1: ("road-end-round", {"E"}),
    }
    if len(mask) == 2 and mask in ({"E", "W"}, {"N", "S"}):
        q = 0 if mask == {"E", "W"} else 1
        if cell in driveways:
            side = driveways[cell]
            q = {"S": 0, "E": 1, "N": 2, "W": 3}[side]
            return "road-driveway-single", q
        if cell in plan.CROSSINGS:
            return "road-crossing", q
        return "road-straight", q
    if len(mask) == 2:
        bases[2] = ("road-bend", {"W", "S"})
    name, base = bases[len(mask)]
    for q in range(4):
        if rotate_mask(base, q) == mask:
            return name, q
    raise ValueError(f"no tile for {cell} {mask}")


def build_roads():
    stats = {}
    for cell, kind in sorted(ROAD_CELLS.items()):
        mask = plan.road_mask(ROAD_CELLS, RB_CELLS, cell)
        tile, q = tile_for(cell, mask)
        x, y = plan.cell_center(*cell)
        place("roads", tile, x, y, q, "ROADS", f"road_{kind}_{cell[0]:02d}_{cell[1]:02d}",
              z=0.0, meta=dict(cell=cell, kind=kind, mask=sorted(mask), tile=tile))
        stats[tile] = stats.get(tile, 0) + 1
    for ci, cj in plan.ROUNDABOUTS:
        x, y = plan.cell_center(ci, cj)
        place("roads", "road-roundabout", x, y, 0, "ROADS", f"roundabout_{ci}_{cj}", z=0.0)
    return stats


# --------------------------------------------------------------------------- districts
TOWERS = [("commercial", n) for n in ("building-skyscraper-d", "building-skyscraper-b",
                                      "building-skyscraper-c", "building-skyscraper-e",
                                      "building-skyscraper-a")]
TALL_MID = [("commercial", n) for n in ("building-m", "building-l", "building-i",
                                        "building-g", "building-f")]
MIDRISE = [("commercial", f"building-{c}") for c in "abcdefghijk"]
HOUSES = [("suburban", f"building-type-{c}") for c in "abcdefghijklmnopqrstu"]


def build_core(block):
    """Tower blocks: skyscrapers on the edges facing the central crossroads."""
    rect = block_rect(block)
    centre_x = 0.0 if abs(rect[0]) < abs(rect[2]) else 0.0

    def pools(side, along, rng):
        x0, y0, x1, y1 = rect
        near_axis = ((side == "E" and x1 <= 0.5) or (side == "W" and x0 >= -0.5)
                     or (side == "N" and y1 <= 0.5) or (side == "S" and y0 >= -0.5))
        # Distance of this point of the edge from the central crossroads.
        if side in "SN":
            dist = abs(along + 0.6 - centre_x) if x1 <= 0.5 else abs(along)
        else:
            dist = abs(along)
        towers = TOWERS[:] if dist < 2.6 else TOWERS[2:]
        rng.shuffle(towers)
        mid = TALL_MID[:]
        rng.shuffle(mid)
        return (towers + mid) if near_axis else (mid[:2] + towers[2:] + mid[2:])

    corner_towers(block, rect)
    placed = pack_frontage(block, lambda side, along, rng: rng.sample(TALL_MID, len(TALL_MID))
                           + rng.sample(MIDRISE[:9], 9), "COMMERCIAL_DISTRICT", setback=0.06,
                           gap=0.05, margin=0.05, sides_order="EWNS")
    courtyard_details(block, rect)
    return placed


def corner_towers(block, rect):
    """Four towers on the parcel corners; the tallest faces the central crossroads."""
    x0, y0, x1, y1 = rect
    corners = sorted(((x, y) for x in (x0, x1) for y in (y0, y1)), key=lambda c: math.hypot(*c))
    ranking = ["building-skyscraper-d", "building-skyscraper-b", "building-skyscraper-c",
               "building-skyscraper-e"]
    rng = rng_for(block[0] + "towers")
    mids = ranking[1:3]
    rng.shuffle(mids)
    far = rng.choice(["building-skyscraper-a", "building-skyscraper-e"])
    for k, (cx, cy) in enumerate(corners):
        model = [ranking[0] if block[0] != "core_ne" else "building-skyscraper-b",
                 mids[0], mids[1], far][k]
        w, d, _ = model_size("commercial", model)
        # Face the street on the corner's horizontal edge (N or S).
        side = "S" if cy == y0 else "N"
        q = SIDE_Q[side]
        px = cx + (0.06 + w / 2) * (1 if cx == x0 else -1)
        py = cy + (0.06 + d / 2) * (1 if cy == y0 else -1)
        place("commercial", model, px, py, q, "COMMERCIAL_DISTRICT", f"{block[0]}_tower_{k}",
              recenter=True, solid=True, meta=dict(block=block[0], side=side))


def courtyard_details(block, rect):
    """Small planted courtyard inside a perimeter block when space is free."""
    rng = rng_for(block[0] + "court")
    x0, y0, x1, y1 = rect
    for _ in range(40):
        x, y = rng.uniform(x0 + 0.4, x1 - 0.4), rng.uniform(y0 + 0.4, y1 - 0.4)
        if overlaps(x - 0.24, y - 0.19, x + 0.24, y + 0.19, tol=-0.04):
            continue
        if rng.random() < 0.5:
            place("suburban", "planter", x, y, rng.randrange(4), "VEGETATION",
                  f"{block[0]}_planter", solid=True)
            place("suburban", "tree-small", x + 0.08, y, 0, "VEGETATION",
                  f"{block[0]}_courtyard_tree", scale=1.1, z=LOT_Z + 0.14)
        else:
            place("suburban", "tree-large", x, y, 0, "VEGETATION", f"{block[0]}_courtyard_tree",
                  scale=1.3 + rng.random() * 0.3, rot=rng.random() * 6.28)
            FOOTPRINTS.append((f"{block[0]}_courtyard_tree", x - 0.2, y - 0.2, x + 0.2, y + 0.2))


def build_midrise(block):
    rect = block_rect(block)
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2

    def pools(side, along, rng):
        near = math.hypot(cx, cy) < 7.5
        pool = MIDRISE[:]
        rng.shuffle(pool)
        if near and rng.random() < 0.35:
            pool = TALL_MID[:2] + pool
        return pool

    placed = pack_frontage(block, pools, "COMMERCIAL_DISTRICT", setback=0.06, gap=0.08,
                           margin=0.08)
    courtyard_details(block, rect)
    return placed


def house_extras(rec, side, rect, rng):
    """Front path, garden tree and back fence for each house."""
    w, d = (rec["w"], rec["d"]) if side in "SN" else (rec["d"], rec["w"])
    x, y = rec["x"], rec["y"]
    out = {"S": (0, -1), "N": (0, 1), "W": (-1, 0), "E": (1, 0)}[side]
    q = SIDE_Q[side]
    # Path from the door to the sidewalk (setback is 0.28).
    px, py = x + out[0] * (d / 2 + 0.14), y + out[1] * (d / 2 + 0.14)
    place("suburban", "path-short", px, py, q, "RESIDENTIAL_DISTRICT", f"{rec['name']}_path",
          scale=1.0, z=LOT_Z)
    # Garden tree behind the house, beside it if the parcel is shallow.
    bx, by = x - out[0] * (d / 2 + 0.32), y - out[1] * (d / 2 + 0.32)
    lateral = (out[1], out[0])
    off = (w / 2 - 0.2) * (1 if rng.random() < 0.5 else -1)
    tx, ty = bx + lateral[0] * off, by + lateral[1] * off
    if (rect[0] + 0.12 < tx < rect[2] - 0.12 and rect[1] + 0.12 < ty < rect[3] - 0.12
            and not overlaps(tx - 0.1, ty - 0.1, tx + 0.1, ty + 0.1)):
        tree = "tree-large" if rng.random() < 0.6 else "tree-small"
        place("suburban", tree, tx, ty, 0, "VEGETATION", f"{rec['name']}_garden_tree",
              scale=1.25 + rng.random() * 0.35, rot=rng.random() * 6.28)
        FOOTPRINTS.append((f"{rec['name']}_garden_tree", tx - 0.1, ty - 0.1, tx + 0.1, ty + 0.1))


def build_houses(block, materials):
    lot_slab(block, "grass", materials)
    rng = rng_for(block[0] + "pool")
    houses = HOUSES[:]
    rng.shuffle(houses)

    def pools(side, along, rng_):
        start = rng_.randrange(len(houses))
        return houses[start:] + houses[:start]

    placed = pack_frontage(block, pools, "RESIDENTIAL_DISTRICT", setback=0.28, gap=0.34,
                           margin=0.12, extra=house_extras)
    back_fences(block, placed)
    return placed


def back_fences(block, placed):
    """Fence along the shared back line of back-to-back house rows."""
    x0, y0, x1, y1 = block_rect(block)
    sides = {side for _, side in placed}
    lines = []
    if {"S", "N"} <= sides and y1 - y0 >= 3:
        lines.append(("h", (y0 + y1) / 2, x0 + 0.15, x1 - 0.15))
    if {"E", "W"} <= sides and x1 - x0 >= 3:
        lines.append(("v", (x0 + x1) / 2, y0 + 0.15, y1 - 0.15))
    for axis, c, a, b in lines[:1]:
        k = a + 0.24
        while k < b - 0.24:
            x, y = (k, c) if axis == "h" else (c, k)
            if not overlaps(x - 0.24, y - 0.04, x + 0.24, y + 0.04) if axis == "h" \
                    else not overlaps(x - 0.04, y - 0.24, x + 0.04, y + 0.24):
                place("suburban", "fence", x, y, 0 if axis == "h" else 1,
                      "RESIDENTIAL_DISTRICT", f"{block[0]}_fence")
            k += 0.475


def build_plaza(block, materials):
    """Civic plaza with an energy beacon, terraces, trees and lighting."""
    lot_slab(block, "plaza", materials)
    x0, y0, x1, y1 = block_rect(block)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    b = MeshBuilder(materials)
    # Stepped base, slim column and glowing rings: the city's "energy beacon".
    b.cyl(0, 0, 0, 0.62, 0.03, "paving", segments=24)
    b.cyl(0, 0, 0.03, 0.46, 0.04, "white", segments=24)
    b.cyl(0, 0, 0.07, 0.36, 0.02, "water", segments=24)
    b.cyl(0, 0, 0.07, 0.07, 1.25, "steel", segments=12, radius_top=0.035)
    for k, z in enumerate((0.45, 0.78, 1.08)):
        b.cyl(0, 0, z, 0.15 - k * 0.03, 0.035, "glow", segments=16)
    b.cyl(0, 0, 1.32, 0.045, 0.06, "glow", segments=10)
    b.to_object("plaza_energy_beacon", "URBAN_DETAILS", cx, cy, LOT_Z)
    FOOTPRINTS.append(("plaza_energy_beacon", cx - 0.62, cy - 0.62, cx + 0.62, cy + 0.62))
    # Planted corners.
    for sx in (-1, 1):
        for sy in (-1, 1):
            px, py = cx + sx * 1.35, cy + sy * 1.35
            place("suburban", "planter", px, py, 0, "VEGETATION", "plaza_planter", solid=True)
            place("suburban", "tree-large", px, py, 0, "VEGETATION", "plaza_tree",
                  scale=1.45, z=LOT_Z + 0.14)
            place("roads", "light-square-cross", cx + sx * 0.9, cy + sy * 0.9, 0,
                  "STREET_LIGHTING", "plaza_lamp")
            lamp_glow_cross(cx + sx * 0.9, cy + sy * 0.9, materials)
    # Cafe terraces with parasols on the two pedestrian edges.
    for k, px in enumerate((cx - 0.75, cx + 0.05, cx + 0.8)):
        place("commercial", "detail-parasol-a" if k % 2 else "detail-parasol-b",
              px, y1 - 0.35, 0, "URBAN_DETAILS", "plaza_parasol", solid=True)
    for k, py in enumerate((cy - 0.6, cy + 0.4)):
        place("commercial", "detail-parasol-b" if k else "detail-parasol-a",
              x0 + 0.35, py, 0, "URBAN_DETAILS", "plaza_parasol", solid=True)


def build_park(block, materials):
    """Southern park: pond, tree groves, paths and a sports court."""
    lot_slab(block, "park", materials)
    x0, y0, x1, y1 = block_rect(block)
    rng = rng_for(block[0])
    b = MeshBuilder(materials)
    pond = (x0 + 2.0, (y0 + y1) / 2, 1.05)
    b.cyl(pond[0], pond[1], 0.0, pond[2] + 0.08, LOT_Z + 0.004, "paving", segments=32)
    b.cyl(pond[0], pond[1], 0.0, pond[2], LOT_Z + 0.006, "water", segments=32)
    # Sports court on the east side.
    court = (x1 - 1.55, (y0 + y1) / 2)
    b.box(court[0], court[1], LOT_Z - 0.002, 2.3, 1.5, 0.006, "court")
    for dx in (-1.05, 1.05):
        b.box(court[0] + dx, court[1], LOT_Z + 0.004, 0.02, 1.3, 0.002, "white")
    for dy in (-0.65, 0.65):
        b.box(court[0], court[1] + dy, LOT_Z + 0.004, 2.12, 0.02, 0.002, "white")
    b.box(court[0], court[1], LOT_Z + 0.004, 0.02, 1.3, 0.002, "white")
    b.to_object("park_pond_and_court", "VEGETATION")
    FOOTPRINTS.append(("park_pond", pond[0] - 1.1, pond[1] - 1.1, pond[0] + 1.1, pond[1] + 1.1))
    FOOTPRINTS.append(("park_court", court[0] - 1.2, court[1] - 0.8, court[0] + 1.2, court[1] + 0.8))
    # Main path: north entrance to south entrance between pond and court.
    px = x0 + 3.65
    k = y0 + 0.1
    while k < y1 - 0.1:
        place("suburban", "path-stones-long", px, k + 0.2, 1, "VEGETATION", "park_path")
        k += 0.4
    FOOTPRINTS.append(("park_path", px - 0.12, y0, px + 0.12, y1))
    for py in (y0 + 0.9, (y0 + y1) / 2, y1 - 0.9):
        place("roads", "light-curved", px + 0.2, py, 1, "STREET_LIGHTING", "park_lamp")
        lamp_glow(px + 0.2, py, 1, materials, arm=0.2, height=0.66)
    # Groves around the pond and along the edges.
    trees = 0
    for _ in range(400):
        if trees >= 46:
            break
        x, y = rng.uniform(x0 + 0.2, x1 - 0.2), rng.uniform(y0 + 0.2, y1 - 0.2)
        if overlaps(x - 0.16, y - 0.16, x + 0.16, y + 0.16):
            continue
        big = rng.random() < 0.55
        place("suburban", "tree-large" if big else "tree-small", x, y, 0, "VEGETATION",
              "park_tree", scale=1.3 + rng.random() * 0.5, rot=rng.random() * 6.28)
        FOOTPRINTS.append(("park_tree", x - 0.16, y - 0.16, x + 0.16, y + 0.16))
        trees += 1
    for k in range(4):
        place("suburban", "planter", pond[0] + math.cos(k * 1.57 + 0.78) * 1.3,
              pond[1] + math.sin(k * 1.57 + 0.78) * 1.3, k, "VEGETATION", "park_planter")


def build_buffer(block):
    """Tree alignment separating the industrial area from housing."""
    x0, y0, x1, y1 = block_rect(block)
    rng = rng_for(block[0])
    k = x0 + 0.3
    while k < x1 - 0.2:
        y = (y0 + y1) / 2 + rng.uniform(-0.12, 0.12)
        place("suburban", "tree-large", k, y, 0, "VEGETATION", f"{block[0]}_tree",
              scale=1.5 + rng.random() * 0.4, rot=rng.random() * 6.28)
        FOOTPRINTS.append((f"{block[0]}_tree", k - 0.15, y - 0.15, k + 0.15, y + 0.15))
        k += 0.55


def industrial_building(block, model, x, y, q, label):
    _, _, _ = model_size("industrial", model)
    return place("industrial", model, x, y, q, "INDUSTRIAL_DISTRICT",
                 f"{block[0]}_{label}", recenter=True, solid=True)


def container_rows(block, x, y, rows, cols, rng, q=0):
    """Stacked shipping containers in a storage yard."""
    variants = ["shipping-container-a", "shipping-container-b", "shipping-container-c"]
    w, d, h = model_size("industrial", variants[0])
    if q % 2:
        w, d = d, w
    for r in range(rows):
        for c in range(cols):
            cx, cy = x + c * (w + 0.04), y + r * (d + 0.12)
            levels = 1 + (rng.random() < 0.55) + (rng.random() < 0.2)
            for lv in range(levels):
                place("industrial", rng.choice(variants), cx, cy, q, "INDUSTRIAL_DISTRICT",
                      f"{block[0]}_container", recenter=True, z=LOT_Z + lv * h)
            FOOTPRINTS.append((f"{block[0]}_containers", cx - w / 2, cy - d / 2,
                               cx + w / 2, cy + d / 2))


def build_industrial_yard(block, materials):
    """South yard: factories on the collector, storage and tanks behind."""
    lot_slab(block, "concrete", materials)
    x0, y0, x1, y1 = block_rect(block)
    rng = rng_for(block[0])
    # Factories facing the south collector (north edge), loading bays between.
    industrial_building(block, "building-r", x0 + 1.4, y1 - 0.72, 2, "factory_r")
    industrial_building(block, "building-l", x0 + 4.0, y1 - 1.06, 2, "factory_l")
    industrial_building(block, "building-a", x1 - 1.25, y1 - 0.7, 2, "factory_a")
    # Chimneys behind the main factory hall.
    for k, (cx, name) in enumerate(((x0 + 0.65, "chimney-large"), (x0 + 1.65, "chimney-medium"),
                                    (x0 + 2.25, "chimney-medium"))):
        place("industrial", name, cx, y1 - 2.15 + (0.25 if k else 0), 0, "INDUSTRIAL_DISTRICT",
              f"{block[0]}_{name}", recenter=True, solid=True)
    # Tanks and a water tower.
    place("industrial", "detail-tank-large", x0 + 4.0, y0 + 1.15, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_tank_large", recenter=True, solid=True)
    place("industrial", "detail-tank", x0 + 5.3, y0 + 0.55, 1, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_tank", recenter=True, solid=True)
    place("industrial", "water-tower", x0 + 2.75, y0 + 0.9, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_water_tower", recenter=True, solid=True)
    # Container storage in the south-east of the yard.
    container_rows(block, x1 - 1.85, y0 + 0.6, 2, 5, rng, q=0)
    # Loading apron with yellow markings in front of factory_a (driveway at 24,8).
    b = MeshBuilder(materials)
    apron = (x1 - 1.25, y1 - 1.75)
    b.box(apron[0], apron[1], LOT_Z, 1.9, 0.55, 0.003, "asphalt")
    for k in range(5):
        b.box(apron[0] - 0.8 + k * 0.4, apron[1], LOT_Z + 0.003, 0.02, 0.45, 0.002, "marking")
    b.to_object(f"{block[0]}_loading_apron", "INDUSTRIAL_DISTRICT")
    place("roads", "dumpster", x0 + 0.35, y0 + 0.35, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_dumpster")
    for k in range(3):
        place("roads", "construction-barrier", x1 - 2.3 + k * 0.25, y1 - 1.45, 1,
              "INDUSTRIAL_DISTRICT", f"{block[0]}_barrier")


def build_industrial_east(block, materials):
    """Eastern plant: factories on the industrial road, solar field behind."""
    lot_slab(block, "concrete", materials)
    x0, y0, x1, y1 = block_rect(block)
    rng = rng_for(block[0])
    # Factories along the west edge (front to the industrial road i=26).
    # Dimensions checked against blender/model_audit.json (footprints in comments).
    industrial_building(block, "building-c", x0 + 1.1, y0 + 1.2, 3, "factory_c")   # 2.11 x 1.88
    industrial_building(block, "building-t", x0 + 0.78, y0 + 3.35, 3, "factory_t")  # 1.39 x 1.72
    industrial_building(block, "building-e", x0 + 0.75, y0 + 5.15, 3, "factory_e")  # 1.29 x 1.68
    industrial_building(block, "building-g", x0 + 3.1, y0 + 0.75, 0, "factory_g")   # 1.68 x 1.28
    place("industrial", "chimney-large", x0 + 2.8, y0 + 2.5, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_chimney", recenter=True, solid=True)
    place("industrial", "chimney-small", x0 + 1.9, y0 + 3.3, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_chimney_small", recenter=True, solid=True)
    # Solar field in the north-east of the plot (rows of grouped panels).
    for r in range(3):
        for c in range(2):
            px, py = x0 + 2.45 + c * 1.6, y0 + 4.35 + r * 0.95
            place("industrial", "solar-panel-landscape-group", px, py, 0,
                  "INDUSTRIAL_DISTRICT", f"{block[0]}_solar", recenter=True, solid=True)
    container_rows(block, x0 + 3.6, y0 + 2.5, 1, 3, rng, q=0)
    place("industrial", "detail-tank", x1 - 0.5, y0 + 1.5, 0, "INDUSTRIAL_DISTRICT",
          f"{block[0]}_tank", recenter=True, solid=True)


# --------------------------------------------------------------------------- substation
def build_substation(block, materials):
    """Fenced 132/20 kV substation: line entry, busbars, bays, transformers."""
    x0, y0, x1, y1 = block_rect(block)
    lot_slab(block, "gravel", materials)
    inset = 0.15
    fx0, fy0, fx1, fy1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    gate_y, gate_w = plan.cell_center(26, 5)[1], 0.7
    col = "SUBSTATION"

    # Perimeter chain-link fence with a gate towards the industrial road.
    f = MeshBuilder(materials)
    edges = [((fx0, fy0), (fx1, fy0)), ((fx1, fy0), (fx1, fy1)), ((fx1, fy1), (fx0, fy1)),
             ((fx0, fy1), (fx0, gate_y + gate_w / 2)), ((fx0, gate_y - gate_w / 2), (fx0, fy0))]
    for (ax, ay), (bx, by) in edges:
        length = math.hypot(bx - ax, by - ay)
        n = max(1, round(length / 0.45))
        horiz = abs(by - ay) < 1e-6
        for k in range(n + 1):
            px, py = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            f.box(px, py, LOT_Z, 0.03, 0.03, 0.34, "dark_steel")
        mx, my = (ax + bx) / 2, (ay + by) / 2
        sx, sy = (length, 0.008) if horiz else (0.008, length)
        f.box(mx, my, LOT_Z + 0.02, sx, sy, 0.3, "fence")
        rx, ry = (length, 0.018) if horiz else (0.018, length)
        f.box(mx, my, LOT_Z + 0.33, rx, ry, 0.018, "dark_steel")
    # Gate leaves (slightly open) and hazard posts.
    for s in (-1, 1):
        f.box(fx0 - 0.02, gate_y + s * gate_w / 4, LOT_Z, 0.02, gate_w / 2 - 0.02, 0.3,
              "dark_steel")
        f.box(fx0, gate_y + s * (gate_w / 2 + 0.03), LOT_Z, 0.05, 0.05, 0.38, "hazard")
    f.to_object("substation_fence_and_gate", col)
    FOOTPRINTS.append(("substation_fence_S", fx0, fy0 - 0.02, fx1, fy0 + 0.02))
    place("roads", "road-sign-warning", fx0 - 0.08, gate_y + gate_w / 2 + 0.16, 3, col,
          "substation_warning_sign")

    # Internal access road from the gate.
    r = MeshBuilder(materials)
    r.box((fx0 + fx1) / 2 - 0.1, gate_y, LOT_Z, fx1 - fx0 - 0.2, 0.42, 0.004, "asphalt")
    r.box((fx0 + fx1) / 2 - 0.1, gate_y, LOT_Z + 0.004, fx1 - fx0 - 0.4, 0.015, 0.002,
          "marking")
    r.to_object("substation_access_road", col)

    # Control building (Kenney industrial) by the gate.
    place("industrial", "building-k", fx0 + 0.85, fy1 - 0.62, 0, col, "substation_control_building",
          recenter=True, solid=True)

    # --- 132 kV bay south of the road: gantries, busbars, breakers, disconnectors.
    phases = [fy0 + 0.55, fy0 + 0.9, fy0 + 1.25]
    bus_z = 0.78
    gantry = MeshBuilder(materials)
    vc = (phases[0] + phases[-1]) / 2     # mesh origin at the centre of the portal
    for v in (phases[0] - 0.35, phases[-1] + 0.35):
        gantry.box(0, v - vc, 0, 0.06, 0.06, 1.0, "steel")
        for k in range(4):
            gantry.box(0, v - vc, 0.1 + k * 0.24, 0.07, 0.07, 0.015, "steel")
    gantry.box(0, 0, 1.0, 0.07, 1.48, 0.07, "steel")
    for v in phases:
        gantry.insulator(0, v - vc, 1.0, discs=4, radius=0.03, pitch=0.035, hanging=True)
    gantry_mesh = gantry.to_mesh("substation_gantry")
    for k, u in enumerate((fx0 + 0.55, fx1 - 0.2)):
        instance_mesh(gantry_mesh, f"substation_gantry_{k}", col, u, vc, LOT_Z)
        FOOTPRINTS.append((f"substation_gantry_{k}", u - 0.05, phases[0] - 0.4, u + 0.05,
                           phases[-1] + 0.4))

    post = MeshBuilder(materials)
    post.box(0, 0, 0, 0.05, 0.05, 0.5, "steel")
    post.insulator(0, 0, 0.5, discs=6, radius=0.03, pitch=0.045)
    post_mesh = post.to_mesh("substation_post_insulator")
    bus = MeshBuilder(materials)
    u_from, u_to = fx0 + 0.55, fx1 - 0.2
    for v in phases:
        bus.tube((u_from, v, bus_z + 0.02), (u_to, v, bus_z + 0.02), 0.018, "steel", 6)
        for u in [u_from + 0.35] + [b + 0.65 for b in (fx0 + 1.6, fx0 + 2.65, fx0 + 3.7)]:
            instance_mesh(post_mesh, "substation_bus_support", col, u, v, LOT_Z)
        # Line drops from the gantries to the busbars.
        for ug in (u_from, u_to):
            bus.tube((ug, v, 1.0 - 4 * 0.035), (ug + (0.25 if ug == u_from else -0.25), v,
                                                 bus_z + 0.02), 0.008, "cable", 4)
    bus.to_object("substation_132kV_busbars", col, 0, 0, LOT_Z)

    breaker = MeshBuilder(materials)
    breaker.box(0, 0, 0, 0.16, 0.16, 0.06, "concrete")
    breaker.box(0, 0, 0.06, 0.05, 0.05, 0.2, "steel")
    breaker.box(0, 0, 0.26, 0.11, 0.08, 0.07, "dark_steel")
    breaker.insulator(0, 0, 0.33, discs=5, radius=0.028, pitch=0.04)
    breaker_mesh = breaker.to_mesh("substation_circuit_breaker")
    discon = MeshBuilder(materials)
    discon.box(0, 0, 0, 0.05, 0.22, 0.4, "steel")
    for dv in (-0.08, 0.08):
        discon.insulator(0, dv, 0.4, discs=4, radius=0.022, pitch=0.035)
    discon.box(0, 0, 0.55, 0.02, 0.2, 0.015, "steel")
    discon_mesh = discon.to_mesh("substation_disconnector")
    bays = [fx0 + 1.6, fx0 + 2.65, fx0 + 3.7]
    for k, u in enumerate(bays):
        for v in phases:
            instance_mesh(breaker_mesh, f"substation_breaker_{k}", col, u, v, LOT_Z)
            instance_mesh(discon_mesh, f"substation_disconnector_{k}", col, u + 0.32, v,
                          LOT_Z)
        FOOTPRINTS.append((f"substation_bay_{k}", u - 0.1, phases[0] - 0.15, u + 0.4,
                           phases[-1] + 0.15))

    # --- Power transformers north of the road, fed from each bay.
    tr = MeshBuilder(materials)
    tr.box(0, 0, 0, 0.62, 0.5, 0.04, "concrete")
    tr.box(0, 0, 0.04, 0.44, 0.34, 0.34, "transformer")
    for side in (-1, 1):
        for k in range(5):
            tr.box(-0.16 + k * 0.08, side * 0.205, 0.07, 0.045, 0.06, 0.26, "transformer")
    tr.tube((-0.2, 0.1, 0.46), (0.2, 0.1, 0.46), 0.05, "transformer", 10)
    tr.box(-0.12, 0.1, 0.38, 0.03, 0.03, 0.06, "dark_steel")
    tr.box(0.12, 0.1, 0.38, 0.03, 0.03, 0.06, "dark_steel")
    for k in range(3):
        tr.insulator(-0.13 + k * 0.13, -0.08, 0.38, discs=5, radius=0.025, pitch=0.035)
        tr.insulator(-0.1 + k * 0.1, 0.13, 0.38, discs=2, radius=0.018, pitch=0.03)
    tr.box(0.24, -0.05, 0.1, 0.03, 0.12, 0.16, "dark_steel")
    tr_mesh = tr.to_mesh("substation_power_transformer")
    feeders = MeshBuilder(materials)
    tr_v = gate_y + 0.75
    for k, u in enumerate(bays):
        instance_mesh(tr_mesh, f"substation_transformer_T{k + 1}", col, u + 0.15, tr_v, LOT_Z)
        FOOTPRINTS.append((f"substation_transformer_T{k + 1}", u + 0.15 - 0.31, tr_v - 0.25,
                           u + 0.15 + 0.31, tr_v + 0.25))
        # Overhead connection breaker -> transformer HV bushings (3 phases).
        for p, v in enumerate(phases):
            feeders.tube((u, v, 0.33 + 5 * 0.04), (u + 0.02 + (p - 1) * 0.13,
                                                   tr_v - 0.08, 0.56), 0.007, "cable", 4)
        # Medium-voltage cabinets fed from the LV bushings.
        cab_v = fy1 - 0.22
        feeders.box(u + 0.15, cab_v, LOT_Z, 0.36, 0.16, 0.24, "cabinet")
        feeders.box(u + 0.15, cab_v - 0.081, LOT_Z + 0.14, 0.2, 0.004, 0.05, "glow")
        for p in range(3):
            feeders.tube((u + 0.05 + p * 0.1, tr_v + 0.13, 0.44 + LOT_Z),
                         (u + 0.05 + p * 0.1, cab_v - 0.04, LOT_Z + 0.25), 0.006, "cable", 4)
        FOOTPRINTS.append((f"substation_cabinet_{k}", u - 0.03, cab_v - 0.08, u + 0.33,
                           cab_v + 0.08))
    feeders.to_object("substation_feeders_and_mv_cabinets", col, 0, 0, LOT_Z)
    # Floodlights inside the yard.
    for u, v in ((fx0 + 0.25, fy0 + 0.25), (fx1 - 0.25, fy1 - 0.25), (fx1 - 0.25, fy0 + 0.25)):
        place("roads", "light-square", u, v, 1 if u < 0 else 3, col, "substation_floodlight")
    return dict(gantry_east=(fx1 - 0.2, phases), bounds=(fx0, fy0, fx1, fy1))


def lattice_tower_mesh(materials, height=2.3):
    b = MeshBuilder(materials)
    base, top = 0.34, 0.1
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    for cx, cy in corners:
        b.tube((cx * base / 2, cy * base / 2, 0), (cx * top / 2, cy * top / 2, height), 0.014,
               "steel", 4)
    levels = 7
    for k in range(levels):
        z0, z1 = height * k / levels, height * (k + 1) / levels
        w0 = base + (top - base) * k / levels
        w1 = base + (top - base) * (k + 1) / levels
        for a in range(4):
            c0, c1 = corners[a], corners[(a + 1) % 4]
            b.tube((c0[0] * w0 / 2, c0[1] * w0 / 2, z0), (c1[0] * w1 / 2, c1[1] * w1 / 2, z1),
                   0.006, "steel", 3)
            b.tube((c0[0] * w1 / 2, c0[1] * w1 / 2, z1), (c1[0] * w1 / 2, c1[1] * w1 / 2, z1),
                   0.006, "steel", 3)
    arms = []
    for z, half in ((height * 0.78, 0.42), (height * 0.93, 0.3)):
        b.box(0, 0, z, 2 * half, 0.05, 0.04, "steel")
        for s in (-1, 1):
            b.insulator(s * (half - 0.03), 0, z, discs=4, radius=0.022, pitch=0.035,
                        hanging=True)
            arms.append((s * (half - 0.03), 0, z - 4 * 0.035))
    b.cyl(0, 0, height, 0.012, 0.18, "steel", 4)
    return b.to_mesh("hv_lattice_tower"), arms


def catenary(builder, p0, p1, sag, radius=0.007, steps=10):
    p0, p1 = Vector(p0), Vector(p1)
    prev = p0
    for k in range(1, steps + 1):
        t = k / steps
        p = p0.lerp(p1, t)
        p.z -= sag * 4 * t * (1 - t)
        builder.tube(prev, p, radius, "cable", 4)
        prev = p


def build_power_lines(materials, sub):
    """132 kV line entering from the east edge and MV distribution poles."""
    mesh, arms = lattice_tower_mesh(materials)
    gantry_u, phases = sub["gantry_east"]
    towers = [(16.85, phases[1] - 0.1), (16.85, phases[1] + 5.6)]
    w = MeshBuilder(materials)
    for k, (tx, ty) in enumerate(towers):
        instance_mesh(mesh, f"hv_tower_{k}", "SUBSTATION", tx, ty, 0.0)
        FOOTPRINTS.append((f"hv_tower_{k}", tx - 0.17, ty - 0.17, tx + 0.17, ty + 0.17))
    # Cross-arms span along X, across the north-south line.
    lower = [a for a in arms if a[2] < 2.0]
    upper = [a for a in arms if a[2] >= 2.0]
    t0, t1 = towers
    # Line between the two towers (runs along the eastern periphery) ...
    for ax, ay, az in arms:
        catenary(w, (t0[0] + ax, t0[1] + ay, az), (t1[0] + ax, t1[1] + ay, az), 0.18)
    # ... and down-lead from tower 0 into the substation entry gantry (3 phases).
    for (ax, ay, az), v in zip(lower + upper[:1], phases):
        catenary(w, (t0[0] + ax, t0[1] + ay, az), (gantry_u, v, 1.0 - 4 * 0.035 + LOT_Z), 0.12)
    # Overhead earth wire.
    catenary(w, (t0[0], t0[1], 2.48), (t1[0], t1[1], 2.48), 0.15, radius=0.005)

    # Medium-voltage distribution along the outer ring towards the city (Kenney poles).
    poles = []
    y = -7.6
    while y < 13.0:
        poles.append((15.62, y))
        y += 2.1
    for k, (px, py) in enumerate(poles):
        place("roads", "electricity-pole", px, py, 0, "STREET_LIGHTING", f"mv_pole_{k:02d}",
              z=0.0, solid=True)
    # Kenney pole: cross-arm along X with insulators near x=-0.28 / +0.26 at z~0.5.
    for (ax, ay), (bx, by) in zip(poles, poles[1:]):
        for dx in (-0.255, 0.235):
            catenary(w, (ax + dx, ay, 0.505), (bx + dx, by, 0.505), 0.06, radius=0.006, steps=8)
    w.to_object("power_lines_conductors", "SUBSTATION", 0, 0, 0)
    return poles


# --------------------------------------------------------------------------- street furniture
def lamp_glow(x, y, q, materials, arm=0.2, height=0.585):
    """Small emissive lens under a Kenney lamp head (animated from the dashboard)."""
    ang = q * math.pi / 2
    dx, dy = -math.sin(ang) * arm, math.cos(ang) * arm
    LAMP_LENSES.append((x + dx, y + dy, height))


def lamp_glow_cross(x, y, materials):
    for q in range(4):
        lamp_glow(x, y, q, materials, arm=0.19, height=0.57)


LAMP_LENSES = []


def build_street_furniture(materials):
    """Lamps and trees on sidewalks, traffic lights at main junctions."""
    spacing = {"avenue": 1, "collector": 2, "ring": 3, "center": 2, "local": 3, "industrial": 2}
    counts = {"lamps": 0, "street_trees": 0, "traffic_lights": 0}
    for cell, kind in sorted(ROAD_CELLS.items()):
        mask = plan.road_mask(ROAD_CELLS, RB_CELLS, cell)
        if mask not in ({"E", "W"}, {"N", "S"}):
            continue
        if cell in plan.CROSSINGS or cell in {d[:2] for d in plan.DRIVEWAYS}:
            continue
        # Skip cells right next to a junction (keep corners and crossings clear).
        i, j = cell
        near_junction = any(len(plan.road_mask(ROAD_CELLS, RB_CELLS, n)) > 2 or n in RB_CELLS
                            for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))
                            if n in ROAD_CELLS or n in RB_CELLS)
        if near_junction:
            continue
        k = i if mask == {"E", "W"} else j
        if k % spacing[kind]:
            continue
        x, y = plan.cell_center(i, j)
        side = 1 if (k // spacing[kind]) % 2 else -1
        horizontal = mask == {"E", "W"}
        for s in ((-1, 1) if kind == "avenue" else (side,)):
            # Kenney light-square: pole at origin, arm towards +Y.
            px, py = (x, y + s * 0.445) if horizontal else (x + s * 0.445, y)
            if kind == "avenue" and (k + (s > 0)) % 2:
                place("suburban", "tree-small", px, py, 0, "VEGETATION", f"street_tree_{i}_{j}",
                      scale=1.15, z=LOT_Z, rot=(i * 7 + j) % 6)
                counts["street_trees"] += 1
                continue
            q = (2 if s > 0 else 0) if horizontal else (1 if s > 0 else 3)
            place("roads", "light-square", px, py, q, "STREET_LIGHTING", f"lamp_{i}_{j}",
                  z=LOT_Z)
            lamp_glow(px, py, q, materials)
            counts["lamps"] += 1
    # Traffic lights at the four corners of the main signalised junctions.
    for ci, cj in ((17, 17), (12, 17), (22, 17), (17, 12), (17, 22), (17, 8), (17, 26)):
        x, y = plan.cell_center(ci, cj)
        for q, (sx, sy) in enumerate(((1, -1), (1, 1), (-1, 1), (-1, -1))):
            place("roads", "traffic-light", x + sx * 0.43, y + sy * 0.43, q, "URBAN_DETAILS",
                  f"traffic_light_{ci}_{cj}", z=LOT_Z)
            counts["traffic_lights"] += 1
    # Street name signs at neighbourhood entrances.
    for ci, cj in ((8, 8), (8, 26), (24, 26), (13, 26), (26, 8)):
        x, y = plan.cell_center(ci, cj)
        place("roads", "road-sign-street", x - 0.43, y - 0.43, 0, "URBAN_DETAILS",
              f"street_sign_{ci}_{cj}", z=LOT_Z)
    # Lenses: one shared emissive mesh for all lamp heads.
    lenses = MeshBuilder(materials)
    for lx, ly, lz in LAMP_LENSES:
        lenses.box(lx, ly, lz + LOT_Z, 0.05, 0.05, 0.012, "glow")
    lenses.to_object("street_lamp_lenses", "STREET_LIGHTING")
    return counts


def build_roundabout_islands(materials):
    (wx, wy), (ex, ey) = [plan.cell_center(*c) for c in plan.ROUNDABOUTS]
    b = MeshBuilder(materials)
    for x, y in ((wx, wy), (ex, ey)):
        b.cyl(x, y, 0.0, 0.3, LOT_Z + 0.01, "park", segments=24)
    b.to_object("roundabout_islands", "VEGETATION")
    place("suburban", "tree-large", wx, wy, 0, "VEGETATION", "roundabout_west_tree", scale=1.7,
          z=LOT_Z + 0.01)
    for k, a in enumerate((0.6, 2.7, 4.8)):
        place("suburban", "tree-small", wx + math.cos(a) * 0.18, wy + math.sin(a) * 0.18, 0,
              "VEGETATION", "roundabout_west_shrub", scale=0.9, z=LOT_Z + 0.01)
    # Industrial gateway: a wind turbine on the eastern roundabout island.
    place("industrial", "windmill", ex, ey, 1, "URBAN_DETAILS", "roundabout_east_wind_turbine",
          z=LOT_Z + 0.01)


def build_leftovers(materials):
    """Small green pockets on cells not taken by roads or parcels."""
    owned = set()
    for block in plan.BLOCKS:
        owned |= plan.block_cells(block)
    rng = rng_for("leftovers")
    b = MeshBuilder(materials)
    for i in range(3, 32):
        for j in range(3, 32):
            cell = (i, j)
            if cell in owned or is_road(cell):
                continue
            x, y = plan.cell_center(i, j)
            b.box(x, y, 0.0, 1.0, 1.0, LOT_Z, "grass")
            for _ in range(2):
                tx, ty = x + rng.uniform(-0.28, 0.28), y + rng.uniform(-0.28, 0.28)
                place("suburban", "tree-large" if rng.random() < 0.5 else "tree-small", tx, ty,
                      0, "VEGETATION", f"pocket_tree_{i}_{j}", scale=1.3 + rng.random() * 0.4,
                      rot=rng.random() * 6.28)
    b.to_object("green_pockets", "VEGETATION")


def build_outskirts(poles):
    """Irregular tree belt between the ring road and the edge of the terrain."""
    rng = rng_for("outskirts")
    half = plan.TERRAIN_HALF - 0.4
    inner = 15.8
    keep_clear = [(14.5, -18, 18, -7.0)]   # HV line corridor east of the substation
    trees = 0
    for _ in range(3000):
        if trees >= 230:
            break
        x, y = rng.uniform(-half, half), rng.uniform(-half, half)
        if max(abs(x), abs(y)) < inner:
            continue
        if any(a < x < c and b < y < d for a, b, c, d in keep_clear):
            continue
        if any(abs(x - px) < 0.45 and abs(y - py) < 0.45 for px, py in poles):
            continue
        if overlaps(x - 0.17, y - 0.17, x + 0.17, y + 0.17):
            continue
        # Denser clusters: accept more trees in "groves" defined by a noise-like field.
        field = math.sin(x * 0.55) * math.cos(y * 0.47) + math.sin((x + y) * 0.23)
        if field < -0.35 and rng.random() < 0.8:
            continue
        big = rng.random() < 0.6
        place("suburban", "tree-large" if big else "tree-small", x, y, 0, "VEGETATION",
              "outskirts_tree", scale=1.4 + rng.random() * 0.6, z=0.0, rot=rng.random() * 6.28)
        FOOTPRINTS.append(("outskirts_tree", x - 0.17, y - 0.17, x + 0.17, y + 0.17))
        trees += 1
    return trees


# --------------------------------------------------------------------------- lighting & camera
def setup_render_scene(scene):
    world = bpy.data.worlds.new("dashboard_world")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*srgb("#1e3a33"), 1)
    bg.inputs["Strength"].default_value = 1.1
    scene.world = world
    sun_data = bpy.data.lights.new("key_sun", "SUN")
    sun_data.energy = 3.2
    sun_data.angle = math.radians(4)
    sun_data.color = srgb("#fff3e0")
    sun = bpy.data.objects.new("key_sun", sun_data)
    sun.rotation_euler = (math.radians(48), math.radians(8), math.radians(-35))
    scene.collection.objects.link(sun)
    fill_data = bpy.data.lights.new("cyan_fill", "SUN")
    fill_data.energy = 0.6
    fill_data.color = srgb("#7ff0d8")
    fill = bpy.data.objects.new("cyan_fill", fill_data)
    fill.rotation_euler = (math.radians(65), 0, math.radians(150))
    scene.collection.objects.link(fill)
    cam_data = bpy.data.cameras.new("aerial_three_quarter")
    cam_data.lens = 38
    cam_data.clip_end = 400
    cam = bpy.data.objects.new("aerial_three_quarter", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 48
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    scene.view_settings.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            scene.view_settings.look = look
            break
        except TypeError:
            continue
    return cam


def aim(cam, location, target, lens=38, ortho=None):
    cam.location = location
    direction = Vector(target) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.type = "ORTHO" if ortho else "PERSP"
    if ortho:
        cam.data.ortho_scale = ortho
    cam.data.lens = lens


def render_views(scene, cam):
    out = HERE / "renders"
    out.mkdir(exist_ok=True)
    views = {
        "01_hero_three_quarter": ((50, -50, 46), (0, -1.5, 0), 33, None),
        "02_top_plan": ((0, 0, 90), (0, 0, 0), 50, 65),
        "03_core_closeup": ((10, -12, 9), (0, 0, 1.5), 32, None),
        "04_residential_west": ((-12, -12, 9), (-21, -4, 0), 30, None),
        "05_industrial_substation": ((30, -2, 11), (18, -18, 0), 30, None),
        "06_north_view": ((-30, 40, 30), (0, 2, 0), 36, None),
    }
    only = [a.split("=", 1)[1] for a in ARGS if a.startswith("--views=")]
    for name, (loc, target, lens, ortho) in views.items():
        if only and not any(name.startswith(o) for o in only[0].split(",")):
            continue
        aim(cam, loc, target, lens, ortho)
        scene.render.resolution_y = 1600 if ortho else 1000
        scene.render.filepath = str(out / f"{name}.png")
        bpy.ops.render.render(write_still=True)
    aim(cam, *views["01_hero_three_quarter"][:3])


# --------------------------------------------------------------------------- validation
def validate(scene):
    report = {"plan_problems": [], "overlaps": [], "floating": [], "outside_terrain": [],
              "missing_textures": [], "substation_outside_fence": []}
    problems, dead_ends = plan.validate_plan()
    report["plan_problems"] = problems
    report["dead_ends"] = [list(c) for c in sorted(dead_ends)]
    # Solid footprints must not overlap each other or road cells.
    fps = FOOTPRINTS
    for a in range(len(fps)):
        n0, x0, y0, x1, y1 = fps[a]
        for b in range(a + 1, len(fps)):
            m0, a0, b0, a1, b1 = fps[b]
            if x0 < a1 - 0.015 and x1 > a0 + 0.015 and y0 < b1 - 0.015 and y1 > b0 + 0.015:
                report["overlaps"].append([n0, m0])
    for name, x0, y0, x1, y1 in fps:
        for cell in list(ROAD_CELLS) + list(RB_CELLS):
            cx, cy = plan.cell_center(*cell)
            if x0 < cx + 0.5 - 0.02 and x1 > cx - 0.5 + 0.02 and \
                    y0 < cy + 0.5 - 0.02 and y1 > cy - 0.5 + 0.02:
                if not name.startswith(("mv_pole", "hv_tower")):
                    report["overlaps"].append([name, f"road{cell}"])
    # Ground contact and terrain bounds (world space).
    half = 32.0
    base_levels = (0.0, LOT_Z * S, (LOT_Z + 0.01) * S, (LOT_Z + 0.14) * S)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in scene.objects:
        if obj.type != "MESH" or obj.name.startswith("TERRAIN"):
            continue
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        zmin = min(p.z for p in pts)
        if max(abs(p.x) for p in pts) > half + 0.01 or max(abs(p.y) for p in pts) > half + 0.01:
            report["outside_terrain"].append(obj.name)
        model = obj.get("model", "")
        stacked = "container" in obj.name
        if model and not stacked and not any(abs(zmin - z) < 0.006 for z in base_levels):
            report["floating"].append([obj.name, round(zmin, 3)])
    for img in bpy.data.images:
        if img.source == "FILE" and not img.packed_file and not Path(
                bpy.path.abspath(img.filepath)).is_file():
            report["missing_textures"].append(img.name)
    # Substation equipment must stay inside the fenced parcel.
    sub = next(b for b in plan.BLOCKS if b[1] == "substation")
    sx0, sy0, sx1, sy1 = block_rect(sub)
    for rec in PLACED:
        if rec["collection"] == "SUBSTATION" and rec["name"].startswith("substation_")                 and "warning_sign" not in rec["name"] and rec["model"] != "aux":
            if not (sx0 < rec["x"] < sx1 and sy0 < rec["y"] < sy1):
                report["substation_outside_fence"].append(rec["name"])
        if rec["model"] == "aux" and rec["name"].startswith("substation_") and                 (rec["x"], rec["y"]) != (0, 0) and not (sx0 < rec["x"] < sx1 and sy0 < rec["y"] < sy1):
            report["substation_outside_fence"].append(rec["name"])
    report["counts"] = {name: len(coll(name).objects) for name in COLLECTIONS}
    report["triangles"] = sum(sum(len(p.vertices) - 2 for p in o.data.polygons)
                              for o in scene.objects if o.type == "MESH")
    report["unique_meshes"] = len({o.data.name for o in scene.objects if o.type == "MESH"})
    del depsgraph
    return report


# --------------------------------------------------------------------------- export
def export_glb(path):
    bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=False,
        export_cameras=False, export_lights=False, export_extras=True,
        export_yup=True, export_apply=False, export_texcoords=True, export_normals=True,
        export_tangents=False, export_materials="EXPORT", export_image_format="AUTO",
        export_animations=False, export_skins=False, export_morph=False,
    )


def route_world(cells):
    """Convert a list of grid cells to Three.js world coordinates (x, z)."""
    out = []
    for i, j in cells:
        x, y = plan.cell_center(i, j)
        out.append([round(x * S, 3), round(-y * S, 3)])
    return out


def shortest_path(a, b):
    from collections import deque
    graph = set(ROAD_CELLS) | set(RB_CELLS)
    prev = {a: None}
    todo = deque([a])
    while todo:
        c = todo.popleft()
        if c == b:
            break
        for di, dj in plan.DIRS.values():
            n = (c[0] + di, c[1] + dj)
            if n in graph and n not in prev:
                prev[n] = c
                todo.append(n)
    path, c = [], b
    while c is not None:
        path.append(c)
        c = prev[c]
    return path[::-1]


def simplify(path):
    """Keep only the corners of a cell path."""
    if len(path) < 3:
        return path
    out = [path[0]]
    for p, c, n in zip(path, path[1:], path[2:]):
        if (c[0] - p[0], c[1] - p[1]) != (n[0] - c[0], n[1] - c[1]):
            out.append(c)
    out.append(path[-1])
    return out


def write_sidecar(report, road_stats, furniture):
    source = (26, 5)   # substation gate on the industrial road
    targets = {"centro": (17, 17), "residencial_oeste": (5, 17), "residencial_norte": (24, 32),
               "industrial": (29, 8), "plaza": (13, 22), "noreste": (32, 22)}
    routes = {name: route_world(simplify(shortest_path(source, t)))
              for name, t in targets.items()}
    sidecar = {
        "version": 1,
        "units": "Three.js world units (glTF metres), y up",
        "terrain": {"size": 64, "half": 32},
        "camera": {"target": [0, 1.2, 0], "direction": [1, 0.85, 1.05]},
        "glowMaterials": ["city_glow"],
        "energyRoutes": routes,
        "stats": {"triangles": report["triangles"], "uniqueMeshes": report["unique_meshes"],
                  "objects": sum(report["counts"].values()), "roads": road_stats,
                  "furniture": furniture},
    }
    (STATIC_CITY / "energy_city.json").write_text(json.dumps(sidecar, indent=1),
                                                  encoding="utf-8")


# --------------------------------------------------------------------------- main
def main():
    scene = reset_scene()
    materials = build_materials()
    build_terrain(materials)
    road_stats = build_roads()
    zones = {}
    for block in plan.BLOCKS:
        zones.setdefault(block[1], []).append(block)
    # Order matters only for the overlap packer: fixed layouts first.
    sub = build_substation(next(b for b in plan.BLOCKS if b[1] == "substation"), materials)
    for block in zones["industrial"]:
        (build_industrial_yard if block[0] == "ind_yard" else build_industrial_east)(
            block, materials)
    for block in zones["plaza"]:
        build_plaza(block, materials)
    for block in zones["towers"]:
        lot_slab(block, "paving", materials)
        build_core(block)
    for block in zones["midrise"]:
        lot_slab(block, "paving", materials)
        build_midrise(block)
    for block in zones["houses"]:
        build_houses(block, materials)
    for block in zones["park"]:
        build_park(block, materials)
    for block in zones["buffer"]:
        lot_slab(block, "grass", materials)
        build_buffer(block)
    build_roundabout_islands(materials)
    build_leftovers(materials)
    poles = build_power_lines(materials, sub)
    furniture = build_street_furniture(materials)
    furniture["outskirts_trees"] = build_outskirts(poles)

    cam = setup_render_scene(scene)
    aim(cam, (44, -44, 40), (0, 0, 0), 36)
    report = validate(scene)
    report["roads"] = road_stats
    report["furniture"] = furniture
    (HERE / "validation_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("VALIDATION", json.dumps({k: (v if not isinstance(v, list) else len(v))
                                    for k, v in report.items()}))

    bpy.ops.file.pack_all()
    bpy.data.orphans_purge(do_recursive=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(HERE / "energy_city.blend"), compress=True)
    if "--no-export" not in ARGS:
        export_glb(STATIC_CITY / "energy_city.glb")
        write_sidecar(report, road_stats, furniture)
    if "--no-render" not in ARGS:
        render_views(scene, cam)


main()
