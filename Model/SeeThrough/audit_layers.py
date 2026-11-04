from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
LAYERS = ROOT / "ciel_upper_body_reference"
AUDIT = ROOT / "audit"
THUMB = 300
LABEL_H = 44
COLS = 4


def checkerboard(size: tuple[int, int], block: int = 20) -> Image.Image:
    result = Image.new("RGBA", size, (232, 232, 232, 255))
    draw = ImageDraw.Draw(result)
    for y in range(0, size[1], block):
        for x in range(0, size[0], block):
            if (x // block + y // block) % 2:
                draw.rectangle((x, y, x + block - 1, y + block - 1), fill=(190, 190, 190, 255))
    return result


def tile(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    image.thumbnail((THUMB, THUMB), Image.Resampling.LANCZOS)
    canvas = checkerboard((THUMB, THUMB))
    x = (THUMB - image.width) // 2
    y = (THUMB - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    result = Image.new("RGB", (THUMB, THUMB + LABEL_H), "white")
    result.paste(canvas.convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(result)
    draw.text((8, THUMB + 8), path.stem, fill="black", font=ImageFont.load_default(size=18))
    return result


def contact_sheet(paths: list[Path], destination: Path) -> None:
    rows = math.ceil(len(paths) / COLS)
    sheet = Image.new("RGB", (COLS * THUMB, rows * (THUMB + LABEL_H)), (225, 225, 225))
    for index, path in enumerate(paths):
        sheet.paste(tile(path), ((index % COLS) * THUMB, (index // COLS) * (THUMB + LABEL_H)))
    sheet.save(destination, quality=95)


def alpha_stats(path: Path) -> dict[str, object]:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    pixels = image.width * image.height
    visible = pixels - histogram[0]
    opaque = histogram[255]
    return {
        "name": path.name,
        "size": [image.width, image.height],
        "alpha_bbox": alpha.getbbox(),
        "visible_fraction": round(visible / pixels, 6),
        "opaque_fraction": round(opaque / pixels, 6),
        "semi_transparent_fraction": round((visible - opaque) / pixels, 6),
    }


def main() -> None:
    AUDIT.mkdir(exist_ok=True)
    pngs = sorted(LAYERS.glob("*.png"), key=lambda path: path.name.casefold())
    references = [path for path in pngs if path.stem in {"src_img", "src_head", "reconstruction"}]
    depths = [path for path in pngs if path.stem.endswith("_depth")]
    semantic = [path for path in pngs if path not in references and path not in depths]

    midpoint = math.ceil(len(semantic) / 2)
    contact_sheet(semantic[:midpoint], AUDIT / "semantic_layers_1.jpg")
    contact_sheet(semantic[midpoint:], AUDIT / "semantic_layers_2.jpg")
    midpoint = math.ceil(len(depths) / 2)
    contact_sheet(depths[:midpoint], AUDIT / "depth_layers_1.jpg")
    contact_sheet(depths[midpoint:], AUDIT / "depth_layers_2.jpg")
    contact_sheet(references, AUDIT / "references.jpg")

    stats = [alpha_stats(path) for path in semantic]
    (AUDIT / "alpha_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"semantic={len(semantic)} depth={len(depths)} references={len(references)}")
    for item in stats:
        print(
            f"{item['name']}: visible={item['visible_fraction']:.4f} "
            f"opaque={item['opaque_fraction']:.4f} semi={item['semi_transparent_fraction']:.4f} "
            f"bbox={item['alpha_bbox']}"
        )


if __name__ == "__main__":
    main()
