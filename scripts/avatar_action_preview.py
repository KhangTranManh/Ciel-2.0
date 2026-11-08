"""Render representative arm-motion frames for every imported avatar action."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_inspect import add_preview_setup, point_at, world_bounds


ARM_BONES = (
    "mixamorig:LeftShoulder",
    "mixamorig:LeftArm",
    "mixamorig:LeftForeArm",
    "mixamorig:LeftHand",
    "mixamorig:RightShoulder",
    "mixamorig:RightArm",
    "mixamorig:RightForeArm",
    "mixamorig:RightHand",
)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def safe_name(action_name: str) -> str:
    short = action_name.replace("Armature|", "").replace("|BaseLayer", "")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", short).strip("_").lower()


def arm_motion_score(armature: bpy.types.Object) -> float:
    score = 0.0
    for bone_name in ARM_BONES:
        bone = armature.pose.bones.get(bone_name)
        if bone:
            score += abs(bone.matrix_basis.to_quaternion().angle)
    return score


def representative_frame(
    scene: bpy.types.Scene,
    armature: bpy.types.Object,
    action: bpy.types.Action,
) -> tuple[int, float]:
    start = int(math.floor(action.frame_range[0]))
    end = int(math.ceil(action.frame_range[1]))
    step = max(1, (end - start) // 48)
    candidates = list(range(start, end + 1, step))
    if end not in candidates:
        candidates.append(end)

    best_frame = start
    best_score = -1.0
    for frame in candidates:
        scene.frame_set(frame)
        score = arm_motion_score(armature)
        if score > best_score:
            best_frame = frame
            best_score = score
    return best_frame, best_score


def bone_angles(armature: bpy.types.Object) -> dict[str, float]:
    values = {}
    for bone_name in ARM_BONES:
        bone = armature.pose.bones.get(bone_name)
        if bone:
            values[bone_name] = round(math.degrees(bone.matrix_basis.to_quaternion().angle), 2)
    return values


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    scene.render.fps = 60
    armatures = [obj for obj in scene.objects if obj.type == "ARMATURE"]
    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    if len(armatures) != 1:
        raise RuntimeError(f"Expected one armature, found {len(armatures)}")
    if not meshes:
        raise RuntimeError("No avatar mesh found")

    armature = armatures[0]
    armature.data.pose_position = "POSE"
    animation_data = armature.animation_data_create()

    minimum, maximum = world_bounds(meshes)
    center = (minimum + maximum) / 2
    size = maximum - minimum
    distance = max(size.length * 1.5, 2.0)
    camera = add_preview_setup(center, size)
    scene.render.resolution_x = 576
    scene.render.resolution_y = 768

    actions = sorted(bpy.data.actions, key=lambda action: action.name)
    report = []
    for action in actions:
        animation_data.action = action
        frame, score = representative_frame(scene, armature, action)
        scene.frame_set(frame)
        short_name = safe_name(action.name)

        rendered = {}
        for view_name, offset in {
            "front": Vector((0.0, -distance, 0.0)),
            "three_quarter": Vector((-distance * 0.7, -distance * 0.7, 0.0)),
        }.items():
            camera.location = center + offset
            point_at(camera, center)
            destination = output_dir / f"{short_name}_{view_name}.png"
            scene.render.filepath = str(destination)
            bpy.ops.render.render(write_still=True)
            rendered[view_name] = str(destination)

        report.append(
            {
                "action": action.name,
                "representative_frame": frame,
                "arm_motion_score": round(score, 6),
                "bone_rotation_degrees": bone_angles(armature),
                "renders": rendered,
            }
        )

    report_path = output_dir / "action_preview_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "actions": len(report)}))


if __name__ == "__main__":
    main()
