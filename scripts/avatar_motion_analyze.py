"""Report the animation channels needed for full-body motion refinement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


TARGET_BONES = (
    "mixamorig:Hips",
    "mixamorig:Spine",
    "mixamorig:Spine1",
    "mixamorig:Spine2",
    "mixamorig:LeftShoulder",
    "mixamorig:RightShoulder",
    "mixamorig:LeftArm",
    "mixamorig:RightArm",
    "mixamorig:LeftForeArm",
    "mixamorig:RightForeArm",
    "mixamorig:LeftHand",
    "mixamorig:RightHand",
)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def channelbag(action: bpy.types.Action):
    return action.layers[0].strips[0].channelbag(action.slots[0])


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    animation = armature.animation_data_create()
    scene = bpy.context.scene
    report = {"armature": armature.name, "bones": {}, "actions": []}
    for bone_name in TARGET_BONES:
        bone = armature.pose.bones.get(bone_name)
        if bone:
            report["bones"][bone_name] = {
                "rotation_mode": bone.rotation_mode,
                "parent": bone.parent.name if bone.parent else None,
            }
    for action in bpy.data.actions:
        if not action.name.startswith("Ciel_"):
            continue
        animation.action = action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        samples = []
        for frame in sorted({start, (start + end) // 2, end}):
            scene.frame_set(frame)
            samples.append(
                {
                    "frame": frame,
                    "world_positions": {
                        bone_name: list((armature.matrix_world @ armature.pose.bones[bone_name].matrix).translation)
                        for bone_name in TARGET_BONES
                        if bone_name in armature.pose.bones
                    },
                }
            )
        channels = {}
        for curve in channelbag(action).fcurves:
            for bone_name in TARGET_BONES:
                prefix = f'pose.bones["{bone_name}"]'
                if curve.data_path.startswith(prefix):
                    channels.setdefault(bone_name, {}).setdefault(curve.data_path[len(prefix) + 1 :], []).append(
                        {
                            "index": curve.array_index,
                            "keys": len(curve.keyframe_points),
                        }
                    )
        report["actions"].append(
            {
                "name": action.name,
                "frame_range": [start, end],
                "channels": channels,
                "samples": samples,
            }
        )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output)}))


if __name__ == "__main__":
    main()
