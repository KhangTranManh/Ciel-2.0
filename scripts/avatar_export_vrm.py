"""Auto-map a Mixamo-style avatar to VRM 1.0 and export a test artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


FINGER_MAPPING = {
    "left_thumb_metacarpal": "Ciel_LeftFinger_05_Proximal",
    "left_thumb_proximal": "Ciel_LeftFinger_05_Intermediate",
    "left_thumb_distal": "Ciel_LeftFinger_05_Distal",
    "left_index_proximal": "Ciel_LeftFinger_04_Proximal",
    "left_index_intermediate": "Ciel_LeftFinger_04_Intermediate",
    "left_index_distal": "Ciel_LeftFinger_04_Distal",
    "left_middle_proximal": "Ciel_LeftFinger_03_Proximal",
    "left_middle_intermediate": "Ciel_LeftFinger_03_Intermediate",
    "left_middle_distal": "Ciel_LeftFinger_03_Distal",
    "left_ring_proximal": "Ciel_LeftFinger_02_Proximal",
    "left_ring_intermediate": "Ciel_LeftFinger_02_Intermediate",
    "left_ring_distal": "Ciel_LeftFinger_02_Distal",
    "left_little_proximal": "Ciel_LeftFinger_01_Proximal",
    "left_little_intermediate": "Ciel_LeftFinger_01_Intermediate",
    "left_little_distal": "Ciel_LeftFinger_01_Distal",
    "right_thumb_metacarpal": "Ciel_RightFinger_01_Proximal",
    "right_thumb_proximal": "Ciel_RightFinger_01_Intermediate",
    "right_thumb_distal": "Ciel_RightFinger_01_Distal",
    "right_index_proximal": "Ciel_RightFinger_02_Proximal",
    "right_index_intermediate": "Ciel_RightFinger_02_Intermediate",
    "right_index_distal": "Ciel_RightFinger_02_Distal",
    "right_middle_proximal": "Ciel_RightFinger_03_Proximal",
    "right_middle_intermediate": "Ciel_RightFinger_03_Intermediate",
    "right_middle_distal": "Ciel_RightFinger_03_Distal",
    "right_ring_proximal": "Ciel_RightFinger_04_Proximal",
    "right_ring_intermediate": "Ciel_RightFinger_04_Intermediate",
    "right_ring_distal": "Ciel_RightFinger_04_Distal",
    "right_little_proximal": "Ciel_RightFinger_05_Proximal",
    "right_little_intermediate": "Ciel_RightFinger_05_Intermediate",
    "right_little_distal": "Ciel_RightFinger_05_Distal",
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend-output", required=True)
    parser.add_argument("--vrm-output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def human_bone_mapping(armature: bpy.types.Object) -> dict[str, str]:
    human_bones = armature.data.vrm_addon_extension.vrm1.humanoid.human_bones
    mapping = {}
    for prop in human_bones.bl_rna.properties:
        if prop.identifier in {"rna_type", "name"}:
            continue
        value = getattr(human_bones, prop.identifier, None)
        node = getattr(value, "node", None)
        bone_name = getattr(node, "bone_name", "") if node else ""
        if bone_name:
            mapping[prop.identifier] = bone_name
    return mapping


def main() -> None:
    args = parse_args()
    blend_output = Path(args.blend_output).resolve()
    vrm_output = Path(args.vrm_output).resolve()
    report_path = Path(args.report).resolve()
    for path in (blend_output, vrm_output, report_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"Expected one armature, found {len(armatures)}")
    armature = armatures[0]
    extension = armature.data.vrm_addon_extension
    extension.spec_version = "1.0"

    result = bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(
        armature_object_name=armature.name
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"VRM humanoid auto assignment failed: {result}")

    human_bones = extension.vrm1.humanoid.human_bones
    finger_mapping_applied = {}
    for human_name, bone_name in FINGER_MAPPING.items():
        human_bone = getattr(human_bones, human_name, None)
        if human_bone is None or bone_name not in armature.data.bones:
            continue
        human_bone.node.bone_name = bone_name
        finger_mapping_applied[human_name] = bone_name

    meta = extension.vrm1.meta
    meta.vrm_name = "Ciel 2.0"
    meta.version = "0.5-three-joint-finger-runtime"
    if not meta.authors:
        bpy.ops.vrm.add_vrm1_meta_author(armature_object_name=armature.name)
    if meta.authors:
        meta.authors[0].value = "Ciel 2.0 / Meshy AI source"

    mapping = human_bone_mapping(armature)
    required = {
        "hips",
        "spine",
        "head",
        "left_upper_arm",
        "left_lower_arm",
        "left_hand",
        "right_upper_arm",
        "right_lower_arm",
        "right_hand",
        "left_upper_leg",
        "left_lower_leg",
        "left_foot",
        "right_upper_leg",
        "right_lower_leg",
        "right_foot",
    }
    missing_required = sorted(required - set(mapping))
    if missing_required:
        raise RuntimeError(f"Missing required VRM bones: {missing_required}")

    bpy.ops.wm.save_as_mainfile(filepath=str(blend_output))
    export_result = bpy.ops.export_scene.vrm(
        filepath=str(vrm_output),
        armature_object_name=armature.name,
        export_invisibles=False,
        export_only_selections=False,
        export_gltf_animations=False,
        ignore_warning=False,
    )
    if "FINISHED" not in export_result:
        raise RuntimeError(f"VRM export failed: {export_result}")

    report = {
        "blend": str(blend_output),
        "vrm": str(vrm_output),
        "vrm_size_bytes": vrm_output.stat().st_size,
        "mapped_bones": mapping,
        "finger_mapping_applied": finger_mapping_applied,
        "missing_optional_finger_bones": sorted(
            name
            for name in (
                "left_thumb_metacarpal",
                "left_index_proximal",
                "left_middle_proximal",
                "left_ring_proximal",
                "left_little_proximal",
                "right_thumb_metacarpal",
                "right_index_proximal",
                "right_middle_proximal",
                "right_ring_proximal",
                "right_little_proximal",
            )
            if name not in mapping
        ),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"blend": str(blend_output), "vrm": str(vrm_output)}))


if __name__ == "__main__":
    main()
