"""Layer gait-driven torso, clavicle, and hand mechanics onto Ciel actions.

The source walk/run clips already contain usable limb trajectories. This pass
keeps those trajectories and adds the motion that makes them read as one body:
pelvis weight shift, counter-rotation through the spine, clavicle follow-through,
and a clearly readable soft-fist hand pose.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


GAIT = {
    "Ciel_Walk_InPlace": {
        "hip_yaw": 1.8,
        "spine_yaw": -2.0,
        "chest_yaw": -3.2,
        "lean": 1.5,
        "side_lean": 1.0,
        "clavicle_swing": 2.2,
        "clavicle_lift": 0.8,
        "hip_side": 0.008,
        "hip_bounce": 0.005,
        "finger_scale": 1.22,
    },
    "Ciel_Run_InPlace": {
        "hip_yaw": 3.0,
        "spine_yaw": -3.5,
        "chest_yaw": -5.5,
        "lean": 5.0,
        "side_lean": 1.8,
        "clavicle_swing": 4.0,
        "clavicle_lift": 1.4,
        "hip_side": 0.012,
        "hip_bounce": 0.010,
        "finger_scale": 1.38,
    },
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def channelbag(action: bpy.types.Action):
    return action.layers[0].strips[0].channelbag(action.slots[0])


def curves_for(action: bpy.types.Action, bone_name: str, property_name: str):
    pattern = re.compile(
        rf'^pose\.bones\["{re.escape(bone_name)}"\]\.{re.escape(property_name)}$'
    )
    found = {
        curve.array_index: curve
        for curve in channelbag(action).fcurves
        if pattern.match(curve.data_path)
    }
    return found


def quaternion_at(curves, frame: int) -> Quaternion:
    value = Quaternion(tuple(curves[index].evaluate(frame) for index in range(4)))
    value.normalize()
    return value


def fit_gait_phase(values: list[float]) -> tuple[float, float, float]:
    """Fit c + a*sin(theta) + b*cos(theta) without external dependencies."""
    count = len(values)
    mean = sum(values) / count
    sin_projection = 0.0
    cos_projection = 0.0
    for index, value in enumerate(values):
        theta = math.tau * index / max(count - 1, 1)
        centered = value - mean
        sin_projection += centered * math.sin(theta)
        cos_projection += centered * math.cos(theta)
    scale = 2.0 / max(count - 1, 1)
    a = sin_projection * scale
    b = cos_projection * scale
    amplitude = max(math.hypot(a, b), 1e-8)
    return a / amplitude, b / amplitude, amplitude


def add_parent_space_rotation(
    action: bpy.types.Action,
    bone_name: str,
    frames: list[int],
    rotations: list[Quaternion],
) -> int:
    curves = curves_for(action, bone_name, "rotation_quaternion")
    if len(curves) != 4:
        return 0
    previous = None
    for frame, delta in zip(frames, rotations):
        result = delta @ quaternion_at(curves, frame)
        result.normalize()
        if previous is not None and previous.dot(result) < 0.0:
            result.negate()
        for component in range(4):
            curve = curves[component]
            point = next(
                (point for point in curve.keyframe_points if round(point.co.x) == frame),
                None,
            )
            if point is None:
                curve.keyframe_points.insert(frame, result[component], options={"FAST"})
            else:
                point.co.y = result[component]
            curve.update()
        previous = result.copy()
    return len(frames)


def add_location_offsets(
    action: bpy.types.Action,
    bone_name: str,
    frames: list[int],
    offsets: list[Vector],
) -> int:
    curves = curves_for(action, bone_name, "location")
    if len(curves) != 3:
        return 0
    originals = {
        component: [curves[component].evaluate(frame) for frame in frames]
        for component in range(3)
    }
    for index, (frame, offset) in enumerate(zip(frames, offsets)):
        for component in range(3):
            curve = curves[component]
            value = originals[component][index] + offset[component]
            point = next(
                (point for point in curve.keyframe_points if round(point.co.x) == frame),
                None,
            )
            if point is None:
                curve.keyframe_points.insert(frame, value, options={"FAST"})
            else:
                point.co.y = value
            curve.update()
    return len(frames)


def amplify_finger_pose(action: bpy.types.Action, factor: float) -> int:
    changed = 0
    for curve in channelbag(action).fcurves:
        if not curve.data_path.startswith('pose.bones["Ciel_'):
            continue
        if "Finger" not in curve.data_path or not curve.data_path.endswith("rotation_euler"):
            continue
        if curve.array_index != 0:
            continue
        for point in curve.keyframe_points:
            point.co.y *= factor
            point.interpolation = "BEZIER"
            point.handle_left_type = "AUTO_CLAMPED"
            point.handle_right_type = "AUTO_CLAMPED"
            changed += 1
        curve.update()
    return changed


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    armature = next(obj for obj in scene.objects if obj.type == "ARMATURE")
    animation = armature.animation_data_create()
    report = []

    for action_name, settings in GAIT.items():
        action = bpy.data.actions.get(action_name)
        if action is None:
            continue
        animation.action = action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        frames = list(range(start, end + 1))

        hand_difference = []
        for frame in frames:
            scene.frame_set(frame)
            left = (armature.matrix_world @ armature.pose.bones["mixamorig:LeftHand"].matrix).translation
            right = (armature.matrix_world @ armature.pose.bones["mixamorig:RightHand"].matrix).translation
            hand_difference.append(left.y - right.y)
        sin_weight, cos_weight, source_amplitude = fit_gait_phase(hand_difference)

        swing = []
        quadrature = []
        bounce = []
        for index in range(len(frames)):
            theta = math.tau * index / max(len(frames) - 1, 1)
            value = sin_weight * math.sin(theta) + cos_weight * math.cos(theta)
            derivative = sin_weight * math.cos(theta) - cos_weight * math.sin(theta)
            swing.append(value)
            quadrature.append(derivative)
            bounce.append(2.0 * value * value - 1.0)

        def axis_rot(axis, degrees):
            return Quaternion(axis, math.radians(degrees))

        counts = {}
        counts["hips_rotation"] = add_parent_space_rotation(
            action,
            "mixamorig:Hips",
            frames,
            [
                axis_rot((0, 0, 1), settings["hip_yaw"] * value)
                for value in swing
            ],
        )
        counts["hips_location"] = add_location_offsets(
            action,
            "mixamorig:Hips",
            frames,
            [
                Vector(
                    (
                        settings["hip_side"] * side,
                        0.0,
                        settings["hip_bounce"] * lift,
                    )
                )
                for side, lift in zip(quadrature, bounce)
            ],
        )
        counts["spine"] = add_parent_space_rotation(
            action,
            "mixamorig:Spine",
            frames,
            [
                axis_rot((1, 0, 0), settings["lean"])
                @ axis_rot((0, 0, 1), settings["spine_yaw"] * value)
                @ axis_rot((0, 1, 0), settings["side_lean"] * side)
                for value, side in zip(swing, quadrature)
            ],
        )
        counts["spine2"] = add_parent_space_rotation(
            action,
            "mixamorig:Spine2",
            frames,
            [
                axis_rot((0, 0, 1), settings["chest_yaw"] * value)
                for value in swing
            ],
        )
        for side_name, sign in (("Left", 1.0), ("Right", -1.0)):
            counts[f"{side_name.lower()}_shoulder"] = add_parent_space_rotation(
                action,
                f"mixamorig:{side_name}Shoulder",
                frames,
                [
                    axis_rot((0, 0, 1), sign * settings["clavicle_swing"] * value)
                    @ axis_rot((0, 1, 0), sign * settings["clavicle_lift"] * lift)
                    for value, lift in zip(swing, bounce)
                ],
            )
        counts["finger_keys"] = amplify_finger_pose(action, settings["finger_scale"])
        report.append(
            {
                "action": action_name,
                "frames": [start, end],
                "source_hand_swing_amplitude": source_amplitude,
                "phase_fit": {"sin": sin_weight, "cos": cos_weight},
                "settings": settings,
                "changed": counts,
            }
        )

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
                "reference": "gemini_generated_video_8af36c40.mp4",
                "method": "gait phase inferred from opposing hand motion; pelvis/spine/clavicle counter-motion plus soft-fist amplification",
                "actions": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
