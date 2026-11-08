"""Import an FBX and report armature/bone/action inventory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    source = Path(args.fbx).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True)
    armatures = []
    for obj in bpy.context.scene.objects:
        if obj.type != "ARMATURE":
            continue
        bones = [bone.name for bone in obj.data.bones]
        armatures.append(
            {
                "object": obj.name,
                "bones": bones,
                "finger_bones": [
                    name
                    for name in bones
                    if any(token in name.lower() for token in ("thumb", "index", "middle", "ring", "pinky", "finger"))
                ],
            }
        )
    report = {
        "source": str(source),
        "armatures": armatures,
        "actions": [
            {"name": action.name, "frame_range": list(action.frame_range)}
            for action in bpy.data.actions
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "armatures": len(armatures)}))


if __name__ == "__main__":
    main()
