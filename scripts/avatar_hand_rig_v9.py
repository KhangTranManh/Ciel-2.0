"""Build an anatomical three-joint hand rig with reusable relaxed poses."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_finger_rig import kmeans_1d, mean_point, pca_axis, percentile, smoothstep


BOUNDARIES = (0.0, 0.36, 0.69, 1.0)
CENTERS = tuple((left + right) / 2.0 for left, right in zip(BOUNDARIES[:-1], BOUNDARIES[1:]))
FINGER_NAMES = {
    "Left": ("Pinky", "Ring", "Middle", "Index", "Thumb"),
    "Right": ("Thumb", "Index", "Middle", "Ring", "Pinky"),
}
FINGER_SCALE = {"Thumb": 0.72, "Index": 0.88, "Middle": 1.0, "Ring": 1.07, "Pinky": 1.12}
ACTION_POSES = {
    "Ciel_Idle_Neutral": {"fingers": (10.0, 15.0, 8.0), "thumb": (8.0, 11.0, 6.0)},
    "Ciel_Wave": {"fingers": (6.0, 9.0, 5.0), "thumb": (7.0, 9.0, 5.0)},
    "Ciel_Walk_InPlace": {"fingers": (12.0, 18.0, 10.0), "thumb": (9.0, 13.0, 7.0)},
    "Ciel_Run_InPlace": {"fingers": (14.0, 21.0, 12.0), "thumb": (13.0, 18.0, 10.0)},
    "Ciel_Talk_Open": {"fingers": (5.0, 7.0, 4.0), "thumb": (6.0, 8.0, 4.0)},
    "Ciel_Talk_Right": {"fingers": (7.0, 10.0, 6.0), "thumb": (7.0, 10.0, 5.0)},
    "Ciel_Shrug": {"fingers": (5.0, 8.0, 4.0), "thumb": (6.0, 8.0, 4.0)},
}
POSE_LIBRARY = {
    "Ciel_HandPose_Relaxed": {"fingers": (10.0, 15.0, 8.0), "thumb": (8.0, 11.0, 6.0)},
    "Ciel_HandPose_SoftFist": {"fingers": (18.0, 28.0, 16.0), "thumb": (13.0, 18.0, 10.0)},
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def collect_hand_data(armature, mesh, side: str):
    hand_name = f"mixamorig:{side}Hand"
    hand_group = mesh.vertex_groups[hand_name]
    hand_bone = armature.data.bones[hand_name]
    mesh_to_armature = armature.matrix_world.inverted() @ mesh.matrix_world
    armature_to_hand = hand_bone.matrix_local.inverted()
    records = []
    for vertex in mesh.data.vertices:
        try:
            weight = hand_group.weight(vertex.index)
        except RuntimeError:
            continue
        if weight < 0.65:
            continue
        local = armature_to_hand @ (mesh_to_armature @ vertex.co)
        if local.y >= 7.5:
            records.append({"index": vertex.index, "weight": weight, "local": local})
    distal = [record["local"] for record in records if record["local"].y >= 9.0]
    axis = pca_axis(distal)
    centers, assignments = kmeans_1d([record["local"].dot(axis) for record in records])
    for record, assignment in zip(records, assignments):
        record["cluster"] = assignment

    fingers = []
    for cluster, finger_name in enumerate(FINGER_NAMES[side]):
        finger_records = [record for record in records if record["cluster"] == cluster]
        points = [record["local"] for record in finger_records]
        y_values = [point.y for point in points]
        base_y = max(7.5, percentile(y_values, 0.08))
        tip_y = percentile(y_values, 0.97)
        span = tip_y - base_y
        boundaries = []
        for fraction in BOUNDARIES:
            y = base_y + span * fraction
            nearby = [point for point in points if abs(point.y - y) <= span * 0.10]
            if not nearby:
                nearby = sorted(points, key=lambda point: abs(point.y - y))[:12]
            boundary = mean_point(nearby)
            boundary.y = y
            boundaries.append(boundary)
        fingers.append(
            {
                "name": finger_name,
                "records": finger_records,
                "base_y": base_y,
                "tip_y": tip_y,
                "boundaries": boundaries,
            }
        )
    return {
        "hand": hand_name,
        "hand_matrix": hand_bone.matrix_local.copy(),
        "hand_group": hand_group,
        "axis": axis,
        "cluster_centers": centers,
        "fingers": fingers,
    }


def build_bones(armature, side_data):
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for side, data in side_data.items():
        parent = armature.data.edit_bones[data["hand"]]
        for finger in data["fingers"]:
            finger["bones"] = []
            current_parent = parent
            for joint in range(3):
                name = f"mixamorig:{side}Hand{finger['name']}{joint + 1}"
                bone = armature.data.edit_bones.new(name)
                bone.head = data["hand_matrix"] @ finger["boundaries"][joint]
                bone.tail = data["hand_matrix"] @ finger["boundaries"][joint + 1]
                bone.parent = current_parent
                bone.use_connect = current_parent != parent
                bone.use_deform = True
                finger["bones"].append(name)
                current_parent = bone
    bpy.ops.object.mode_set(mode="OBJECT")


def assign_weights(mesh, side_data):
    report = {}
    for side, data in side_data.items():
        hand_group = data["hand_group"]
        side_report = []
        for finger in data["fingers"]:
            groups = [mesh.vertex_groups.new(name=name) for name in finger["bones"]]
            span = finger["tip_y"] - finger["base_y"]
            changed = 0
            for record in finger["records"]:
                raw_progress = (record["local"].y - finger["base_y"]) / span
                progress = min(max(raw_progress, 0.0), 1.0)
                transfer = record["weight"] * smoothstep(progress) * 0.90
                if transfer <= 1e-5:
                    continue
                vertex = mesh.data.vertices[record["index"]]
                other_count = sum(
                    1
                    for membership in vertex.groups
                    if membership.group != hand_group.index and membership.weight > 1e-6
                )
                limit = max(0, 3 - other_count)
                if limit == 0:
                    continue
                nearest = sorted(range(3), key=lambda index: abs(progress - CENTERS[index]))[: min(2, limit)]
                raw = [math.exp(-0.5 * ((progress - CENTERS[index]) / 0.21) ** 2) for index in nearest]
                total = sum(raw)
                hand_group.add([record["index"]], record["weight"] - transfer, "REPLACE")
                for index, value in zip(nearest, raw):
                    groups[index].add([record["index"]], transfer * value / total, "REPLACE")
                changed += 1
            side_report.append({"finger": finger["name"], "bones": finger["bones"], "changed_vertices": changed})
        report[side] = side_report
    return report


def apply_pose(armature, action, side_data, pose, frames):
    for frame in frames:
        for side, data in side_data.items():
            direction = 1.0 if side == "Left" else -1.0
            for finger in data["fingers"]:
                key = "thumb" if finger["name"] == "Thumb" else "fingers"
                scale = FINGER_SCALE[finger["name"]]
                for joint, (bone_name, degrees) in enumerate(zip(finger["bones"], pose[key])):
                    bone = armature.pose.bones[bone_name]
                    bone.rotation_mode = "XYZ"
                    bone.rotation_euler.x = math.radians(degrees * scale * direction)
                    # Keep the generated bone's anatomical plane. An extra local-Z
                    # twist makes this low-poly thumb kick upward at the knuckle.
                    bone.rotation_euler.z = 0.0
                    bone.keyframe_insert("rotation_euler", frame=frame, group=bone_name)
    for curve in action.layers[0].strips[0].channelbag(action.slots[0]).fcurves:
        if "Hand" not in curve.data_path or not any(name in curve.data_path for name in ("Thumb", "Index", "Middle", "Ring", "Pinky")):
            continue
        for point in curve.keyframe_points:
            point.interpolation = "BEZIER"
            point.handle_left_type = "AUTO_CLAMPED"
            point.handle_right_type = "AUTO_CLAMPED"
        curve.update()


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    armature = next(obj for obj in scene.objects if obj.type == "ARMATURE")
    mesh = next(obj for obj in scene.objects if obj.type == "MESH")
    side_data = {side: collect_hand_data(armature, mesh, side) for side in ("Left", "Right")}
    build_bones(armature, side_data)
    weight_report = assign_weights(mesh, side_data)
    animation = armature.animation_data_create()

    keyed = []
    for action_name, pose in ACTION_POSES.items():
        action = bpy.data.actions.get(action_name)
        if action is None:
            continue
        animation.action = action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        apply_pose(armature, action, side_data, pose, [start, end])
        keyed.append(action_name)

    for action_name, pose in POSE_LIBRARY.items():
        action = bpy.data.actions.new(action_name)
        action.use_fake_user = True
        animation.action = action
        apply_pose(armature, action, side_data, pose, [1])

    idle = bpy.data.actions.get("Ciel_Idle_Neutral")
    if idle:
        animation.action = idle
        scene.frame_set(int(idle.frame_range[0]))
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps(
            {
                "output": str(output),
                "method": "three anatomical joints per digit with relaxed pose assets; no procedural finger pulse",
                "keyed_actions": keyed,
                "pose_library": list(POSE_LIBRARY),
                "weights": weight_report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
