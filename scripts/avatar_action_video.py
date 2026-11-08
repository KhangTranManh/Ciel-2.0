"""Build and render one review video containing all avatar actions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_inspect import add_preview_setup, point_at, world_bounds


ACTION_ORDER = (
    # Canonical runtime names first. The legacy aliases keep this review tool
    # usable against the untouched Meshy import as well.
    ("Ciel_Idle_Neutral", "Idle_11"),
    ("Ciel_Wave", "Idle_15"),
    ("Ciel_Walk_InPlace", "Walking"),
    ("Ciel_Run_InPlace", "Running"),
    ("Ciel_Talk_Open", "Talk_with_Hands_Open"),
    ("Ciel_Talk_Right", "Talk_with_Right_Hand_Open"),
    ("Ciel_Shrug", "Shrug"),
)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--resolution-x", type=int, default=480)
    parser.add_argument("--resolution-y", type=int, default=640)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--actions",
        default="",
        help="Optional comma-separated canonical or legacy action names",
    )
    return parser.parse_args(argv)


def action_key(action: bpy.types.Action) -> str:
    return action.name.replace("Armature|", "").replace("|BaseLayer", "")


def main() -> None:
    args = parse_args()
    destination = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    source_fps = 60
    output_fps = args.fps
    if not 10 <= output_fps <= 60:
        raise ValueError("Preview fps must be between 10 and 60")
    scene.render.fps = output_fps
    armatures = [obj for obj in scene.objects if obj.type == "ARMATURE"]
    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    if len(armatures) != 1 or not meshes:
        raise RuntimeError("Expected one armature and at least one mesh")
    armature = armatures[0]
    armature.data.pose_position = "POSE"
    animation_data = armature.animation_data_create()
    animation_data.action = None
    while animation_data.nla_tracks:
        animation_data.nla_tracks.remove(animation_data.nla_tracks[0])
    track = animation_data.nla_tracks.new()
    track.name = "Ciel_Action_Review"

    available = {action_key(action): action for action in bpy.data.actions}
    requested = {
        name.strip() for name in args.actions.split(",") if name.strip()
    }
    cursor = 1
    gap_frames = output_fps // 2
    manifest = []
    for canonical_name, legacy_name in ACTION_ORDER:
        if requested and canonical_name not in requested and legacy_name not in requested:
            continue
        action = available.get(canonical_name) or available.get(legacy_name)
        if action is None:
            continue
        name = action_key(action)
        source_start = float(action.frame_range[0])
        source_end = float(action.frame_range[1])
        source_frames = source_end - source_start + 1.0
        output_frames = max(1, round(source_frames * output_fps / source_fps))
        strip = track.strips.new(name, cursor, action)
        strip.action_frame_start = source_start
        strip.action_frame_end = source_end
        strip.frame_start = cursor
        strip.frame_end = cursor + output_frames
        manifest.append(
            {
                "action": name,
                "frame_start": cursor,
                "frame_end": cursor + output_frames - 1,
                "time_start_seconds": round((cursor - 1) / output_fps, 3),
                "time_end_seconds": round((cursor + output_frames - 1) / output_fps, 3),
            }
        )
        cursor += output_frames + gap_frames

    scene.frame_start = 1
    scene.frame_end = max(1, cursor - gap_frames - 1)

    minimum, maximum = world_bounds(meshes)
    center = (minimum + maximum) / 2
    size = maximum - minimum
    distance = max(size.length * 1.5, 2.0)
    camera = add_preview_setup(center, size)
    if args.resolution_x < 64 or args.resolution_y < 64:
        raise ValueError("Preview resolution must be at least 64 pixels per axis")
    scene.render.resolution_x = args.resolution_x
    scene.render.resolution_y = args.resolution_y
    scene.render.resolution_percentage = 100
    camera.data.ortho_scale *= 1.12
    camera.location = center + Vector((0.0, -distance, 0.0))
    point_at(camera, center)

    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.filepath = str(destination)

    manifest_path.write_text(
        json.dumps(
            {
                "video": str(destination),
                "fps": output_fps,
                "frame_start": scene.frame_start,
                "frame_end": scene.frame_end,
                "duration_seconds": round(scene.frame_end / output_fps, 3),
                "resolution": [args.resolution_x, args.resolution_y],
                "segments": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    bpy.ops.render.render(animation=True)
    print(json.dumps({"video": str(destination), "manifest": str(manifest_path)}))


if __name__ == "__main__":
    main()
