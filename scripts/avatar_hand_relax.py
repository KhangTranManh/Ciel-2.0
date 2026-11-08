"""Add conservative helper bones for a relaxed whole-finger curl pose."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--angle-degrees", type=float, default=7.0)
    return parser.parse_args(argv)


def smoothstep(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return value * value * (3.0 - 2.0 * value)


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    specs = []

    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for side in ("Left", "Right"):
        hand_name = f"mixamorig:{side}Hand"
        helper_name = f"Ciel_{side}FingerCurl"
        hand = armature.data.edit_bones[hand_name]
        helper = armature.data.edit_bones.get(helper_name) or armature.data.edit_bones.new(helper_name)
        helper.head = hand.head.lerp(hand.tail, 0.34)
        helper.tail = hand.tail.copy()
        helper.roll = hand.roll
        helper.parent = hand
        helper.use_connect = False
        helper.use_deform = True
        specs.append(
            {
                "side": side,
                "hand": hand_name,
                "helper": helper_name,
                "head": list(helper.head),
                "tail": list(helper.tail),
            }
        )
    bpy.ops.object.mode_set(mode="OBJECT")

    for spec in specs:
        hand_bone = armature.data.bones[spec["hand"]]
        head_world = armature.matrix_world @ hand_bone.head_local
        tail_world = armature.matrix_world @ hand_bone.tail_local
        axis = tail_world - head_world
        axis_length_squared = max(axis.length_squared, 1e-12)
        hand_group = mesh.vertex_groups[spec["hand"]]
        helper_group = mesh.vertex_groups.get(spec["helper"]) or mesh.vertex_groups.new(
            name=spec["helper"]
        )
        changed = 0
        transferred = 0.0
        for vertex in mesh.data.vertices:
            try:
                hand_weight = hand_group.weight(vertex.index)
            except RuntimeError:
                continue
            if hand_weight <= 1e-6:
                continue
            world_position = mesh.matrix_world @ vertex.co
            progress = (world_position - head_world).dot(axis) / axis_length_squared
            factor = smoothstep((progress - 0.34) / 0.56) * 0.72
            if factor <= 1e-5:
                continue
            helper_weight = hand_weight * factor
            hand_group.add([vertex.index], hand_weight - helper_weight, "REPLACE")
            helper_group.add([vertex.index], helper_weight, "REPLACE")
            changed += 1
            transferred += helper_weight
        spec["changed_vertices"] = changed
        spec["transferred_weight"] = transferred

        pose_bone = armature.pose.bones[spec["helper"]]
        pose_bone.rotation_mode = "XYZ"
        direction = 1.0 if spec["side"] == "Left" else -1.0
        pose_bone.rotation_euler.x = math.radians(args.angle_degrees * direction)

    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps(
            {
                "output": str(output),
                "angle_degrees": args.angle_degrees,
                "helpers": specs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
