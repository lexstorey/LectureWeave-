from pathlib import Path
import json
import math
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

import cv2
from PIL import Image, ImageDraw


def timestamp(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main() -> None:
    video_path = Path(sys.argv[1])
    scores_path = Path(sys.argv[2])
    output_dir = Path(sys.argv[3])
    output_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(scores_path.read_text(encoding="utf-8"))
    changes = sorted(
        [x for x in data["top_changes"] if x["score"] >= 2.0],
        key=lambda x: x["second"],
    )
    cap = cv2.VideoCapture(str(video_path))
    tiles = []
    for index, change in enumerate(changes):
        seconds = change["second"] + 1
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((426, 240))
        tile = Image.new("RGB", (450, 280), "white")
        tile.paste(image, ((450 - image.width) // 2, 6))
        draw = ImageDraw.Draw(tile)
        draw.text(
            (10, 252),
            f"{index + 1:02d} {timestamp(seconds)} score {change['score']:.2f}",
            fill="black",
        )
        tiles.append(tile)

    per_sheet = 12
    for sheet_index in range(math.ceil(len(tiles) / per_sheet)):
        subset = tiles[sheet_index * per_sheet:(sheet_index + 1) * per_sheet]
        sheet = Image.new("RGB", (900, 1680), "#dddddd")
        for i, tile in enumerate(subset):
            sheet.paste(tile, ((i % 2) * 450, (i // 2) * 280))
        sheet.save(output_dir / f"candidates_{sheet_index + 1:02d}.jpg", quality=92)
    print(json.dumps({"candidate_count": len(tiles), "sheets": math.ceil(len(tiles) / per_sheet)}))


if __name__ == "__main__":
    main()
