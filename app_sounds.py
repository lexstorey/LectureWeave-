"""Play LectureWeave+'s short interface sounds without blocking the app."""

from __future__ import annotations

from pathlib import Path
import sys

SOUNDS = ("startup", "done", "failed")


def sound_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / "sounds" / f"{name}.wav"


def play(name: str) -> bool:
    """Start a sound and return at once. Returns False if it cannot play."""
    path = sound_path(name)
    if not path.is_file():
        return False
    if sys.platform == "win32":
        try:
            import winsound

            winsound.PlaySound(
                str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            )
            return True
        except (ImportError, RuntimeError):
            return False
    return False
