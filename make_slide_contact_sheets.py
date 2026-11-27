from pathlib import Path
import json
import math
import sys

from PIL import Image, ImageDraw


def format_time(seconds: float) -> str:
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output_dir = Path(sys.argv[2])
output_dir.mkdir(parents=True, exist_ok=True)
tiles = []
for item in manifest:
    image = Image.open(item["image"]).convert("RGB")
    image.thumbnail((500, 320))
    tile = Image.new("RGB", (520, 360), "white")
    tile.paste(image, ((520 - image.width) // 2, 5))
    draw = ImageDraw.Draw(tile)
    draw.text((10, 335), f"{item['index']:02d} {format_time(item['start'])}", fill="black")
    tiles.append(tile)

per_sheet = 10
for sheet_index in range(math.ceil(len(tiles) / per_sheet)):
    subset = tiles[sheet_index * per_sheet:(sheet_index + 1) * per_sheet]
    sheet = Image.new("RGB", (1040, 1800), "#dddddd")
    for index, tile in enumerate(subset):
        sheet.paste(tile, ((index % 2) * 520, (index // 2) * 360))
    sheet.save(output_dir / f"slides_{sheet_index + 1:02d}.jpg", quality=92)

print(f"Created {math.ceil(len(tiles) / per_sheet)} contact sheets")
