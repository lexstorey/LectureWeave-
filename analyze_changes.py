from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

import cv2
import numpy as np


def frame_signature(frame: np.ndarray) -> np.ndarray:
    # Ignore the fixed pillarbox bars and tiny compression differences.
    height, width = frame.shape[:2]
    crop = frame[:, int(width * 0.055):int(width * 0.945)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (228, 144), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def main() -> None:
    video = Path(sys.argv[1])
    output = Path(sys.argv[2])
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frames / fps

    previous = None
    scores = []
    sample_step = max(1, round(fps))
    frame_index = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_index % sample_step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            second = round(frame_index / fps)
            sig = frame_signature(frame)
            if previous is not None:
                score = float(np.mean(cv2.absdiff(previous, sig)))
                scores.append({"second": second, "score": round(score, 4)})
            previous = sig
        frame_index += 1

    values = np.array([entry["score"] for entry in scores], dtype=np.float32)
    summary = {
        "samples": len(scores),
        "percentiles": {
            str(p): round(float(np.percentile(values, p)), 4)
            for p in [50, 75, 90, 95, 97, 98, 99, 99.5, 99.8, 99.9]
        },
        "top_changes": sorted(scores, key=lambda item: item["score"], reverse=True)[:100],
    }
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
