"""Magnify three actual StandRig trial renders for visual review."""

from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent / "modest-mouth-v3" / "standrig-trial"
sheet = Image.new("RGB", (900, 240), "white")
draw = ImageDraw.Draw(sheet)
for index, value in enumerate(("0", "0.5", "1")):
    image = Image.open(root / f"mouth-{value}.png").convert("RGBA")
    crop = image.crop((550, 280, 610, 320)).resize((300, 200), Image.Resampling.NEAREST)
    background = Image.new("RGBA", crop.size, (235, 235, 235, 255))
    background.alpha_composite(crop)
    sheet.paste(background.convert("RGB"), (index * 300, 40))
    draw.text((index * 300 + 8, 10), f"ParamMouthOpen = {value}", fill="black")
sheet.save(root / "mouth-closeup-sheet.png")
