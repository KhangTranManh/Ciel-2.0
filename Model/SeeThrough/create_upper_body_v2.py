"""Prepare a hidden mouth draft while preserving the source PSD."""

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "ciel_upper_body_reference_repaired.psd"
OUTPUT = ROOT / "ciel_upper_body_v2.psd"
WORK = ROOT / "upper-body-v2-assets"
GENERATED = Path(r"C:\Users\khang\AppData\Roaming\orca\codex-accounts\cdc2a6b5-21ab-4b96-89a8-5de4ee7a70cc\home\generated_images\01a1a454-a8eb-7e72-ada5-8574c1444279\exec-2cd36809-c61d-450d-bbe5-535d85ac96a7.png")
EXPECTED = "f19e8c7d2187913de7cf1633d1a88ff7d6153d09ea8a1322527f111e3db53279"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if digest(SOURCE) != EXPECTED:
        raise RuntimeError("Source SHA-256 mismatch; no output written")
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite {OUTPUT}")
    WORK.mkdir(exist_ok=True)
    psd = PSDImage.open(SOURCE)
    baseline = psd.composite().convert("RGBA")
    original_layers = [(layer.name, layer.bbox, layer.visible, hashlib.sha256(layer.topil().tobytes()).hexdigest()) for layer in psd]

    # The generated source is a conceptual mouth, not a reconstruction of
    # original pixels. Crop its central shape, retaining antialiased edges.
    generated = Image.open(GENERATED).convert("RGBA")
    strong_bbox = generated.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
    if not strong_bbox:
        raise RuntimeError("Generated mouth has no significant alpha")
    left, top, right, bottom = strong_bbox
    crop = generated.crop((max(0, left - 3), max(0, top - 3), min(generated.width, right + 3), min(generated.height, bottom + 3)))
    draft = crop.resize((28, 16), Image.Resampling.LANCZOS)
    x, y = 816, 412  # Approximate alignment with the source's 21x11 mouth.
    full = Image.new("RGBA", psd.size, (0, 0, 0, 0))
    full.alpha_composite(draft, (x, y))
    draft_path = WORK / "mouth-interior-draft.png"
    full.save(draft_path)

    face = next(layer for layer in psd if layer.name == "face-clean")
    new_layer = PixelLayer.frompil(draft, psd, name="mouth-interior-draft", left=x, top=y)
    new_layer.visible = False
    psd.insert(psd.index(face) + 1, new_layer)
    psd.save(OUTPUT)

    reopened = PSDImage.open(OUTPUT)
    after = reopened.composite().convert("RGBA")
    diff = np.abs(np.asarray(baseline, dtype=np.int16) - np.asarray(after, dtype=np.int16))
    original_after = [(layer.name, layer.bbox, layer.visible, hashlib.sha256(layer.topil().tobytes()).hexdigest()) for layer in reopened if layer.name != "mouth-interior-draft"]
    same_layers = original_layers == original_after
    ImageChops.difference(baseline, after).save(WORK / "neutral-diff.png")
    baseline.save(WORK / "neutral-original.png")
    after.save(WORK / "neutral-v2.png")
    result = {
        "source_sha256": EXPECTED,
        "v2_sha256": digest(OUTPUT),
        "original_layers_unchanged_by_pixel_hash_bbox_visibility_and_order": same_layers,
        "neutral_diff_max_rgba": int(diff.max()),
        "neutral_diff_mean_rgba": float(diff.mean()),
        "neutral_diff_changed_pixels": int(np.any(diff != 0, axis=2).sum()),
        "new_layers": [{"name": "mouth-interior-draft", "bbox": [x, y, x + 28, y + 16], "z_order": "above face-clean and below mouth", "visible_at_neutral": False, "purpose": "Optional draft mouth interior", "method": "AI generated; cropped and scaled to canvas", "confidence": "low", "issues": ["Teeth, cavity and tongue are inferred, not visible in source", "Position and scale are approximate", "Human artist review and rigging visibility controls required", "Not an approved animation-ready asset"]}],
        "remaining": ["Eyelids and blink shapes need artwork", "Hidden overlap coverage requires inspection and painting", "Combined arm and accessories remain unsplit"],
    }
    (WORK / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not same_layers or diff.max() > 0:
        raise RuntimeError("Neutral or original-layer verification failed; do not import v2")


if __name__ == "__main__":
    main()
