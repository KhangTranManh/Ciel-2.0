"""Normalize avatar action names and remove horizontal root drift safely."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


ACTION_NAMES = {
    "Armature|Idle_11|BaseLayer": "Ciel_Idle_Neutral",
    "Armature|Idle_15|BaseLayer": "Ciel_Wave",
    "Armature|Walking|BaseLayer": "Ciel_Walk_InPlace",
    "Armature|Running|BaseLayer": "Ciel_Run_InPlace",
    "Armature|Talk_with_Hands_Open|BaseLayer": "Ciel_Talk_Open",
    "Armature|Talk_with_Right_Hand_Open|BaseLayer": "Ciel_Talk_Right",
    "Armature|Shrug|BaseLayer": "Ciel_Shrug",
}
ROOT_ACTIONS = {"Ciel_Walk_InPlace", "Ciel_Run_InPlace"}
HIPS_LOCATION = 'pose.bones["mixamorig:Hips"].location'


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def channelbag(action: bpy.types.Action):
    return action.layers[0].strips[0].channelbag(action.slots[0])


def remove_horizontal_root_drift(action: bpy.types.Action) -> dict[str, object]:
    curves = {
        curve.array_index: curve
        for curve in channelbag(action).fcurves
        if curve.data_path == HIPS_LOCATION
    }
    result = {"before": {}, "after": {}}
    for axis, label in ((0, "x"), (1, "y")):
        curve = curves.get(axis)
        if curve is None or len(curve.keyframe_points) < 2:
            continue
        points = curve.keyframe_points
        first_frame = points[0].co.x
        last_frame = points[-1].co.x
        first_value = points[0].co.y
        delta = points[-1].co.y - first_value
        result["before"][label] = delta
        duration = max(last_frame - first_frame, 1.0)
        for point in points:
            progress = (point.co.x - first_frame) / duration
            point.co.y -= delta * progress
        curve.update()
        result["after"][label] = points[-1].co.y - points[0].co.y
    return result


def loop_seam_metrics(action: bpy.types.Action) -> dict[str, float]:
    differences = []
    for curve in channelbag(action).fcurves:
        points = curve.keyframe_points
        if len(points) >= 2:
            differences.append(abs(points[-1].co.y - points[0].co.y))
    return {
        "max_raw_curve_delta": max(differences, default=0.0),
        "mean_raw_curve_delta": sum(differences) / len(differences) if differences else 0.0,
    }


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = []
    for action in list(bpy.data.actions):
        old_name = action.name
        action.name = ACTION_NAMES.get(old_name, old_name)
        entry = {
            "old_name": old_name,
            "new_name": action.name,
            "frame_range": list(action.frame_range),
        }
        if action.name in ROOT_ACTIONS:
            entry["root_drift"] = remove_horizontal_root_drift(action)
        entry["loop_seam"] = loop_seam_metrics(action)
        report.append(entry)

    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report_path.write_text(
        json.dumps({"output": str(output), "actions": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path)}))


if __name__ == "__main__":
    main()
