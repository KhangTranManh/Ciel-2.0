"""Import an avatar FBX non-destructively and produce an inspection checkpoint.

Run with Blender, not the system Python:

    blender --background --python scripts/avatar_inspect.py -- \
        --input path/to/avatar.fbx --output-dir Model/Working
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def import_fbx(path: Path) -> None:
    result = bpy.ops.import_scene.fbx(filepath=str(path), use_anim=True)
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX import failed: {result}")


def world_bounds(mesh_objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    corners = [obj.matrix_world @ Vector(corner) for obj in mesh_objects for corner in obj.bound_box]
    if not corners:
        raise RuntimeError("No mesh bounds found after FBX import")
    return (
        Vector(tuple(min(point[i] for point in corners) for i in range(3))),
        Vector(tuple(max(point[i] for point in corners) for i in range(3))),
    )


def inspect_scene(source: Path) -> dict[str, object]:
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    minimum, maximum = world_bounds(meshes)

    mesh_report = []
    for obj in meshes:
        data = obj.data
        mesh_report.append(
            {
                "name": obj.name,
                "vertices": len(data.vertices),
                "edges": len(data.edges),
                "polygons": len(data.polygons),
                "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
                "vertex_groups": len(obj.vertex_groups),
                "shape_keys": (
                    [key.name for key in data.shape_keys.key_blocks] if data.shape_keys else []
                ),
            }
        )

    armature_report = []
    for obj in armatures:
        armature_report.append(
            {
                "name": obj.name,
                "bones": len(obj.data.bones),
                "bone_names": [bone.name for bone in obj.data.bones],
                "active_action": (
                    obj.animation_data.action.name
                    if obj.animation_data and obj.animation_data.action
                    else None
                ),
            }
        )

    actions = []
    for action in bpy.data.actions:
        start, end = action.frame_range
        actions.append(
            {
                "name": action.name,
                "frame_start": float(start),
                "frame_end": float(end),
                "frames": float(end - start + 1),
            }
        )

    images = []
    for image in bpy.data.images:
        resolved = Path(bpy.path.abspath(image.filepath)) if image.filepath else None
        images.append(
            {
                "name": image.name,
                "filepath": str(resolved) if resolved else None,
                "packed": bool(image.packed_file),
                "available": bool(image.packed_file or (resolved and resolved.exists())),
                "size": list(image.size),
            }
        )

    return {
        "source_fbx": str(source),
        "blender_version": bpy.app.version_string,
        "scene_fps": bpy.context.scene.render.fps,
        "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
        "object_counts": {
            kind: sum(1 for obj in bpy.context.scene.objects if obj.type == kind)
            for kind in sorted({obj.type for obj in bpy.context.scene.objects})
        },
        "totals": {
            "vertices": sum(item["vertices"] for item in mesh_report),
            "polygons": sum(item["polygons"] for item in mesh_report),
            "armatures": len(armature_report),
            "actions": len(actions),
            "shape_keys": sum(len(item["shape_keys"]) for item in mesh_report),
        },
        "meshes": mesh_report,
        "armatures": armature_report,
        "actions": actions,
        "images": images,
    }


def point_at(obj: bpy.types.Object, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_preview_setup(center: Vector, size: Vector) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world.color = (0.035, 0.035, 0.045)
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.0

    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.name = "InspectionCamera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(size.z * 1.12, size.x * 1.45)
    scene.camera = camera

    for name, location, energy, area_size in (
        ("KeyLight", center + Vector((-size.x, -size.y * 1.5, size.z * 0.8)), 300, 4.0),
        ("FillLight", center + Vector((size.x, -size.y, size.z * 0.25)), 160, 3.0),
        ("RimLight", center + Vector((0.0, size.y * 1.5, size.z)), 220, 3.0),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.name = name
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = area_size
        point_at(light, center)

    return camera


def render_views(output_dir: Path) -> list[str]:
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    minimum, maximum = world_bounds(meshes)
    center = (minimum + maximum) / 2
    size = maximum - minimum
    distance = max(size.length * 1.5, 2.0)

    original_pose_positions = {obj.name: obj.data.pose_position for obj in armatures}
    for obj in armatures:
        obj.data.pose_position = "REST"

    camera = add_preview_setup(center, size)
    views = {
        "view_neg_y": Vector((0.0, -distance, 0.0)),
        "view_pos_y": Vector((0.0, distance, 0.0)),
        "view_pos_x": Vector((distance, 0.0, 0.0)),
        "view_three_quarter": Vector((-distance * 0.7, -distance * 0.7, 0.0)),
    }
    rendered = []
    for name, offset in views.items():
        camera.location = center + offset
        point_at(camera, center)
        destination = output_dir / f"{name}.png"
        bpy.context.scene.render.filepath = str(destination)
        bpy.ops.render.render(write_still=True)
        rendered.append(str(destination))

    for obj in armatures:
        obj.data.pose_position = original_pose_positions[obj.name]
    return rendered


def main() -> None:
    args = parse_args()
    source = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output_dir.mkdir(parents=True, exist_ok=True)

    clear_scene()
    import_fbx(source)
    bpy.context.scene.render.fps = 60

    report = inspect_scene(source)
    report_path = output_dir / "inspection.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    blend_path = output_dir / "ciel_master.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    rendered = render_views(output_dir)

    print(json.dumps({"blend": str(blend_path), "report": str(report_path), "renders": rendered}))


if __name__ == "__main__":
    main()
