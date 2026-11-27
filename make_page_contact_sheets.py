from pathlib import Path
import math
import re
import sys

from PIL import Image, ImageDraw

source = Path(sys.argv[1])
output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
pages = sorted(
    source.glob("page-*.png"),
    key=lambda path: int(re.search(r"(\d+)", path.stem).group(1)),
)
tiles = []
for index, page in enumerate(pages, 1):
    image = Image.open(page).convert("RGB")
    image.thumbnail((510, 660))
    tile = Image.new("RGB", (530, 700), "white")
    tile.paste(image, ((530 - image.width) // 2, 5))
    ImageDraw.Draw(tile).text((12, 675), f"Page {index}", fill="black")
    tiles.append(tile)

per_sheet = 6
for sheet_index in range(math.ceil(len(tiles) / per_sheet)):
    subset = tiles[sheet_index * per_sheet:(sheet_index + 1) * per_sheet]
    sheet = Image.new("RGB", (1060, 2100), "#dddddd")
    for index, tile in enumerate(subset):
        sheet.paste(tile, ((index % 2) * 530, (index // 2) * 700))
    sheet.save(output / f"pages_{sheet_index + 1:02d}.jpg", quality=92)

print(f"Created {math.ceil(len(tiles) / per_sheet)} sheets for {len(tiles)} pages")
