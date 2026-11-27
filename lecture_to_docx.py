from __future__ import annotations

import argparse
import html
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

import cv2
import numpy as np
from slide_detection import (
    DetectionConfig,
    analysis_frame,
    block_change_ratio,
    build_activity_mask,
    choose_change_threshold,
    hash_distance,
    hsv_histogram_similarity,
    perceptual_hash,
)
from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


# A neutral prompt: subject-specific terms come from the slides and the user's
# glossary as hotwords, so no single unit's vocabulary is forced on every lecture.
DEFAULT_PROMPT = (
    "A university lecture. Names, acronyms, legislation, cases and specialist "
    "terms are spelled correctly, with full punctuation."
)
# Bump when transcription settings change so stale cached transcripts are not reused.
TRANSCRIPTION_SETTINGS_VERSION = "2"

ProgressCallback = Callable[[str, int, int, str], None]
def format_time(seconds: float, include_ms: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if include_ms:
        milliseconds = int(round((seconds - whole) * 1000))
        if milliseconds == 1000:
            whole += 1
            milliseconds = 0
            hours, remainder = divmod(whole, 3600)
            minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_chat_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        hours = "0"
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported chat timestamp: {value}")
    if not all(part.isdigit() for part in (hours, minutes, seconds)):
        raise ValueError(f"Unsupported chat timestamp: {value}")
    if int(seconds) >= 60 or (len(parts) == 3 and int(minutes) >= 60):
        raise ValueError(f"Unsupported chat timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def clean_chat_markup(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ").strip()
    value = re.sub(r"^[-*]\s+", "", value)
    value = re.sub(r"^\*\*(.*?)\*\*$", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_chat_comments(text: str) -> list[dict[str, Any]]:
    """Parse Blackboard copied chat in bulleted Markdown or plain-text blocks."""
    normalized = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    comments: list[dict[str, Any]] = []
    time_pattern = re.compile(r"^\*{0,2}\s*(\d{1,3}:\d{2}(?::\d{2})?)\s*\*{0,2}$")

    timestamps: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = time_pattern.fullmatch(line.strip())
        if match:
            timestamps.append((index, match.group(1)))

    def previous_content_line(before: int) -> int | None:
        for line_index in range(before - 1, -1, -1):
            if lines[line_index].strip():
                return line_index
        return None

    for position, (timestamp_index, timestamp_text) in enumerate(timestamps):
        author_index = previous_content_line(timestamp_index)
        if author_index is None:
            continue
        author = clean_chat_markup(lines[author_index])

        if position + 1 < len(timestamps):
            next_timestamp_index = timestamps[position + 1][0]
            next_author_index = previous_content_line(next_timestamp_index)
            message_end = next_author_index if next_author_index is not None else next_timestamp_index
        else:
            message_end = len(lines)

        message = clean_chat_markup(" ".join(lines[timestamp_index + 1:message_end]))
        if not author or not message:
            continue
        try:
            start = parse_chat_timestamp(timestamp_text)
        except ValueError:
            continue
        comments.append(
            {
                "author": author,
                "timestamp": timestamp_text,
                "start": start,
                "text": message,
            }
        )
    comments.sort(key=lambda item: item["start"])
    return comments


def signature(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    crop = frame[:, int(width * 0.055):int(width * 0.945)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (228, 144), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def signature_difference(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a, b)))


def crop_pillarbox(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    column_mean = np.mean(gray, axis=0)
    candidates = np.flatnonzero(column_mean > 10)
    if candidates.size:
        left = max(0, int(candidates[0]) - 2)
        right = min(frame.shape[1], int(candidates[-1]) + 3)
        if right - left >= frame.shape[1] * 0.6:
            return frame[:, left:right]
    return frame


def extract_frame(cap: cv2.VideoCapture, seconds: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not decode video frame at {format_time(seconds)}")
    return frame


def detect_slides(
    video_path: Path,
    output_dir: Path,
    threshold: float | None = None,
    minimum_duration: float = 2.5,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Detect settled slide appearances with per-video adaptive thresholds."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        raise RuntimeError(f"Could not determine the frame rate of {video_path}")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps
    sample_step = max(1, round(fps))
    config = DetectionConfig()

    # Learn a small moving-region mask from the opening minute.  This rejects
    # webcam tiles, cursors and animated meeting logos without cropping the slide.
    calibration_frames: list[np.ndarray] = []
    for second in range(min(75, max(1, int(duration)))):
        cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        calibration_frames.append(analysis_frame(frame, config))
    activity_mask = build_activity_mask(calibration_frames, config)
    cap.release()

    cap = cv2.VideoCapture(str(video_path))

    samples: list[dict[str, Any]] = []
    previous: np.ndarray | None = None
    frame_index = 0
    candidate_count = 0
    last_reported_percent = -1
    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_index % sample_step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            second = frame_index / fps
            analysed = analysis_frame(frame, config)
            ratio = 0.0 if previous is None else block_change_ratio(
                previous, analysed, config, activity_mask
            )
            samples.append(
                {
                    "second": second,
                    "signature": cv2.cvtColor(analysed, cv2.COLOR_BGR2GRAY),
                    "phash": perceptual_hash(analysed),
                    "histogram_frame": cv2.resize(analysed, (96, 54), interpolation=cv2.INTER_AREA),
                    "ratio": ratio,
                    "brightness": float(np.mean(analysed)),
                }
            )
            previous = analysed
            percent = min(100, int(frame_index * 100 / max(1, frame_count)))
            if progress_callback and percent != last_reported_percent:
                progress_callback(
                    "slide_scan",
                    frame_index,
                    frame_count,
                    f"Scanning slides — {format_time(second)} of {format_time(duration)} "
                    f"({percent}%) · calibrating changes for this recording",
                )
                last_reported_percent = percent
        frame_index += 1
    cap.release()
    ratios = [float(sample["ratio"]) for sample in samples[1:]]
    chosen_threshold, threshold_mode = choose_change_threshold(ratios, config)
    if threshold is not None:
        chosen_threshold = threshold
        threshold_mode = "manual"
    candidate_count = sum(sample["ratio"] >= chosen_threshold for sample in samples[1:])
    if progress_callback:
        progress_callback(
            "slide_scan",
            frame_count,
            frame_count,
            f"Slide scan complete — reviewing {candidate_count} possible changes "
            f"with {threshold_mode} calibration…",
        )

    # A threshold crossing is only accepted after the image settles.  The
    # boundary remains at the beginning of the change, while capture happens
    # inside the stable dwell so transition frames are not exported.
    stable_threshold = max(config.minimum_threshold / 2, chosen_threshold * config.stable_fraction)
    merged: list[dict[str, Any]] = []
    if samples:
        first = samples[0]
        merged.append(
            {
                "start": 0.0,
                "capture_time": float(first["second"]),
                **{key: first[key] for key in ("signature", "phash", "histogram_frame", "brightness")},
            }
        )
    index = 1
    while index < len(samples):
        candidate = samples[index]
        if candidate["ratio"] < chosen_threshold:
            index += 1
            continue
        boundary = float(candidate["second"])
        settled_index: int | None = None
        for lookahead in range(index + 1, min(len(samples), index + 5)):
            if samples[lookahead]["ratio"] <= stable_threshold:
                settled_index = lookahead
                break
        if settled_index is None:
            index += 1
            continue
        settled = samples[settled_index]
        previous_slide = merged[-1]
        votes = (
            signature_difference(previous_slide["signature"], settled["signature"]) >= 1.0,
            hash_distance(previous_slide["phash"], settled["phash"]) > config.duplicate_hash_distance,
            hsv_histogram_similarity(
                previous_slide["histogram_frame"], settled["histogram_frame"]
            ) < 0.985,
        )
        if sum(bool(vote) for vote in votes) >= 2 and settled["brightness"] >= 15:
            if boundary - float(previous_slide["start"]) >= minimum_duration:
                merged.append(
                    {
                        "start": boundary,
                        "capture_time": float(settled["second"]),
                        **{
                            key: settled[key]
                            for key in ("signature", "phash", "histogram_frame", "brightness")
                        },
                    }
                )
        index = settled_index + 1

    if not merged:
        raise RuntimeError("No stable slides were detected")
    for index, item in enumerate(merged):
        item["index"] = index + 1
        item["end"] = merged[index + 1]["start"] if index + 1 < len(merged) else duration
    merged[0]["alignment_start"] = 0.0
    for item in merged[1:]:
        item["alignment_start"] = item["start"]

    # Assign a canonical identity globally.  A lecturer returning to an earlier
    # slide remains a new timeline appearance but reuses the original image.
    canonicals: list[dict[str, Any]] = []
    for item in merged:
        canonical_index: int | None = None
        for prior in canonicals:
            if (
                hash_distance(item["phash"], prior["phash"]) <= config.duplicate_hash_distance
                and signature_difference(item["signature"], prior["signature"]) <= 0.75
                and hsv_histogram_similarity(
                    item["histogram_frame"], prior["histogram_frame"]
                ) >= 0.985
            ):
                canonical_index = int(prior["canonical_index"])
                break
        if canonical_index is None:
            canonical_index = len(canonicals) + 1
            canonicals.append({**item, "canonical_index": canonical_index})
        item["canonical_index"] = canonical_index

    cap = cv2.VideoCapture(str(video_path))
    manifest: list[dict[str, Any]] = []
    canonical_images: dict[int, str] = {}
    for capture_index, item in enumerate(merged, 1):
        if progress_callback:
            progress_callback(
                "slide_capture",
                capture_index - 1,
                len(merged),
                f"Saving clean slide images — {capture_index} of {len(merged)}",
            )
        canonical_index = int(item["canonical_index"])
        if canonical_index in canonical_images:
            image_path = Path(canonical_images[canonical_index])
        else:
            frame = crop_pillarbox(extract_frame(cap, item["capture_time"]))
            image_name = f"slide_{item['index']:03d}_{int(item['start']):06d}.jpg"
            image_path = output_dir / image_name
            cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            canonical_images[canonical_index] = str(image_path)
        manifest.append(
            {
                "index": item["index"],
                "start": item["start"],
                "alignment_start": item["alignment_start"],
                "end": item["end"],
                "capture_time": item["capture_time"],
                "image": str(image_path),
                "canonical_index": canonical_index,
            }
        )
    cap.release()
    if progress_callback:
        progress_callback(
            "slide_capture",
            len(merged),
            len(merged),
            f"Detected {len(merged)} stable slide appearances.",
        )
    (output_dir / "slides.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    diagnostics = {
        "algorithm": "adaptive-block-ensemble-v1",
        "threshold": chosen_threshold,
        "threshold_mode": threshold_mode,
        "activity_mask_fraction": float(np.mean(activity_mask)) if activity_mask is not None else 0.0,
        "candidate_changes": candidate_count,
        "accepted_appearances": len(manifest),
        "canonical_slides": len(canonical_images),
    }
    (output_dir / "transition_detection.json").write_text(
        json.dumps(diagnostics, indent=2), encoding="utf-8"
    )
    return manifest, duration


def whisper_model_files_complete(model_dir: Path) -> bool:
    required = ("config.json", "model.bin", "tokenizer.json")
    try:
        return all(
            (model_dir / name).is_file() and (model_dir / name).stat().st_size > 0
            for name in required
        )
    except OSError:
        return False


def whisper_model_is_cached(model_name: str, models_dir: Path) -> bool:
    """Return whether a complete model is already present without downloading it."""
    from faster_whisper.utils import _MODELS

    repo_id = model_name if "/" in model_name else _MODELS.get(model_name)
    if not repo_id:
        return False
    trusted_model_dir = (
        models_dir
        / "trusted"
        / re.sub(r"[^0-9A-Za-z._-]+", "-", model_name)
    )
    if whisper_model_files_complete(trusted_model_dir):
        return True

    repo_cache = models_dir / ("models--" + repo_id.replace("/", "--"))
    snapshots_dir = repo_cache / "snapshots"
    if not snapshots_dir.is_dir():
        return False
    try:
        snapshots = (path for path in snapshots_dir.iterdir() if path.is_dir())
        return any(whisper_model_files_complete(snapshot) for snapshot in snapshots)
    except OSError:
        return False


def materialize_whisper_snapshot(snapshot: Path, destination: Path, repo_cache: Path) -> bool:
    """Create a link-free model directory from a Hugging Face snapshot.

    Hugging Face snapshots use symbolic links on Windows. Packaged applications
    can reject those reparse points with WinError 448, so this creates ordinary
    hard links to the underlying blob files instead. Hard links use no additional
    model-sized disk space and are safe for CTranslate2 to open.
    """
    names = (
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
        "vocabulary.txt",
    )
    destination.mkdir(parents=True, exist_ok=True)
    # Newer Hugging Face Xet caches can use two link layers: the snapshot links
    # to a repository blob, which links again to a shared blob beside the repo.
    # Resolve each layer textually before opening it so Windows never traverses
    # the reparse point that can trigger WinError 448.
    cache_root = os.path.normcase(os.path.abspath(str(repo_cache.parent)))
    copied = 0
    for name in names:
        entry = snapshot / name
        if not os.path.lexists(str(entry)):
            continue
        source = entry
        visited: set[str] = set()
        for _hop in range(8):
            source_path = os.path.normcase(os.path.abspath(str(source)))
            if os.path.commonpath((cache_root, source_path)) != cache_root:
                raise RuntimeError(
                    f"Refusing model link outside the Hugging Face cache: {name}"
                )
            if source_path in visited:
                raise RuntimeError(f"Circular Hugging Face model link: {name}")
            visited.add(source_path)
            if not os.path.islink(str(source)):
                break
            target = os.readlink(str(source))
            source = Path(os.path.normpath(os.path.join(str(source.parent), target)))
        else:
            raise RuntimeError(f"Too many Hugging Face model link layers: {name}")
        source_path = os.path.normcase(os.path.abspath(str(source)))
        source_size = os.stat(source_path).st_size
        if source_size <= 0:
            continue
        output = destination / name
        try:
            if output.is_file() and output.stat().st_size == source_size:
                copied += 1
                continue
        except OSError:
            pass
        if os.path.lexists(str(output)):
            output.unlink()
        try:
            os.link(source_path, str(output))
        except OSError:
            shutil.copy2(source_path, output)
        copied += 1
    return copied > 0 and whisper_model_files_complete(destination)


def prepare_whisper_model(
    model_name: str,
    models_dir: Path,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Download or repair a local Faster Whisper model and verify required files."""
    from faster_whisper.utils import _MODELS
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    repo_id = model_name if "/" in model_name else _MODELS.get(model_name)
    if not repo_id:
        raise ValueError(f"Unknown Whisper model: {model_name}")
    repo_cache = models_dir / ("models--" + repo_id.replace("/", "--"))
    trusted_model_dir = models_dir / "trusted" / re.sub(r"[^0-9A-Za-z._-]+", "-", model_name)
    if whisper_model_files_complete(trusted_model_dir):
        if progress_callback:
            progress_callback(
                "model",
                1,
                1,
                f"Using the cached Whisper {model_name} speech model.",
            )
        return trusted_model_dir
    snapshots_dir = repo_cache / "snapshots"
    if snapshots_dir.is_dir():
        snapshots = sorted(
            (path for path in snapshots_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for snapshot in snapshots:
            if materialize_whisper_snapshot(snapshot, trusted_model_dir, repo_cache):
                if progress_callback:
                    progress_callback(
                        "model",
                        1,
                        1,
                        f"Using the cached Whisper {model_name} speech model.",
                    )
                return trusted_model_dir

    repairing = repo_cache.exists()
    action = "Repairing an incomplete" if repairing else "Downloading"
    if progress_callback:
        progress_callback(
            "model_download",
            0,
            0,
            f"{action} Whisper {model_name} model — this one-time download can take several minutes…",
        )

    callback = progress_callback

    class ModelDownloadProgress(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["file"] = io.StringIO()
            kwargs["leave"] = False
            super().__init__(*args, **kwargs)

        def update(self, amount=1):
            result = super().update(amount)
            if callback and self.total:
                current = int(self.n)
                total = int(self.total)
                percent = min(100, int(current * 100 / max(1, total)))
                label = str(self.desc or "model files").strip()
                callback(
                    "model_download",
                    current,
                    total,
                    f"Downloading speech model — {label}: "
                    f"{current / 1048576:.0f} of {total / 1048576:.0f} MB ({percent}%)",
                )
            return result

    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    allow_patterns = (
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
    )
    last_error: Exception | None = None
    for force_download in (False, True):
        try:
            snapshot_path = Path(snapshot_download(
                repo_id,
                cache_dir=str(models_dir),
                allow_patterns=allow_patterns,
                max_workers=1,
                force_download=force_download,
                tqdm_class=ModelDownloadProgress,
            ))
            if materialize_whisper_snapshot(snapshot_path, trusted_model_dir, repo_cache):
                if progress_callback:
                    progress_callback(
                        "model_download",
                        1,
                        1,
                        f"Whisper {model_name} model is ready.",
                    )
                return trusted_model_dir
            last_error = RuntimeError("required model files are still missing")
        except Exception as exc:
            last_error = exc
            if progress_callback and not force_download:
                progress_callback(
                    "model_download",
                    0,
                    0,
                    "The first model download was incomplete; retrying the missing files…",
                )
    raise RuntimeError(f"Could not download a complete Whisper model: {last_error}")


def bounded_whisper_hotwords(model: Any, hotwords: str, maximum_tokens: int = 96) -> str:
    """Keep terminology useful without exhausting Whisper's decoding context.

    Faster Whisper combines hotwords with up to half a context window of prior
    transcript. Its own hotword cap can therefore still leave zero tokens for
    decoding. Reserve most of the window for speech and continuity instead.
    """
    cleaned = re.sub(r"\s+", " ", hotwords).strip(" ,")
    if not cleaned:
        return ""
    encoding = model.hf_tokenizer.encode(
        " " + cleaned,
        add_special_tokens=False,
    )
    token_ids = list(encoding.ids)
    if len(token_ids) <= maximum_tokens:
        return cleaned
    return model.hf_tokenizer.decode(token_ids[:maximum_tokens]).strip(" ,")


def transcribe(
    video_path: Path,
    work_dir: Path,
    model_name: str,
    model_cache: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    hotwords: str = "",
    initial_prompt: str = DEFAULT_PROMPT,
) -> tuple[list[dict[str, Any]], str]:
    # PyAV 19 removed the ``metadata_errors`` keyword while some Faster Whisper
    # releases still pass it.  Keep the tool usable with either PyAV API.
    import av

    original_av_open = av.open

    def compatible_av_open(*args, **kwargs):
        try:
            return original_av_open(*args, **kwargs)
        except TypeError:
            if "metadata_errors" not in kwargs:
                raise
            kwargs = dict(kwargs)
            kwargs.pop("metadata_errors", None)
            return original_av_open(*args, **kwargs)

    av.open = compatible_av_open
    from faster_whisper import WhisperModel

    models_dir = model_cache or (work_dir / "models")
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = prepare_whisper_model(model_name, models_dir, progress_callback)
    attempts = [("cuda", "float16"), ("cpu", "int8")]
    last_error: Exception | None = None
    for device, compute_type in attempts:
        try:
            device_label = "NVIDIA GPU" if device == "cuda" else "CPU"
            if progress_callback:
                progress_callback(
                    "model",
                    0,
                    0,
                    f"Loading Whisper {model_name} on {device_label}…",
                )
            print(f"Loading Whisper model {model_name} on {device} ({compute_type})", flush=True)
            model = WhisperModel(
                str(model_path),
                device=device,
                compute_type=compute_type,
            )
            safe_hotwords = bounded_whisper_hotwords(model, hotwords)
            if hotwords and safe_hotwords != hotwords:
                print(
                    f"Limited slide terminology from {len(hotwords)} to "
                    f"{len(safe_hotwords)} characters to preserve decoding context",
                    flush=True,
                )
            iterator, info = model.transcribe(
                str(video_path),
                language="en",
                task="transcribe",
                beam_size=5,
                vad_filter=True,
                # Long recordings conditioned on previous text can fall into
                # repetition loops; skipping hallucinated speech in silences
                # guards against the other common long-form failure.
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
                initial_prompt=initial_prompt or None,
                hotwords=safe_hotwords or None,
                word_timestamps=True,
            )
            if progress_callback:
                progress_callback(
                    "transcribe",
                    0,
                    max(1, int(info.duration * 1000)),
                    f"Transcribing on {device_label} — starting audio analysis…",
                )
            segments: list[dict[str, Any]] = []
            started = time.monotonic()
            last_reported = started
            for segment in iterator:
                if not segment.text.strip():
                    continue
                segment_data = {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": re.sub(r"\s+", " ", segment.text).strip(),
                    "avg_logprob": float(segment.avg_logprob),
                    "compression_ratio": float(segment.compression_ratio),
                    "no_speech_prob": float(segment.no_speech_prob),
                    "words": [
                        {
                            "start": float(word.start),
                            "end": float(word.end),
                            "word": word.word,
                            "probability": float(word.probability),
                        }
                        for word in (segment.words or [])
                    ],
                }
                segments.append(segment_data)
                now = time.monotonic()
                if progress_callback and (now - last_reported >= 0.5 or segment.end >= info.duration):
                    processed = min(float(segment.end), float(info.duration))
                    percent = min(100, int(processed * 100 / max(0.001, info.duration)))
                    elapsed = max(0.001, now - started)
                    speed = processed / elapsed
                    if processed >= 30 and speed > 0:
                        eta = max(0, (info.duration - processed) / speed)
                        remaining = f" · about {format_time(eta)} remaining"
                    else:
                        remaining = " · estimating time remaining"
                    progress_callback(
                        "transcribe",
                        int(processed * 1000),
                        max(1, int(info.duration * 1000)),
                        f"Transcribing on {device_label} — {format_time(processed)} of "
                        f"{format_time(info.duration)} ({percent}%){remaining}",
                    )
                    last_reported = now
            if progress_callback:
                progress_callback(
                    "transcribe",
                    max(1, int(info.duration * 1000)),
                    max(1, int(info.duration * 1000)),
                    f"Transcription complete — {len(segments)} speech segments detected.",
                )
            return segments, device
        except Exception as exc:
            last_error = exc
            if progress_callback and device == "cuda":
                progress_callback(
                    "model",
                    0,
                    0,
                    "NVIDIA transcription was unavailable; switching automatically to CPU…",
                )
            print(f"Whisper attempt on {device} failed: {exc}", file=sys.stderr, flush=True)
    raise RuntimeError(f"Transcription failed: {last_error}")


def write_transcript_files(segments: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "transcript.json").write_text(json.dumps(segments, indent=2), encoding="utf-8")
    with (output_dir / "transcript.txt").open("w", encoding="utf-8") as handle:
        for segment in segments:
            handle.write(f"[{format_time(segment['start'])}] {segment['text']}\n")
    with (output_dir / "transcript.srt").open("w", encoding="utf-8") as handle:
        for index, segment in enumerate(segments, 1):
            handle.write(
                f"{index}\n{format_time(segment['start'], True)} --> "
                f"{format_time(segment['end'], True)}\n{segment['text']}\n\n"
            )


SENTENCE_SNAP_SECONDS = 2.5


def _segment_from_words(template: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    piece = dict(template)
    piece["words"] = words
    piece["start"] = float(words[0]["start"])
    piece["end"] = float(words[-1]["end"])
    piece["text"] = re.sub(r"\s+", " ", "".join(str(word["word"]) for word in words)).strip()
    piece.pop("straddles", None)
    return piece


def split_at_slide_boundaries(
    segment: dict[str, Any], boundaries: list[float]
) -> list[dict[str, Any]]:
    """Split a speech segment where the slide changes, at the nearest sentence end.

    Whole segments used to be filed by their midpoint, so a sentence spoken
    just after a slide change could land under the previous slide. With word
    timestamps the segment is cut at a sentence end within a couple of seconds
    of the change. If there is none, the segment stays whole and is marked as
    straddling the change.
    """
    start, end = float(segment["start"]), float(segment["end"])
    inside = [boundary for boundary in boundaries if start < boundary < end]
    if not inside:
        return [segment]
    words = [word for word in segment.get("words") or [] if "start" in word and "end" in word]
    if len(words) < 2:
        return [dict(segment, straddles=True)]
    cuts: list[int] = []
    unresolved = False
    for boundary in inside:
        best: int | None = None
        for index in range(1, len(words)):
            if not re.search(r"[.?!][\"'”’)]*$", str(words[index - 1]["word"]).strip()):
                continue
            distance = abs(float(words[index]["start"]) - boundary)
            if distance <= SENTENCE_SNAP_SECONDS and (
                best is None or distance < abs(float(words[best]["start"]) - boundary)
            ):
                best = index
        if best is None:
            unresolved = True
        elif best not in cuts:
            cuts.append(best)
    if not cuts:
        return [dict(segment, straddles=True)]
    pieces: list[dict[str, Any]] = []
    previous = 0
    for cut in sorted(cuts) + [len(words)]:
        if cut > previous:
            pieces.append(_segment_from_words(segment, words[previous:cut]))
        previous = cut
    if unresolved:
        pieces = [dict(piece, straddles=True) for piece in pieces]
    return pieces


def align_segments(
    slides: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    comments: list[dict[str, Any]] | None = None,
) -> None:
    for slide in slides:
        slide["segments"] = []
        slide["comments"] = []

    def matching_slide(timestamp: float) -> dict[str, Any]:
        selected = slides[0]
        for slide in slides:
            if slide["alignment_start"] <= timestamp:
                selected = slide
            else:
                break
        return selected

    boundaries = [float(slide["alignment_start"]) for slide in slides[1:]]
    for segment in segments:
        for piece in split_at_slide_boundaries(segment, boundaries):
            midpoint = (piece["start"] + piece["end"]) / 2
            matching_slide(midpoint)["segments"].append(piece)
    for comment in comments or []:
        matching_slide(float(comment["start"]))["comments"].append(comment)


def omit_incidental_slide_appearances(
    slides: list[dict[str, Any]],
    maximum_duration: float = 8.0,
    maximum_spoken_words: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove brief, low-speech slide flicks caused by presentation navigation."""
    if len(slides) <= 1:
        return slides, []

    kept: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    all_segments: list[dict[str, Any]] = []
    all_comments: list[dict[str, Any]] = []
    final_end = float(slides[-1]["end"])

    for slide in slides:
        segments = slide.get("segments", [])
        comments = slide.get("comments", [])
        all_segments.extend(segments)
        all_comments.extend(comments)
        spoken_words = sum(len(segment.get("text", "").split()) for segment in segments)
        duration = float(slide["end"]) - float(slide["start"])
        is_incidental = (
            duration <= maximum_duration
            and spoken_words <= maximum_spoken_words
            and not comments
        )
        if is_incidental:
            omitted.append(slide)
        else:
            kept.append(slide)

    if not kept:
        return slides, []

    for index, slide in enumerate(kept):
        slide["index"] = index + 1
        slide["alignment_start"] = 0.0 if index == 0 else float(slide["start"])
        slide["end"] = float(kept[index + 1]["start"]) if index + 1 < len(kept) else final_end
    align_segments(kept, all_segments, all_comments)
    return kept, omitted


def grouped_transcript(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    previous_end: float | None = None
    for segment in segments:
        should_break = (
            current is None
            or (previous_end is not None and segment["start"] - previous_end > 2.5)
            or len(current["text"]) >= 900
        )
        if should_break:
            current = {"start": segment["start"], "end": segment["end"], "text": segment["text"]}
            groups.append(current)
        else:
            current["text"] += " " + segment["text"]
            current["end"] = segment["end"]
        previous_end = segment["end"]
    return groups


def timeline_events(
    segments: list[dict[str, Any]], comments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group speech while preserving exact insertion points for chat comments."""
    source_events = [(float(item["start"]), 0, "comment", item) for item in comments]
    source_events.extend((float(item["start"]), 1, "segment", item) for item in segments)
    source_events.sort(key=lambda item: (item[0], item[1]))

    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    previous_end: float | None = None

    def flush_current() -> None:
        nonlocal current
        if current is not None:
            events.append(current)
            current = None

    for _start, _order, kind, item in source_events:
        if kind == "comment":
            flush_current()
            events.append(dict(item, kind="comment"))
            previous_end = None
            continue
        should_break = (
            current is None
            or (previous_end is not None and item["start"] - previous_end > 2.5)
            or len(current["text"]) >= 900
            or (current is not None and current.get("speaker") != item.get("speaker"))
        )
        if should_break:
            flush_current()
            current = {
                "kind": "transcript",
                "start": item["start"],
                "end": item["end"],
                "text": item["text"],
                "speaker": item.get("speaker"),
            }
        else:
            current["text"] += " " + item["text"]
            current["end"] = item["end"]
        previous_end = item["end"]
    flush_current()
    return events


def set_style_font(style, name: str, size: float, bold: bool = False) -> None:
    style.font.name = name
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    style._element.rPr.rFonts.set(qn("w:ascii"), name)
    style._element.rPr.rFonts.set(qn("w:hAnsi"), name)


def remove_style_borders(style) -> None:
    properties = style._element.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is not None:
        properties.remove(borders)


def set_image_alt_text(inline_shape, title: str, description: str) -> None:
    doc_pr = inline_shape._inline.docPr
    doc_pr.set("title", title)
    doc_pr.set("descr", description)


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, end])
    run.font.size = Pt(9)


def format_chat_comment(paragraph, comment: dict[str, Any]) -> None:
    paragraph.paragraph_format.left_indent = Inches(0.16)
    paragraph.paragraph_format.right_indent = Inches(0.08)
    paragraph.paragraph_format.space_before = Pt(5)
    paragraph.paragraph_format.space_after = Pt(7)
    properties = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), "F2ECFA")
    properties.append(shading)
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), "6A1B9A")
    borders.append(left)
    properties.append(borders)

    label = paragraph.add_run(
        f"CHAT  [{format_time(comment['start'])}]  {comment['author']}"
    )
    label.bold = True
    label.font.color.rgb = RGBColor(74, 20, 140)
    label.add_break()
    paragraph.add_run(comment["text"])


def build_docx(
    video_path: Path,
    slides: list[dict[str, Any]],
    duration: float,
    output_path: Path,
    transcription_device: str,
    title_text: str,
    omitted_slide_count: int = 0,
    progress_callback: ProgressCallback | None = None,
) -> None:
    document = Document()
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.72)
    section.right_margin = Inches(0.72)

    set_style_font(document.styles["Normal"], "Aptos", 9.75)
    set_style_font(document.styles["Title"], "Aptos Display", 24, True)
    remove_style_borders(document.styles["Title"])
    set_style_font(document.styles["Heading 1"], "Aptos Display", 15, True)
    document.styles["Normal"].paragraph_format.space_after = Pt(7)
    document.styles["Normal"].paragraph_format.line_spacing = 1.03
    document.styles["Heading 1"].paragraph_format.space_before = Pt(4)
    document.styles["Heading 1"].paragraph_format.space_after = Pt(8)
    document.styles["Heading 1"].paragraph_format.keep_with_next = True

    if "Transcript Label" not in document.styles:
        label_style = document.styles.add_style("Transcript Label", WD_STYLE_TYPE.PARAGRAPH)
        set_style_font(label_style, "Aptos", 9.75, True)
        label_style.paragraph_format.space_before = Pt(10)
        label_style.paragraph_format.space_after = Pt(4)
        label_style.paragraph_format.keep_with_next = True

    title = document.add_paragraph(style="Title")
    title.add_run(title_text)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    intro = document.add_paragraph()
    intro.add_run(
        "This document aligns the lecture transcript with the slide displayed at each point in the video. "
        "The transcript is machine-generated and should be checked against the recording before relying on "
        "quotations, case names, statutory provisions or other precise legal wording."
    )

    metadata = document.add_paragraph()
    metadata.add_run("Source video  ").bold = True
    metadata.add_run(video_path.name + "\n")
    metadata.add_run("Duration  ").bold = True
    metadata.add_run(format_time(duration) + "\n")
    metadata.add_run("Detected slide appearances  ").bold = True
    metadata.add_run(str(len(slides)) + "\n")
    if omitted_slide_count:
        metadata.add_run("Brief navigation slide flicks omitted  ").bold = True
        metadata.add_run(str(omitted_slide_count) + "\n")
    comment_count = sum(len(slide.get("comments", [])) for slide in slides)
    if comment_count:
        metadata.add_run("Imported chat comments  ").bold = True
        metadata.add_run(str(comment_count) + "\n")
    metadata.add_run("Transcription processing  ").bold = True
    if transcription_device == "reused":
        metadata.add_run("Local Faster Whisper (cached local transcript)")
    else:
        metadata.add_run("Local Faster Whisper using " + transcription_device.upper())

    footer = section.footer.paragraphs[0]
    add_page_number(footer)

    for slide_position, slide in enumerate(slides):
        if progress_callback:
            progress_callback(
                "export",
                slide_position,
                len(slides),
                f"Building Word document — slide {slide_position + 1} of {len(slides)}",
            )
        document.add_page_break()
        heading = document.add_paragraph(style="Heading 1")
        deck_number = slide.get("deck_slide_number")
        if deck_number:
            heading.add_run(
                f"Deck slide {deck_number} · video appearance {slide_position + 1}  "
                f"{format_time(slide['start'])}"
            )
        else:
            heading.add_run(
                f"Unmatched slide · video appearance {slide_position + 1}  "
                f"{format_time(slide['start'])}"
            )

        picture_paragraph = document.add_paragraph()
        picture_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture_paragraph.paragraph_format.space_after = Pt(4)
        picture_paragraph.paragraph_format.keep_with_next = True
        display_image = slide.get("deck_image") or slide["image"]
        inline_shape = picture_paragraph.add_run().add_picture(display_image, width=Inches(6.35))
        set_image_alt_text(
            inline_shape,
            f"Slide {slide_position + 1}",
            f"Lecture slide displayed at {format_time(slide['start'])}",
        )

        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.paragraph_format.space_after = Pt(8)
        caption.paragraph_format.keep_with_next = True
        caption_run = caption.add_run(
            f"Displayed approximately {format_time(slide['start'])} to {format_time(slide['end'])}"
        )
        caption_run.italic = True
        caption_run.font.size = Pt(9)

        document.add_paragraph(
            "Slide text (already displayed on the slide)", style="Transcript Label"
        )
        slide_text = document.add_paragraph()
        if slide.get("slide_text"):
            slide_text.add_run(str(slide["slide_text"]))
        else:
            missing_text = slide_text.add_run("No reliable slide text was extracted.")
            missing_text.italic = True
        source = document.add_paragraph()
        source_run = source.add_run(f"Text source: {slide.get('text_source', 'not available')}")
        source_run.italic = True
        source_run.font.size = Pt(8.5)

        document.add_paragraph(
            "Lecturer discussion while this slide was displayed", style="Transcript Label"
        )
        segments = slide.get("segments", [])
        comments = slide.get("comments", [])
        if not segments and not comments:
            paragraph = document.add_paragraph()
            run = paragraph.add_run("No speech was detected while this slide was displayed.")
            run.italic = True
            continue
        events = timeline_events(segments, comments)
        for event in events:
            if event["kind"] == "comment":
                format_chat_comment(document.add_paragraph(), event)
                continue
            paragraph = document.add_paragraph()
            timestamp_run = paragraph.add_run(f"[{format_time(event['start'])}] ")
            timestamp_run.bold = True
            if event.get("speaker"):
                speaker_run = paragraph.add_run(f"{event['speaker']}: ")
                speaker_run.bold = True
            paragraph.add_run(event["text"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback(
            "export",
            len(slides),
            len(slides),
            "Saving and finalising the Word document…",
        )
    document.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a Word transcript aligned to automatically detected lecture slides."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "run")
    parser.add_argument("--model", default="turbo")
    parser.add_argument(
        "--title",
        help="Document title (defaults to '<video filename> Transcript and Slides').",
    )
    parser.add_argument(
        "--slide-threshold",
        type=float,
        default=None,
        help="Optional manual block-change ratio; omitted uses per-video calibration.",
    )
    parser.add_argument("--minimum-slide-duration", type=float, default=2.5)
    parser.add_argument("--slides-only", action="store_true")
    parser.add_argument("--reuse-transcript", action="store_true")
    parser.add_argument(
        "--comments",
        type=Path,
        help="Optional text or Markdown file containing copied timestamped chat comments.",
    )
    parser.add_argument(
        "--keep-brief-slides",
        action="store_true",
        help="Keep very brief slide appearances even when they contain three or fewer spoken words.",
    )
    parser.add_argument("--brief-slide-seconds", type=float, default=8.0)
    parser.add_argument("--brief-slide-max-words", type=int, default=3)
    args = parser.parse_args()

    video_path = args.video.resolve()
    work_dir = args.work_dir.resolve()
    slides_dir = work_dir / "slides"
    transcript_dir = work_dir / "transcript"
    work_dir.mkdir(parents=True, exist_ok=True)

    slides, duration = detect_slides(
        video_path,
        slides_dir,
        threshold=args.slide_threshold,
        minimum_duration=args.minimum_slide_duration,
    )
    print(f"Detected {len(slides)} stable slide appearances", flush=True)
    if args.slides_only:
        return

    transcript_json = transcript_dir / "transcript.json"
    if args.reuse_transcript and transcript_json.exists():
        segments = json.loads(transcript_json.read_text(encoding="utf-8"))
        transcription_device = "reused"
    else:
        segments, transcription_device = transcribe(video_path, work_dir, args.model)
        write_transcript_files(segments, transcript_dir)
    print(f"Transcribed {len(segments)} timestamped speech segments", flush=True)

    comments = []
    if args.comments:
        comments = parse_chat_comments(args.comments.read_text(encoding="utf-8"))
        print(f"Parsed {len(comments)} timestamped chat comments", flush=True)
    align_segments(slides, segments, comments)
    omitted_slides: list[dict[str, Any]] = []
    if not args.keep_brief_slides:
        slides, omitted_slides = omit_incidental_slide_appearances(
            slides,
            maximum_duration=args.brief_slide_seconds,
            maximum_spoken_words=args.brief_slide_max_words,
        )
        print(f"Omitted {len(omitted_slides)} brief navigation slide appearances", flush=True)
    alignment = [
        {
            key: value
            for key, value in slide.items()
            if key in {"index", "start", "end", "capture_time", "image", "segments", "comments"}
        }
        for slide in slides
    ]
    (work_dir / "alignment.json").write_text(json.dumps(alignment, indent=2), encoding="utf-8")
    title_text = args.title or f"{video_path.stem} Transcript and Slides"
    build_docx(
        video_path,
        slides,
        duration,
        args.output.resolve(),
        transcription_device,
        title_text,
        len(omitted_slides),
    )
    print(f"Created {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
