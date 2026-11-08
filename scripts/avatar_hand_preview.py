"""Render close hand reviews for selected actions and frames."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_inspect import add_preview_setup, point_at, world_bounds


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--actions", default="Ciel_Walk_InPlace,Ciel_Run_InPlace")
    parser.add_argument("--samples", type=int, default=4)
    return parser.parse_args(argv)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_").lower()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.resolution_x = 480
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    armature = next(obj for obj in scene.objects if obj.type == "ARMATURE")
    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    minimum, maximum = world_bounds(meshes)
    size = maximum - minimum
    camera = add_preview_setup((minimum + maximum) / 2, size)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(size.z * 0.16, 0.32)
    animation = armature.animation_data_create()
    requested = [name.strip() for name in args.actions.split(",") if name.strip()]
    report = []

    for action_name in requested:
        action = bpy.data.actions.get(action_name)
        if action is None:
            continue
        animation.action = action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        frames = sorted(
            {
                round(start + (end - start) * index / max(args.samples - 1, 1))
                for index in range(args.samples)
            }
        )
        for frame in frames:
            scene.frame_set(frame)
            for side in ("Left", "Right"):
                hand = armature.pose.bones[f"mixamorig:{side}Hand"]
                center = (armature.matrix_world @ hand.matrix).translation
                for view, offset in {
                    "front": Vector((0.0, -1.0, 0.0)),
                    "side": Vector((-1.0 if side == "Left" else 1.0, 0.0, 0.0)),
                }.items():
                    camera.location = center + offset
                    point_at(camera, center)
                    destination = output_dir / (
                        f"{safe_name(action_name)}_f{frame:04d}_{side.lower()}_{view}.png"
                    )
                    scene.render.filepath = str(destination)
                    bpy.ops.render.render(write_still=True)
                    report.append(
                        {
                            "action": action_name,
                            "frame": frame,
                            "side": side,
                            "view": view,
                            "path": str(destination),
                        }
                    )

    report_path = output_dir / "hand_preview_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "renders": len(report)}))


if __name__ == "__main__":
    main()
