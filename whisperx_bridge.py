"""Optional bridge to a separately installed WhisperX environment.

WhisperX is deliberately not bundled because PyTorch and diarization models
would make the normal installer extremely large.  Users can point LectureWeave+
at a Python executable where WhisperX is installed.  Alignment remains local;
anonymous speaker labels are added when that environment also has an accepted
pyannote model and ``HF_TOKEN`` is set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable


ProgressCallback = Callable[[str, int, int, str], None]


def default_whisperx_python() -> Path:
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return app_data / "LectureWeavePlus" / "whisperx-env" / "Scripts" / "python.exe"


def inspect_whisperx_environment(python_executable: Path) -> dict[str, Any]:
    """Return versions and GPU readiness without exposing environment secrets."""
    if not python_executable.is_file():
        return {"ready": False, "reason": "Python environment not found"}
    probe = r'''
import json
try:
    import torch, whisperx
    result = {
        "ready": True,
        "torch": torch.__version__,
        "cuda_build": torch.version.cuda,
        "cuda_available": bool(torch.cuda.is_available()),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
    }
except Exception as exc:
    result = {"ready": False, "reason": f"{type(exc).__name__}: {exc}"}
print(json.dumps(result))
'''
    try:
        result = subprocess.run(
            [str(python_executable), "-c", probe],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode == 0 and lines:
            return json.loads(lines[-1])
        detail = (result.stderr or result.stdout or "WhisperX check failed").strip()
        return {"ready": False, "reason": detail[-500:]}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"ready": False, "reason": str(exc)}

HELPER_SOURCE = r'''
import json, os, sys
from pathlib import Path
import torch
import whisperx

request_path, output_path = map(Path, sys.argv[1:3])
request = json.loads(request_path.read_text(encoding="utf-8"))
device = "cuda" if torch.cuda.is_available() else "cpu"
audio = whisperx.load_audio(request["video"])
align_model, metadata = whisperx.load_align_model(language_code="en", device=device)
aligned = whisperx.align(
    request["segments"], align_model, metadata, audio, device,
    return_char_alignments=False,
)
token = os.environ.get("HF_TOKEN", "").strip()
if token:
    diarizer = whisperx.DiarizationPipeline(token=token, device=device)
    turns = diarizer(audio)
    aligned = whisperx.assign_word_speakers(turns, aligned)
output_path.write_text(
    json.dumps(aligned.get("segments", []), ensure_ascii=False), encoding="utf-8"
)
'''


def whisperx_environment_ready(python_executable: Path) -> bool:
    return bool(inspect_whisperx_environment(python_executable).get("ready"))


def enhance_with_whisperx(
    video_path: Path,
    segments: list[dict[str, Any]],
    work_dir: Path,
    python_executable: Path,
    progress_callback: ProgressCallback | None = None,
    hf_token: str | None = None,
) -> list[dict[str, Any]]:
    """Force-align existing text and optionally assign anonymous speakers."""
    helper = work_dir / "whisperx_helper.py"
    request = work_dir / "whisperx_request.json"
    output = work_dir / "whisperx_aligned.json"
    helper.write_text(HELPER_SOURCE, encoding="utf-8")
    request.write_text(
        json.dumps({
            "video": str(video_path),
            "segments": [
                {"start": item["start"], "end": item["end"], "text": item["text"]}
                for item in segments
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    if progress_callback:
        detail = "Aligning words with WhisperX"
        if hf_token:
            detail += " and detecting anonymous speakers"
        progress_callback("whisperx", 0, 0, detail + "…")
    child_environment = os.environ.copy()
    if hf_token:
        child_environment["HF_TOKEN"] = hf_token
    else:
        child_environment.pop("HF_TOKEN", None)
    result = subprocess.run(
        [str(python_executable), str(helper), str(request), str(output)],
        capture_output=True,
        text=True,
        timeout=7200,
        env=child_environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or not output.is_file():
        detail = (result.stderr or result.stdout or "Unknown WhisperX error").strip()
        raise RuntimeError(f"Optional WhisperX alignment failed: {detail[-1200:]}")
    aligned = json.loads(output.read_text(encoding="utf-8"))
    if progress_callback:
        progress_callback("whisperx", 1, 1, "WhisperX word alignment complete.")
    return aligned

