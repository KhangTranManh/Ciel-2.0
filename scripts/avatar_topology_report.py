"""Report disconnected mesh components to guide safe avatar rig extensions."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    mesh_object = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    mesh = mesh_object.data
    adjacency = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].append(b)
        adjacency[b].append(a)

    unseen = set(range(len(mesh.vertices)))
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

        points = [mesh_object.matrix_world @ mesh.vertices[index].co for index in indices]
        minimum = [min(point[axis] for point in points) for axis in range(3)]
        maximum = [max(point[axis] for point in points) for axis in range(3)]
        center = [sum(point[axis] for point in points) / len(points) for axis in range(3)]
        components.append(
            {
                "vertices": len(indices),
                "minimum": minimum,
                "maximum": maximum,
                "center": center,
            }
        )

    components.sort(key=lambda item: item["vertices"], reverse=True)
    output.write_text(
        json.dumps(
            {
                "mesh": mesh_object.name,
                "vertices": len(mesh.vertices),
                "component_count": len(components),
                "components": components,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "components": len(components)}))


if __name__ == "__main__":
    main()
