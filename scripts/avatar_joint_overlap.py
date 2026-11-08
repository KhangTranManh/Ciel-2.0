"""Add subtle parent-to-child rotational overlap to forearms and wrists."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion


OVERLAP = {
    "mixamorig:LeftForeArm": (1, 0.16),
    "mixamorig:LeftHand": (3, 0.38),
    "mixamorig:RightForeArm": (1, 0.16),
    "mixamorig:RightHand": (3, 0.38),
}
LOOP_ACTIONS = {"Ciel_Idle_Neutral", "Ciel_Walk_InPlace", "Ciel_Run_InPlace"}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def channelbag(action: bpy.types.Action):
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
    values = []
    previous = None
    for index in range(len(curves[0].keyframe_points)):
        value = Quaternion(
            tuple(curves[component].keyframe_points[index].co.y for component in range(4))
        )
        value.normalize()
        if previous is not None and previous.dot(value) < 0.0:
            value.negate()
        values.append(value)
        previous = value.copy()
    return values


def add_overlap(
    action: bpy.types.Action, bone_name: str, delay: int, strength: float
) -> int:
    curves = rotation_curves(action, bone_name)
    if not curves:
        return 0
    originals = continuous_quaternions(curves)
    loop = action.name in LOOP_ACTIONS
    outputs = []
    for index, original in enumerate(originals):
        delayed_index = index - delay
        if loop:
            delayed_index %= len(originals)
        else:
            delayed_index = max(delayed_index, 0)
        outputs.append(original.slerp(originals[delayed_index], strength))
    if loop and len(outputs) > 1:
        outputs[-1] = outputs[0].copy()
    for index, value in enumerate(outputs):
        for component, curve in enumerate(curves):
            curve.keyframe_points[index].co.y = value[component]
            curve.keyframe_points[index].interpolation = "LINEAR"
            curve.update()
    return len(outputs)


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = []
    for action in bpy.data.actions:
        entry = {"action": action.name, "bones": {}}
        for bone_name, (delay, strength) in OVERLAP.items():
            keys = add_overlap(action, bone_name, delay, strength)
            entry["bones"][bone_name] = {
                "keys": keys,
                "delay_frames": delay,
                "strength": strength,
            }
        report.append(entry)

    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps(
            {
                "output": str(output),
                "method": "small quaternion delay blended down the forearm-to-wrist chain",
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
