from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

import cv2
from PIL import Image, ImageDraw, ImageFont


def timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main() -> None:
    video_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps
    sample_times = [duration * i / 11 for i in range(12)]

    frames = []
    for index, seconds in enumerate(sample_times):
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((480, 270))
        tile = Image.new("RGB", (500, 310), "white")
        tile.paste(image, ((500 - image.width) // 2, 8))
        draw = ImageDraw.Draw(tile)
        draw.text((12, 285), f"{index + 1:02d}  {timestamp(seconds)}", fill="black")
        frames.append(tile)
        image.save(output_dir / f"sample_{index + 1:02d}_{int(seconds):06d}.jpg", quality=92)

    sheet = Image.new("RGB", (1000, 930), "#dddddd")
    for i, tile in enumerate(frames):
        sheet.paste(tile, ((i % 2) * 500, (i // 2) * 310))
    sheet.save(output_dir / "contact_sheet.jpg", quality=92)

    metadata = {
        "video": str(video_path),
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_seconds": duration,
        "duration": timestamp(duration),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
