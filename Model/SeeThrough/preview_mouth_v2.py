"""Render neutral and hypothetical revealed mouth for visual review only."""

from pathlib import Path
from PIL import Image, ImageDraw
from psd_tools import PSDImage

root = Path(__file__).resolve().parent
work = root / "upper-body-v2-assets"
psd = PSDImage.open(root / "ciel_upper_body_v2.psd")
neutral = psd.composite().convert("RGBA")
next(x for x in psd if x.name == "mouth").visible = False
next(x for x in psd if x.name == "mouth-interior-draft").visible = True
revealed = psd.composite().convert("RGBA")
region = (790, 390, 870, 450)
sheet = Image.new("RGB", (640, 270), "white")
for i, image in enumerate((neutral, revealed)):
    crop = image.crop(region).resize((320, 240), Image.Resampling.NEAREST)
    bg = Image.new("RGBA", crop.size, (240, 240, 240, 255))
    bg.alpha_composite(crop)
    sheet.paste(bg.convert("RGB"), (i * 320, 30))
draw = ImageDraw.Draw(sheet)
draw.text((8, 8), "Original neutral — 4x", fill="black")
draw.text((328, 8), "Mouth draft visible — 4x", fill="black")
sheet.save(work / "mouth-comparison-4x.png")
