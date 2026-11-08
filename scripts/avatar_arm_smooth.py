"""Create a non-destructive Blender checkpoint with subtly smoothed arm motion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion


ARM_STRENGTH = {
    "mixamorig:LeftShoulder": 0.22,
    "mixamorig:LeftArm": 0.30,
    "mixamorig:LeftForeArm": 0.38,
    "mixamorig:LeftHand": 0.42,
    "mixamorig:RightShoulder": 0.22,
    "mixamorig:RightArm": 0.30,
    "mixamorig:RightForeArm": 0.38,
    "mixamorig:RightHand": 0.42,
}
GAUSSIAN_WEIGHTS = {
    2: (1.0, 4.0, 6.0, 4.0, 1.0),
    4: (1.0, 8.0, 28.0, 56.0, 70.0, 56.0, 28.0, 8.0, 1.0),
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--radius", type=int, choices=sorted(GAUSSIAN_WEIGHTS), default=2)
    parser.add_argument("--strength-scale", type=float, default=1.0)
    return parser.parse_args(argv)


def channelbag(action: bpy.types.Action):
    if len(action.slots) != 1 or len(action.layers) != 1 or len(action.layers[0].strips) != 1:
        raise RuntimeError(f"Unsupported layered action layout: {action.name}")
    return action.layers[0].strips[0].channelbag(action.slots[0])


def rotation_curves(action: bpy.types.Action, bone_name: str):
    pattern = re.compile(
        rf'^pose\.bones\["{re.escape(bone_name)}"\]\.rotation_quaternion$'
    )
    curves = {
        curve.array_index: curve
        for curve in channelbag(action).fcurves
        if pattern.match(curve.data_path)
    }
    return [curves[index] for index in range(4)] if len(curves) == 4 else []


def continuous_quaternions(curves) -> list[Quaternion]:
    count = len(curves[0].keyframe_points)
    values = []
    previous = None
    for index in range(count):
        quat = Quaternion(tuple(curves[component].keyframe_points[index].co.y for component in range(4)))
        quat.normalize()
        if previous is not None and previous.dot(quat) < 0.0:
            quat.negate()
        values.append(quat)
        previous = quat.copy()
    return values


def gaussian_quaternion(values: list[Quaternion], index: int, radius: int) -> Quaternion:
    components = [0.0, 0.0, 0.0, 0.0]
    total = 0.0
    for offset, weight in zip(range(-radius, radius + 1), GAUSSIAN_WEIGHTS[radius]):
        sample = values[min(max(index + offset, 0), len(values) - 1)]
        for component in range(4):
            components[component] += sample[component] * weight
        total += weight
    result = Quaternion(tuple(value / total for value in components))
    result.normalize()
    if result.dot(values[index]) < 0.0:
        result.negate()
    return result


def smooth_bone(
    action: bpy.types.Action, bone_name: str, strength: float, radius: int
) -> int:
    curves = rotation_curves(action, bone_name)
    if not curves:
        return 0
    counts = {len(curve.keyframe_points) for curve in curves}
    frames = [[point.co.x for point in curve.keyframe_points] for curve in curves]
    if len(counts) != 1 or any(axis != frames[0] for axis in frames[1:]):
        raise RuntimeError(f"Quaternion curves are not frame-aligned: {action.name} / {bone_name}")

    original = continuous_quaternions(curves)
    output = []
    for index, value in enumerate(original):
        smoothed = gaussian_quaternion(original, index, radius)
        output.append(value.slerp(smoothed, strength))

    for index, quat in enumerate(output):
        for component, curve in enumerate(curves):
            curve.keyframe_points[index].co.y = quat[component]
            curve.keyframe_points[index].interpolation = "LINEAR"
        for curve in curves:
            curve.update()
    return len(output)


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for action in bpy.data.actions:
        action_result = {"action": action.name, "bones": {}}
        for bone_name, base_strength in ARM_STRENGTH.items():
            strength = min(max(base_strength * args.strength_scale, 0.0), 1.0)
            keys = smooth_bone(action, bone_name, strength, args.radius)
            action_result["bones"][bone_name] = {
                "keys": keys,
                "strength": strength,
            }
        results.append(action_result)

    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps(
            {
                "output": str(output),
                "method": f"{args.radius * 2 + 1}-frame Gaussian quaternion smoothing blended per bone",
                "radius": args.radius,
                "strength_scale": args.strength_scale,
                "actions": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
