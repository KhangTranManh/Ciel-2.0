"""Add an optional modest mouth layer while preserving the v2 document."""

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "ciel_upper_body_v2.psd"
OUTPUT = ROOT / "ciel_upper_body_v3.psd"
WORK = ROOT / "modest-mouth-v3"
IMAGE = Path(r"C:\Users\khang\AppData\Roaming\orca\codex-accounts\cdc2a6b5-21ab-4b96-89a8-5de4ee7a70cc\home\generated_images\01a1a454-a8eb-7e72-ada5-8574c1444279\exec-b4ac3327-c0ee-4221-aded-a6f8b4c0da53.png")
EXPECTED = "138d37981c90270ad36f7d78275a4962b92ca4f44a08eeeb5261682a3982ed91"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def layer_fingerprint(layer):
    return (layer.name, layer.bbox, layer.visible, hashlib.sha256(layer.topil().tobytes()).hexdigest())


def main():
    if sha(SOURCE) != EXPECTED:
        raise RuntimeError("v2 source has changed; no output written")
    if OUTPUT.exists() or WORK.exists():
        raise FileExistsError("Output already exists; refusing to overwrite")
    WORK.mkdir()
    psd = PSDImage.open(SOURCE)
    original = psd.composite().convert("RGBA")
    fingerprints = [layer_fingerprint(layer) for layer in psd]
    image = Image.open(IMAGE).convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.point(lambda a: 255 if a > 16 else 0).getbbox()
    if bbox is None:
        raise ValueError("Mouth image contains no usable pixels")
    l, t, r, b = bbox
    crop = image.crop((max(l - 2, 0), max(t - 2, 0), min(r + 2, image.width), min(b + 2, image.height)))
    mouth = crop.resize((24, 11), Image.Resampling.LANCZOS)
    x, y = 819, 415
    asset = Image.new("RGBA", psd.size, (0, 0, 0, 0))
    asset.alpha_composite(mouth, (x, y))
    asset.save(WORK / "mouth-modest-open.png")

    face = next(layer for layer in psd if layer.name == "face-clean")
    layer = PixelLayer.frompil(mouth, psd, name="mouth-modest-open", left=x, top=y)
    layer.visible = False
    psd.insert(psd.index(face) + 1, layer)
    psd.save(OUTPUT)

    result = PSDImage.open(OUTPUT)
    after = result.composite().convert("RGBA")
    data = np.abs(np.asarray(original, dtype=np.int16) - np.asarray(after, dtype=np.int16))
    original_after = [layer_fingerprint(layer) for layer in result if layer.name != "mouth-modest-open"]
    if fingerprints != original_after or data.max() != 0:
        raise RuntimeError("Verification failed; do not use the saved PSD")
    ImageChops.difference(original, after).save(WORK / "neutral-diff.png")
    after.save(WORK / "neutral.png")

    next(layer for layer in result if layer.name == "mouth").visible = False
    next(layer for layer in result if layer.name == "mouth-modest-open").visible = True
    revealed = result.composite().convert("RGBA")
    revealed.save(WORK / "mouth-revealed.png")
    box = (790, 390, 870, 450)
    sheet = Image.new("RGB", (640, 270), "white")
    draw = ImageDraw.Draw(sheet)
    for idx, (label, picture) in enumerate((("Original neutral - 4x", original), ("Modest mouth trial - 4x", revealed))):
        tile = picture.crop(box).resize((320, 240), Image.Resampling.NEAREST)
        bg = Image.new("RGBA", tile.size, (238, 238, 238, 255))
        bg.alpha_composite(tile)
        sheet.paste(bg.convert("RGB"), (idx * 320, 30))
        draw.text((idx * 320 + 8, 8), label, fill="black")
    sheet.save(WORK / "comparison-4x.png")
    manifest = {
        "source_v2_sha256": EXPECTED,
        "output_v3_sha256": sha(OUTPUT),
        "layer": {"name": "mouth-modest-open", "bbox": [x, y, x + 24, y + 11], "visible_at_neutral": False, "position": "above face-clean, below original mouth", "method": "AI-generated mouth from user reference, cropped and scaled", "note": "Position is approximate; no StandRig motion bindings are included."},
        "neutral_diff": {"max": int(data.max()), "mean": float(data.mean()), "changed_pixels": int(np.any(data != 0, axis=2).sum())},
        "existing_layer_pixel_hashes_order_bbox_visibility_unchanged": True,
    }
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
