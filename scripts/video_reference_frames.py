"""Extract evenly spaced review frames from a local reference video with Blender."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--scale-percent", type=int, default=50)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    video = Path(args.video).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.samples < 2:
        raise ValueError("At least two samples are required")

    scene = bpy.context.scene
    sequence_editor = scene.sequence_editor_create()
    for strip in list(sequence_editor.strips):
        sequence_editor.strips.remove(strip)
    strip = sequence_editor.strips.new_movie(
        "Reference", str(video), channel=1, frame_start=1
    )
    scene.frame_start = strip.frame_final_start
    scene.frame_end = strip.frame_final_end - 1
    scene.render.resolution_x = strip.elements[0].orig_width
    scene.render.resolution_y = strip.elements[0].orig_height
    scene.render.resolution_percentage = args.scale_percent
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.use_sequencer = True

    frames = [
        round(
            scene.frame_start
            + (scene.frame_end - scene.frame_start) * index / (args.samples - 1)
        )
        for index in range(args.samples)
    ]
    outputs = []
    for index, frame in enumerate(frames):
        scene.frame_set(frame)
        destination = output_dir / f"reference_{index:02d}_frame_{frame:04d}.png"
        scene.render.filepath = str(destination)
        bpy.ops.render.render(write_still=True)
        outputs.append({"frame": frame, "path": str(destination)})

    report = {
        "video": str(video),
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "samples": outputs,
    }
    report_path = output_dir / "reference_frames.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "frames": len(outputs)}))


if __name__ == "__main__":
    main()
