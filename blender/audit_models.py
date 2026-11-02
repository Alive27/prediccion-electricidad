"""Audit Kenney GLB models: bounding box, pivot, materials, textures. Run with:
blender --background --factory-startup --python audit_models.py -- <kit_dir> [<kit_dir> ...]"""
import sys, json, bpy
from mathutils import Vector
from pathlib import Path

args = sys.argv[sys.argv.index("--") + 1:]
out = {}
for kit in args:
    for glb in sorted(Path(kit, "Models", "GLB format").glob("*.glb")):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(glb))
        pts = [o.matrix_world @ Vector(c) for o in bpy.context.scene.objects if o.type == "MESH" for c in o.bound_box]
        if not pts:
            continue
        mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        mats = sorted({m.name for m in bpy.data.materials if m.users})
        imgs = sorted({i.name for i in bpy.data.images})
        tris = sum(len(o.data.polygons) for o in bpy.context.scene.objects if o.type == "MESH")
        out[f"{Path(kit).name.split('_')[1]}/{glb.stem}"] = dict(
            size=[round(v, 3) for v in (mx - mn)], min=[round(v, 3) for v in mn],
            max=[round(v, 3) for v in mx], mats=mats, imgs=imgs, faces=tris,
            objs=len(bpy.context.scene.objects))
Path(bpy.path.abspath("//"), ).mkdir(exist_ok=True)
Path(__file__).with_name("model_audit.json").write_text(json.dumps(out, indent=1))
print("AUDITED", len(out))
