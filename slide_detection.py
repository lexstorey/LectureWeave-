"""Adaptive slide-change detection primitives for LectureWeave+.

The algorithms in this module are original Python adaptations informed by:

* itsyujay/video-slide-extractor at 221454d1cc296e75a2ddf83cd3ed587d5922c232
  (MIT, Copyright 2026 Video2Any): block change ratios, activity masking,
  footage-specific calibration, and stable-dwell recapture.
* aggarwal-shiv/LectureLens at 05b69841bde1555f516892403052ec57ddcaa5e4
  (MIT, Copyright 2025 Shivam Aggarwal): multi-signal confirmation and global
  perceptual deduplication.

The upstream implementations were not copied verbatim.  See
UPSTREAM_ADOPTION.md and THIRD_PARTY_NOTICES.txt for details and licences.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class DetectionConfig:
    analysis_width: int = 256
    analysis_height: int = 144
    block_size: int = 8
    block_delta: float = 14.0
    activity_fraction: float = 0.50
    maximum_mask_fraction: float = 0.35
    default_threshold: float = 0.018
    minimum_threshold: float = 0.006
    maximum_threshold: float = 0.20
    stable_fraction: float = 0.45
    duplicate_hash_distance: int = 4


def analysis_frame(frame: np.ndarray, config: DetectionConfig = DetectionConfig()) -> np.ndarray:
    """Return a small colour frame after removing narrow pillarbox margins."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    columns = np.mean(gray, axis=0)
    visible = np.flatnonzero(columns > 10)
    if visible.size and visible[-1] - visible[0] >= width * 0.60:
        frame = frame[:, max(0, int(visible[0]) - 2):min(width, int(visible[-1]) + 3)]
    return cv2.resize(
        frame,
        (config.analysis_width, config.analysis_height),
        interpolation=cv2.INTER_AREA,
    )


def _block_scores(
    previous: np.ndarray,
    current: np.ndarray,
    config: DetectionConfig,
) -> np.ndarray:
    delta = cv2.absdiff(previous, current).astype(np.float32)
    if delta.ndim == 3:
        delta = np.mean(delta, axis=2)
    height = delta.shape[0] - delta.shape[0] % config.block_size
    width = delta.shape[1] - delta.shape[1] % config.block_size
    cropped = delta[:height, :width]
    return cropped.reshape(
        height // config.block_size,
        config.block_size,
        width // config.block_size,
        config.block_size,
    ).mean(axis=(1, 3))


def block_change_ratio(
    previous: np.ndarray,
    current: np.ndarray,
    config: DetectionConfig = DetectionConfig(),
    mask: np.ndarray | None = None,
) -> float:
    """Measure the fraction of meaningful image blocks that changed."""
    changed = _block_scores(previous, current, config) >= config.block_delta
    if mask is not None and mask.shape == changed.shape:
        usable = ~mask
        if np.any(usable):
            return float(np.mean(changed[usable]))
    return float(np.mean(changed))


def build_activity_mask(
    frames: Iterable[np.ndarray],
    config: DetectionConfig = DetectionConfig(),
) -> np.ndarray | None:
    """Mask regions that move in most calibration pairs, such as webcam tiles."""
    materialized = list(frames)
    if len(materialized) < 4:
        return None
    flags = [
        _block_scores(left, right, config) >= config.block_delta
        for left, right in zip(materialized, materialized[1:])
    ]
    mask = np.mean(np.stack(flags), axis=0) > config.activity_fraction
    fraction = float(np.mean(mask))
    if fraction <= 0.0 or fraction > config.maximum_mask_fraction:
        return None
    return mask


def choose_change_threshold(
    ratios: Iterable[float],
    config: DetectionConfig = DetectionConfig(),
) -> tuple[float, str]:
    """Choose a per-video threshold using a guarded one-dimensional split."""
    values = np.asarray(list(ratios), dtype=np.float64)
    if values.size < 6:
        return config.default_threshold, "default"
    near_static = float(np.mean(values <= max(config.minimum_threshold, 0.004)))
    if float(np.max(values)) < config.minimum_threshold:
        return config.minimum_threshold, "static"

    ordered = np.sort(values)
    best_score = -1.0
    best_split: tuple[float, float] | None = None
    for index in range(2, len(ordered) - 2):
        low = ordered[:index]
        high = ordered[index:]
        separation = float(high.mean() - low.mean())
        score = len(low) * len(high) * separation * separation
        if score > best_score:
            best_score = score
            best_split = (float(low.mean()), float(high.mean()))
    if best_split:
        low_mean, high_mean = best_split
        if high_mean >= max(low_mean * 2.5, low_mean + config.minimum_threshold):
            threshold = (low_mean + high_mean) / 2.0
            return float(np.clip(threshold, config.minimum_threshold, config.maximum_threshold)), (
                "bimodal" if near_static >= 0.60 else "adaptive"
            )

    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    threshold = median + max(config.minimum_threshold, 2.0 * mad)
    return float(np.clip(threshold, config.minimum_threshold, config.maximum_threshold)), "motion"


def perceptual_hash(frame: np.ndarray) -> np.ndarray:
    """Return a compact DCT perceptual hash suitable for global deduplication."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(resized)[:8, :8]
    values = low.flatten()
    median = float(np.median(values[1:]))
    return values > median


def hash_distance(left: np.ndarray, right: np.ndarray) -> int:
    return int(np.count_nonzero(left != right))


def hsv_histogram_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_hsv = cv2.cvtColor(left, cv2.COLOR_BGR2HSV)
    right_hsv = cv2.cvtColor(right, cv2.COLOR_BGR2HSV)
    left_hist = cv2.calcHist([left_hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    right_hist = cv2.calcHist([right_hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    cv2.normalize(left_hist, left_hist)
    cv2.normalize(right_hist, right_hist)
    return float(cv2.compareHist(left_hist, right_hist, cv2.HISTCMP_CORREL))

