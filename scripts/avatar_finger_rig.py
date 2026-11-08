"""Build a conservative five-segment finger rig for a Meshy avatar."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ACTION_POSES = {
    "Ciel_Idle_Neutral": {"both": (8.0, 12.0, 7.0)},
    "Ciel_Wave": {"Left": (9.0, 14.0, 9.0), "Right": (2.0, 3.0, 2.0)},
    "Ciel_Walk_InPlace": {"both": (13.0, 21.0, 15.0)},
    "Ciel_Run_InPlace": {"both": (18.0, 28.0, 22.0)},
    "Ciel_Talk_Open": {"both": (6.0, 10.0, 7.0)},
    "Ciel_Talk_Right": {
        "Left": (10.0, 16.0, 11.0),
        "Right": (4.0, 7.0, 5.0),
    },
    "Ciel_Shrug": {"both": (5.0, 9.0, 6.0)},
}
ACTION_RESPONSE = {
    "Ciel_Idle_Neutral": 0.08,
    "Ciel_Wave": 0.14,
    "Ciel_Walk_InPlace": 0.18,
    "Ciel_Run_InPlace": 0.20,
    "Ciel_Talk_Open": 0.34,
    "Ciel_Talk_Right": 0.30,
    "Ciel_Shrug": 0.18,
}
FINGER_SCALES = {
    "Left": (0.92, 1.0, 1.04, 1.0, 0.72),
    "Right": (0.72, 1.0, 1.04, 1.0, 0.92),
}
SEGMENT_LABELS = (
    "Proximal",
    "ProximalHelper",
    "Intermediate",
    "DistalHelper",
    "Distal",
)
BOUNDARY_FRACTIONS = (0.0, 0.22, 0.40, 0.62, 0.82, 1.0)
SEGMENT_CENTERS = tuple(
    (left + right) / 2.0
    for left, right in zip(BOUNDARY_FRACTIONS[:-1], BOUNDARY_FRACTIONS[1:])
)
SAMPLE_STEP = 4
LOOP_ACTIONS = {"Ciel_Idle_Neutral", "Ciel_Walk_InPlace", "Ciel_Run_InPlace"}
FINGER_LAGS = {
    "Left": (3, 2, 1, 0, 1),
    "Right": (1, 0, 1, 2, 3),
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def smoothstep(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return value * value * (3.0 - 2.0 * value)


def percentile(values: list[float], position: float) -> float:
    ordered = sorted(values)
    index = min(max(round((len(ordered) - 1) * position), 0), len(ordered) - 1)
    return ordered[index]


def pca_axis(points: list[Vector]) -> Vector:
    mean_x = sum(point.x for point in points) / len(points)
    mean_z = sum(point.z for point in points) / len(points)
    xx = sum((point.x - mean_x) ** 2 for point in points)
    zz = sum((point.z - mean_z) ** 2 for point in points)
    xz = sum((point.x - mean_x) * (point.z - mean_z) for point in points)
    angle = 0.5 * math.atan2(2.0 * xz, xx - zz)
    return Vector((math.cos(angle), 0.0, math.sin(angle)))


def kmeans_1d(values: list[float], count: int = 5) -> tuple[list[float], list[int]]:
    centers = [percentile(values, (index + 0.5) / count) for index in range(count)]
    assignments = [0] * len(values)
    for _ in range(40):
        new_assignments = [
            min(range(count), key=lambda index: abs(value - centers[index])) for value in values
        ]
        new_centers = []
        for index in range(count):
            members = [value for value, assigned in zip(values, new_assignments) if assigned == index]
            new_centers.append(sum(members) / len(members) if members else centers[index])
        if new_assignments == assignments and max(
            abs(a - b) for a, b in zip(centers, new_centers)
        ) < 1e-6:
            assignments = new_assignments
            centers = new_centers
            break
        assignments = new_assignments
        centers = new_centers
    order = sorted(range(count), key=lambda index: centers[index])
    remap = {old: new for new, old in enumerate(order)}
    return [centers[index] for index in order], [remap[index] for index in assignments]


def mean_point(points: list[Vector]) -> Vector:
    return sum(points, Vector()) / len(points)


def expand_control_degrees(degrees: tuple[float, float, float]) -> tuple[float, ...]:
    proximal, intermediate, distal = degrees
    return (
        proximal * 0.70,
        (proximal + intermediate) * 0.15,
        intermediate * 0.70,
        (intermediate + distal) * 0.15,
        distal * 0.70,
    )


def normalized_hand_motion(
    scene: bpy.types.Scene,
    armature: bpy.types.Object,
    frames: list[int],
    side: str,
) -> list[float]:
    pose_bone = armature.pose.bones[f"mixamorig:{side}Hand"]
    positions = []
    rotations = []
    for frame in frames:
        scene.frame_set(frame)
        matrix = armature.matrix_world @ pose_bone.matrix
        positions.append(matrix.translation.copy())
        rotations.append(matrix.to_quaternion())
    speeds = [0.0]
    for index in range(1, len(frames)):
        translation = (positions[index] - positions[index - 1]).length * 6.0
        rotation = rotations[index - 1].rotation_difference(rotations[index]).angle
        speeds.append(translation + rotation)
    if len(speeds) > 1:
        speeds[0] = speeds[1]
    low = percentile(speeds, 0.10)
    high = percentile(speeds, 0.90)
    span = max(high - low, 1e-6)
    normalized = [min(max((value - low) / span, 0.0), 1.0) for value in speeds]
    smoothed = []
    state = normalized[0]
    for value in normalized:
        alpha = 0.38 if value > state else 0.20
        state += (value - state) * alpha
        smoothed.append(state)
    return smoothed


def lagged_sample(values: list[float], index: int, lag: int, loop: bool) -> float:
    sample_index = index - lag
    if loop:
        sample_index %= len(values)
    else:
        sample_index = max(sample_index, 0)
    return values[sample_index]


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    mesh_to_armature = armature.matrix_world.inverted() @ mesh.matrix_world
    side_data = {}

    for side in ("Left", "Right"):
        hand_name = f"mixamorig:{side}Hand"
        hand_group = mesh.vertex_groups[hand_name]
        hand_bone = armature.data.bones[hand_name]
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
        if len(distal) < 100:
            raise RuntimeError(f"Not enough distal hand vertices for {side}: {len(distal)}")
        axis = pca_axis(distal)
        projections = [record["local"].dot(axis) for record in records]
        centers, assignments = kmeans_1d(projections)
        for record, assignment in zip(records, assignments):
            record["finger"] = assignment

        fingers = []
        for finger_index in range(5):
            points = [
                record["local"] for record in records if record["finger"] == finger_index
            ]
            y_values = [point.y for point in points]
            base_y = max(7.5, percentile(y_values, 0.08))
            tip_y = percentile(y_values, 0.97)
            if tip_y - base_y < 2.5:
                raise RuntimeError(f"Finger cluster too short: {side} {finger_index + 1}")
            span = tip_y - base_y
            boundary_points = []
            for fraction in BOUNDARY_FRACTIONS:
                boundary_y = base_y + span * fraction
                nearby = [
                    point
                    for point in points
                    if abs(point.y - boundary_y) <= span * 0.09
                ]
                if not nearby:
                    nearby = sorted(points, key=lambda point: abs(point.y - boundary_y))[:12]
                boundary = mean_point(nearby)
                boundary.y = boundary_y
                boundary_points.append(boundary)
            fingers.append(
                {
                    "index": finger_index,
                    "base_y": base_y,
                    "tip_y": tip_y,
                    "boundary_points": boundary_points,
                    "records": [record for record in records if record["finger"] == finger_index],
                }
            )
        side_data[side] = {
            "hand": hand_name,
            "hand_matrix": hand_bone.matrix_local.copy(),
            "axis": axis,
            "cluster_centers": centers,
            "fingers": fingers,
        }

    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for side, data in side_data.items():
        hand_edit = armature.data.edit_bones[data["hand"]]
        for finger in data["fingers"]:
            number = finger["index"] + 1
            segment_names = []
            parent = hand_edit
            for segment_index, label in enumerate(SEGMENT_LABELS):
                segment_name = f"Ciel_{side}Finger_{number:02d}_{label}"
                segment = armature.data.edit_bones.new(segment_name)
                segment.head = (
                    data["hand_matrix"] @ finger["boundary_points"][segment_index]
                )
                segment.tail = (
                    data["hand_matrix"] @ finger["boundary_points"][segment_index + 1]
                )
                segment.parent = parent
                segment.use_connect = parent != hand_edit
                segment.use_deform = True
                segment_names.append(segment_name)
                parent = segment
            finger["segment_names"] = segment_names
    bpy.ops.object.mode_set(mode="OBJECT")

    report_sides = {}
    for side, data in side_data.items():
        hand_group = mesh.vertex_groups[data["hand"]]
        finger_reports = []
        for finger in data["fingers"]:
            segment_groups = [
                mesh.vertex_groups.new(name=name) for name in finger["segment_names"]
            ]
            transferred = 0.0
            changed = 0
            span = finger["tip_y"] - finger["base_y"]
            for record in finger["records"]:
                progress = smoothstep((record["local"].y - finger["base_y"]) / span)
                amount = record["weight"] * progress * 0.92
                if amount <= 1e-5:
                    continue
                normalized = min(
                    max((record["local"].y - finger["base_y"]) / span, 0.0), 1.0
                )
                vertex = mesh.data.vertices[record["index"]]
                existing_other_influences = sum(
                    1
                    for membership in vertex.groups
                    if membership.group != hand_group.index and membership.weight > 1e-6
                )
                segment_influence_limit = max(0, 3 - existing_other_influences)
                if segment_influence_limit == 0:
                    continue
                nearest = sorted(
                    range(len(SEGMENT_CENTERS)),
                    key=lambda index: abs(normalized - SEGMENT_CENTERS[index]),
                )[:segment_influence_limit]
                raw_shares = [
                    math.exp(
                        -0.5
                        * ((normalized - SEGMENT_CENTERS[index]) / 0.16) ** 2
                    )
                    for index in nearest
                ]
                raw_total = sum(raw_shares)
                shares = tuple(
                    (index, share / raw_total)
                    for index, share in zip(nearest, raw_shares)
                )
                hand_group.add([record["index"]], record["weight"] - amount, "REPLACE")
                for segment_index, share in shares:
                    segment_weight = amount * share
                    if segment_weight > 1e-6:
                        segment_groups[segment_index].add(
                            [record["index"]], segment_weight, "REPLACE"
                        )
                changed += 1
                transferred += amount
            finger_reports.append(
                {
                    "finger": finger["index"] + 1,
                    "segments": finger["segment_names"],
                    "source_vertices": len(finger["records"]),
                    "changed_vertices": changed,
                    "transferred_weight": transferred,
                    "length": span,
                }
            )
        report_sides[side] = {
            "pca_axis": list(data["axis"]),
            "cluster_centers": data["cluster_centers"],
            "fingers": finger_reports,
        }

    scene = bpy.context.scene
    animation_data = armature.animation_data_create()
    keyed_actions = []
    for action in bpy.data.actions:
        pose_spec = ACTION_POSES.get(action.name)
        if not pose_spec:
            continue
        animation_data.action = action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        frames = list(range(start, end + 1, SAMPLE_STEP))
        if frames[-1] != end:
            frames.append(end)
        motion = {
            side: normalized_hand_motion(scene, armature, frames, side)
            for side in ("Left", "Right")
        }
        amplitude = ACTION_RESPONSE[action.name]
        loop = action.name in LOOP_ACTIONS
        for sample, frame in enumerate(frames):
            effective_sample = 0 if loop and sample == len(frames) - 1 else sample
            progress = effective_sample / max(len(frames) - 1, 1)
            for side, data in side_data.items():
                control_degrees = pose_spec.get(side, pose_spec.get("both"))
                degrees_by_segment = expand_control_degrees(control_degrees)
                direction = 1.0 if side == "Left" else -1.0
                for finger in data["fingers"]:
                    scale = FINGER_SCALES[side][finger["index"]]
                    for joint_index, (bone_name, degrees) in enumerate(
                        zip(finger["segment_names"], degrees_by_segment)
                    ):
                        lag = FINGER_LAGS[side][finger["index"]] + joint_index // 2
                        response = lagged_sample(
                            motion[side], effective_sample, lag, loop
                        )
                        phase = (
                            finger["index"] * 0.32
                            + (0.21 if side == "Right" else 0.0)
                            + joint_index * 0.12
                        )
                        organic = 0.025 * math.sin(math.tau * progress + phase)
                        pulse = 1.0 + amplitude * (response - 0.45) + organic
                        pose_bone = armature.pose.bones[bone_name]
                        pose_bone.rotation_mode = "XYZ"
                        pose_bone.rotation_euler.x = math.radians(
                            degrees * scale * direction * pulse
                        )
                        pose_bone.keyframe_insert(
                            data_path="rotation_euler", index=0, frame=frame, group=bone_name
                        )
        channelbag = action.layers[0].strips[0].channelbag(action.slots[0])
        for curve in channelbag.fcurves:
            if not curve.data_path.startswith('pose.bones["Ciel_') or "Finger" not in curve.data_path:
                continue
            for point in curve.keyframe_points:
                point.interpolation = "BEZIER"
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"
            curve.update()
        keyed_actions.append(action.name)

    idle = bpy.data.actions.get("Ciel_Idle_Neutral")
    if idle:
        animation_data.action = idle
        bpy.context.scene.frame_set(int(idle.frame_range[0]))
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps(
            {
                "output": str(output),
                "method": "PCA five-finger clustering with three VRM controls and two interpolated helper deform bones per finger",
                "sample_step_frames": SAMPLE_STEP,
                "interpolation": "BEZIER / AUTO_CLAMPED with hand-speed response and chained per-segment lag",
                "keyed_actions": keyed_actions,
                "sides": report_sides,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
