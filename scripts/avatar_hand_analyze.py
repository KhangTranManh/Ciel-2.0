"""Inspect Meshy hand topology in hand-bone local coordinates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def bounds(points: list[Vector]) -> dict[str, list[float]]:
    return {
        "minimum": [min(point[i] for point in points) for i in range(3)],
        "maximum": [max(point[i] for point in points) for i in range(3)],
    }


def main() -> None:
    args = parse_args()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    result = {}
    mesh_to_armature = armature.matrix_world.inverted() @ mesh.matrix_world

    for side in ("Left", "Right"):
        bone_name = f"mixamorig:{side}Hand"
        group = mesh.vertex_groups.get(bone_name)
        bone = armature.data.bones[bone_name]
        armature_to_bone = bone.matrix_local.inverted()
        selected: dict[int, float] = {}
        local_points: dict[int, Vector] = {}
        for vertex in mesh.data.vertices:
            try:
                weight = group.weight(vertex.index)
            except RuntimeError:
                continue
            if weight < 0.05:
                continue
            selected[vertex.index] = weight
            local_points[vertex.index] = armature_to_bone @ (mesh_to_armature @ vertex.co)

        adjacency = {index: [] for index in selected}
        for edge in mesh.data.edges:
            a, b = edge.vertices
            if a in selected and b in selected:
                adjacency[a].append(b)
                adjacency[b].append(a)

        unseen = set(selected)
        components = []
        while unseen:
            seed = unseen.pop()
            queue = deque([seed])
            indices = [seed]
            while queue:
                current = queue.popleft()
                for neighbor in adjacency[current]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        queue.append(neighbor)
                        indices.append(neighbor)
            points = [local_points[index] for index in indices]
            components.append(
                {
                    "vertices": len(indices),
                    "weight_sum": sum(selected[index] for index in indices),
                    **bounds(points),
                }
            )
        components.sort(key=lambda component: component["vertices"], reverse=True)
        points = list(local_points.values())
        result[side] = {
            "bone_head": list(bone.head_local),
            "bone_tail": list(bone.tail_local),
            "selected_vertices": len(selected),
            "local_bounds": bounds(points),
            "connected_components": len(components),
            "largest_components": components[:30],
        }

    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path)}))


if __name__ == "__main__":
    main()
