"""Build a non-destructive repaired copy of the See-through PSD."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer


ROOT = Path(__file__).resolve().parent
SOURCE_PSD = ROOT / "ciel_upper_body_reference.psd"
FACE_CANDIDATE = ROOT / "candidates" / "face_clean_ai_v1_aligned.png"
CLOTHING_CANDIDATE = (
    ROOT / "seed7" / "ciel_upper_body_reference" / "handwear.png"
)
OUTPUT_PSD = ROOT / "ciel_upper_body_reference_repaired.psd"
OUTPUT_PREVIEW = ROOT / "candidates" / "repaired_face_composite.png"
OUTPUT_FACE = ROOT / "candidates" / "face_clean_final.png"


def find_layer(psd: PSDImage, name: str):
    for layer in psd:
        if layer.name == name:
            return layer
    raise KeyError(f"Layer not found: {name}")


def find_internal_alpha_holes(alpha: Image.Image) -> Image.Image:
    """Return transparent islands that do not connect to the crop boundary."""
    binary = alpha.point(lambda value: 255 if value else 0)
    outside = Image.new("L", binary.size, 0)
    outside.paste(binary)
    # The silhouette can touch a crop edge and split the outside into several
    # regions, so seed the flood fill from every transparent border pixel.
    border = (
        [(x, 0) for x in range(alpha.width)]
        + [(x, alpha.height - 1) for x in range(alpha.width)]
        + [(0, y) for y in range(alpha.height)]
        + [(alpha.width - 1, y) for y in range(alpha.height)]
    )
    for point in border:
        if outside.getpixel(point) == 0:
            ImageDraw.floodfill(outside, point, 128)

    # Transparent pixels not reached from the crop boundary are internal holes.
    pixels = outside.load()
    holes = Image.new("L", alpha.size, 0)
    hole_pixels = holes.load()
    for y in range(alpha.height):
        for x in range(alpha.width):
            if pixels[x, y] == 0:
                hole_pixels[x, y] = 255
    return holes


def main() -> None:
    psd = PSDImage.open(SOURCE_PSD)
    old_face = find_layer(psd, "face")
    face_index = psd.index(old_face)

    # Preserve the exact mask and bounds of the original PSD face layer. Only
    # the contaminated color pixels are replaced by the cleaned candidate.
    old_face_image = old_face.topil().convert("RGBA")
    left, top, right, bottom = old_face.bbox
    candidate = Image.open(FACE_CANDIDATE).convert("RGBA")
    clean_face = candidate.crop((left, top, right, bottom))
    old_alpha = old_face_image.getchannel("A")
    holes = find_internal_alpha_holes(old_alpha)

    # The AI-cleaned candidate retained a faint color boundary around the old
    # transparent islands. Replace only that neighborhood with a heavily
    # smoothed sample of the surrounding skin and feather its edge.
    repair_mask = holes.filter(ImageFilter.MaxFilter(31)).filter(
        ImageFilter.GaussianBlur(10)
    )
    # Sample an unaffected central forehead area. A flat skin fill is safer
    # than blurring here because transparent source pixels contain black RGB.
    sample = np.asarray(clean_face.convert("RGB"))[45:145, 75:215]
    skin_color = tuple(int(value) for value in np.median(sample, axis=(0, 1)))
    smooth_skin = Image.new("RGBA", clean_face.size, (*skin_color, 255))
    clean_face = Image.composite(smooth_skin, clean_face, repair_mask)

    repaired_alpha = old_alpha.copy()
    alpha_repair_mask = holes.filter(ImageFilter.MaxFilter(15))
    repaired_alpha = Image.composite(
        Image.new("L", old_alpha.size, 255), repaired_alpha, alpha_repair_mask
    )
    clean_face.putalpha(repaired_alpha)

    full_face = Image.new("RGBA", psd.size, (0, 0, 0, 0))
    full_face.alpha_composite(clean_face, (left, top))
    full_face.save(OUTPUT_FACE)

    replacement = PixelLayer.frompil(
        clean_face,
        psd,
        name="face-clean",
        top=top,
        left=left,
    )
    psd.remove(old_face)
    psd.insert(face_index, replacement)

    # Seed 7 correctly recovered the brown sleeve and the two yellow clothing
    # pieces that the original PSD omitted. Replace only the mislabeled sleeve.
    old_sleeve = find_layer(psd, "legwear")
    sleeve_index = psd.index(old_sleeve)
    clothing = Image.open(CLOTHING_CANDIDATE).convert("RGBA")
    clothing_box = clothing.getchannel("A").getbbox()
    if clothing_box is None:
        raise ValueError("Selected clothing candidate is empty")
    clothing_layer = PixelLayer.frompil(
        clothing.crop(clothing_box),
        psd,
        name="arm-and-yellow-accessories",
        left=clothing_box[0],
        top=clothing_box[1],
    )
    psd.remove(old_sleeve)
    psd.insert(sleeve_index, clothing_layer)

    # The source generator placed the bangs below the face. They must be above
    # the facial features in normal view while the clean face stays underneath.
    front_hair = find_layer(psd, "front hair")
    psd.remove(front_hair)
    psd.append(front_hair)

    psd.save(OUTPUT_PSD)
    psd.composite().save(OUTPUT_PREVIEW)

    print(f"Saved {OUTPUT_FACE}")
    print(f"Saved {OUTPUT_PSD}")
    print(f"Saved {OUTPUT_PREVIEW}")


if __name__ == "__main__":
    main()
