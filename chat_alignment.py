"""Calibrate the chat clock against the recording.

Blackboard chat timestamps are not measured on the recording's clock: in a
reviewed lecture they ran 2–3 minutes late, with a slowly growing drift, so
every chat question landed after the spoken answer to it. The lecturer often
echoes a chat comment aloud ("Answering Jock…", or repeats a distinctive
word), which gives anchor points. This module finds those echoes and fits a
robust line, offset = a + b * chat_time, through them.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import random
import re
from typing import Any

from common_words import COMMON_WORDS

SEARCH_BEFORE = 420.0   # seconds an echo may precede the chat stamp
SEARCH_AFTER = 90.0     # seconds an echo may follow the chat stamp
INLIER_TOLERANCE = 15.0
MAXIMUM_DRIFT = 0.08    # seconds of drift per second of recording
MINIMUM_COMMENTS = 3
SPEAKING_LAG = 6.0      # place a comment just before the answer that echoes it


@dataclass
class ChatClockFit:
    offset: float          # seconds added at chat time zero
    drift: float           # extra seconds added per second of chat time
    support: int           # chat comments whose echo agrees with the fit
    method: str            # "auto", "manual" or "none"

    def shift_at(self, chat_time: float) -> float:
        return self.offset + self.drift * chat_time

    def describe(self) -> str:
        if self.method == "none":
            return "not adjusted"
        sign = "-" if self.offset < 0 else "+"
        whole = int(round(abs(self.offset)))
        text = f"{sign}{whole // 60}:{whole % 60:02d} ({self.method}"
        if self.method == "auto":
            text += f", {self.support} echoes"
            if abs(self.drift) >= 0.002:
                text += f", drift {self.drift * 3600:+.0f}s/h"
        return text + ")"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'’]+", text.casefold())


def transcript_words(segments: list[dict[str, Any]]) -> list[tuple[float, str]]:
    """Timed lower-case words, using word timestamps when available."""
    words: list[tuple[float, str]] = []
    for segment in segments:
        timed = segment.get("words") or []
        if timed:
            for word in timed:
                for token in _tokens(str(word.get("word", ""))):
                    words.append((float(word.get("start", segment["start"])), token))
            continue
        tokens = _tokens(str(segment.get("text", "")))
        if not tokens:
            continue
        start = float(segment["start"])
        span = max(0.0, float(segment.get("end", start)) - start)
        for index, token in enumerate(tokens):
            words.append((start + span * index / len(tokens), token))
    return words


def _comment_keys(comment: dict[str, Any], frequency: dict[str, int]) -> list[tuple[str, float]]:
    keys: list[tuple[str, float]] = []
    author_tokens = _tokens(str(comment.get("author", "")))
    if author_tokens and len(author_tokens[0]) >= 3 and author_tokens[0] not in COMMON_WORDS:
        keys.append((author_tokens[0], 3.0))
    for token in set(_tokens(str(comment.get("text", "")))):
        if len(token) >= 4 and token not in COMMON_WORDS and 1 <= frequency.get(token, 0) <= 3:
            keys.append((token, 1.0))
    return keys


def estimate_chat_clock(
    comments: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> ChatClockFit:
    """Fit the chat-to-recording clock from spoken echoes of chat comments."""
    words = transcript_words(segments)
    if not comments or not words:
        return ChatClockFit(0.0, 0.0, 0, "none")
    frequency: dict[str, int] = {}
    positions: dict[str, list[float]] = {}
    for time, token in words:
        frequency[token] = frequency.get(token, 0) + 1
        positions.setdefault(token, []).append(time)

    # Each observation: (comment index, chat time, shift, weight)
    observations: list[tuple[int, float, float, float]] = []
    for index, comment in enumerate(comments):
        chat_time = float(comment["start"])
        for key, weight in _comment_keys(comment, frequency):
            for spoken in positions.get(key, []):
                shift = spoken - chat_time
                if -SEARCH_BEFORE <= shift <= SEARCH_AFTER:
                    observations.append((index, chat_time, shift, weight))
    if len({item[0] for item in observations}) < MINIMUM_COMMENTS:
        return ChatClockFit(0.0, 0.0, 0, "none")

    def score(offset: float, drift: float) -> tuple[float, int]:
        best: dict[int, float] = {}
        for index, chat_time, shift, weight in observations:
            if abs(shift - (offset + drift * chat_time)) <= INLIER_TOLERANCE:
                best[index] = max(best.get(index, 0.0), weight)
        return sum(best.values()), len(best)

    candidates: list[tuple[float, float]] = [(item[2], 0.0) for item in observations]
    pairs = [
        (first, second)
        for first, second in itertools.combinations(observations, 2)
        if first[0] != second[0] and abs(first[1] - second[1]) >= 60
    ]
    if len(pairs) > 4000:
        pairs = random.Random(7).sample(pairs, 4000)
    for first, second in pairs:
        drift = (second[2] - first[2]) / (second[1] - first[1])
        if abs(drift) <= MAXIMUM_DRIFT:
            candidates.append((first[2] - drift * first[1], drift))

    best_line = (0.0, 0.0)
    best_score = (0.0, 0)
    for offset, drift in candidates:
        current = score(offset, drift)
        if current[0] > best_score[0] or (
            current[0] == best_score[0] and abs(drift) < abs(best_line[1])
        ):
            best_line, best_score = (offset, drift), current
    if best_score[1] < MINIMUM_COMMENTS or best_score[0] < MINIMUM_COMMENTS + 1:
        return ChatClockFit(0.0, 0.0, best_score[1], "none")

    # Refine with least squares over the closest observation per comment.
    offset, drift = best_line
    closest: dict[int, tuple[float, float]] = {}
    for index, chat_time, shift, _weight in observations:
        residual = abs(shift - (offset + drift * chat_time))
        if residual <= INLIER_TOLERANCE and (
            index not in closest
            or residual < abs(closest[index][1] - (offset + drift * closest[index][0]))
        ):
            closest[index] = (chat_time, shift)
    points = list(closest.values())
    if len(points) >= 4:
        mean_x = sum(x for x, _ in points) / len(points)
        mean_y = sum(y for _, y in points) / len(points)
        spread = sum((x - mean_x) ** 2 for x, _ in points)
        if spread > 0:
            fitted = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
            if abs(fitted) <= MAXIMUM_DRIFT:
                drift = fitted
                offset = mean_y - drift * mean_x
    else:
        offset = sum(y - drift * x for x, y in points) / len(points)
    return ChatClockFit(offset, drift, len(points), "auto")


def apply_chat_clock(
    comments: list[dict[str, Any]],
    fit: ChatClockFit,
    lag: float = SPEAKING_LAG,
) -> list[dict[str, Any]]:
    """Return comments moved onto the recording clock, keeping the original time."""
    if fit.method == "none":
        return [dict(comment) for comment in comments]
    adjusted: list[dict[str, Any]] = []
    for comment in comments:
        original = float(comment.get("original_start", comment["start"]))
        shift = fit.shift_at(original) - (lag if fit.method == "auto" else 0.0)
        adjusted.append(
            dict(comment, original_start=original, start=max(0.0, original + shift))
        )
    adjusted.sort(key=lambda item: item["start"])
    return adjusted
