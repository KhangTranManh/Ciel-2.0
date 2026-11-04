"""Save an isolated eye-layer contact sheet for coverage review."""

from pathlib import Path
from PIL import Image, ImageDraw
from psd_tools import PSDImage

root = Path(__file__).resolve().parent
psd = PSDImage.open(root / "ciel_upper_body_reference_repaired.psd")
names = ["eyewhite-r", "irides-r", "eyelash-r", "eyewhite-l", "irides-l", "eyelash-l"]
boxes = [(700, 235, 860, 340)] * 3 + [(870, 295, 975, 395)] * 3
sheet = Image.new("RGB", (640 * 3, 460 * 2), "white")
draw = ImageDraw.Draw(sheet)
for index, (name, box) in enumerate(zip(names, boxes)):
    layer = next(item for item in psd if item.name == name)
    full = Image.new("RGBA", psd.size)
    full.alpha_composite(layer.topil().convert("RGBA"), (layer.left, layer.top))
    crop = full.crop(box).resize(((box[2] - box[0]) * 4, (box[3] - box[1]) * 4), Image.Resampling.NEAREST)
    bg = Image.new("RGBA", crop.size, (220, 220, 220, 255))
    bg.alpha_composite(crop)
    x, y = (index % 3) * 640, (index // 3) * 460
    sheet.paste(bg.convert("RGB"), (x, y + 30))
    draw.text((x + 8, y + 8), name + " (isolated, 4x)", fill="black")
sheet.save(root / "upper-body-v2-assets" / "eyes-isolated-4x.png")
