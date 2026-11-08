"""Validate deform-weight coverage and per-vertex influence counts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    deform_group_indices = {
        group.index for group in mesh.vertex_groups if group.name in deform
    }
    counts = []
    totals = []
    for vertex in mesh.data.vertices:
        weights = [
            membership.weight
            for membership in vertex.groups
            if membership.group in deform_group_indices and membership.weight > 1e-6
        ]
        counts.append(len(weights))
        totals.append(sum(weights))
    report = {
        "mesh": mesh.name,
        "vertices": len(mesh.data.vertices),
        "deform_bones": len(deform),
        "max_influences": max(counts, default=0),
        "over_four": sum(count > 4 for count in counts),
        "unweighted": sum(total <= 1e-6 for total in totals),
        "weight_sum_min": min(totals, default=0.0),
        "weight_sum_max": max(totals, default=0.0),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
