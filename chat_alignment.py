"""Calibrate the chat clock against the recording.

Blackboard chat timestamps are not measured on the recording's clock: in a
reviewed lecture they ran 2–3 minutes late, with a slowly growing drift, so
every chat question landed after the spoken answer to it. The lecturer often
echoes a chat comment aloud ("Answering Jock…", or repeats a distinctive
word), which gives anchor points. This module finds those echoes and fits a
robust line, offset = a + b * chat_time, through them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import random
import re
from typing import Any

from common_words import COMMON_WORDS

SEARCH_BEFORE = 420.0   # seconds an echo may precede the chat stamp
SEARCH_AFTER = 90.0     # seconds an echo may follow the chat stamp
INLIER_TOLERANCE = 15.0
ANCHOR_TOLERANCE = 40.0
MAXIMUM_DRIFT = 0.08    # seconds of drift per second of recording
MINIMUM_COMMENTS = 3
SPEAKING_LAG = 6.0      # place a comment just before the answer that echoes it
ANCHOR_REACH = 600.0    # how far beyond the outermost echo a shift is trusted
NAME_MAX_MENTIONS = 6   # names said more often are story characters or staff


@dataclass
class ChatClockFit:
    offset: float          # seconds added at chat time zero (trend line)
    drift: float           # extra seconds added per second of chat time
    support: int           # chat comments whose echo agrees with the fit
    method: str            # "auto", "manual" or "none"
    anchors: list[tuple[float, float]] = field(default_factory=list)

    def shift_at(self, chat_time: float) -> float | None:
        """Seconds to add to a chat stamp, or None when it cannot be verified.

        Automatic fits interpolate between echo anchors and hold the nearest
        anchor for up to ``ANCHOR_REACH`` beyond them. The clock can jump
        (for example when a recording was paused), so comments far from any
        anchor are left as stamped rather than guessed.
        """
        if self.method == "manual":
            return self.offset
        if self.method != "auto" or not self.anchors:
            return None
        first_time, first_shift = self.anchors[0]
        last_time, last_shift = self.anchors[-1]
        if chat_time < first_time:
            return first_shift if first_time - chat_time <= ANCHOR_REACH else None
        if chat_time > last_time:
            return last_shift if chat_time - last_time <= ANCHOR_REACH else None
        for (left_time, left_shift), (right_time, right_shift) in zip(self.anchors, self.anchors[1:]):
            if left_time <= chat_time <= right_time:
                if right_time == left_time:
                    return left_shift
                ratio = (chat_time - left_time) / (right_time - left_time)
                return left_shift + ratio * (right_shift - left_shift)
        return last_shift

    def describe(self) -> str:
        if self.method == "none":
            return "not adjusted"

        def clock(value: float) -> str:
            sign = "-" if value < 0 else "+"
            whole = int(round(abs(value)))
            return f"{sign}{whole // 60}:{whole % 60:02d}"

        if self.method == "manual":
            return f"{clock(self.offset)} (manual)"
        shifts = [shift for _time, shift in self.anchors]
        low, high = min(shifts), max(shifts)
        amount = clock(low) if abs(high - low) < 10 else f"{clock(high)} to {clock(low)}"
        start = int(self.anchors[0][0])
        end = int(self.anchors[-1][0])
        return (
            f"{amount} (auto, {self.support} echoes between "
            f"{start // 3600:02d}:{start % 3600 // 60:02d} and {end // 3600:02d}:{end % 3600 // 60:02d}; "
            "comments more than 10 min outside that range left as stamped)"
        )


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
    if (
        author_tokens
        and len(author_tokens[0]) >= 3
        and author_tokens[0] not in COMMON_WORDS
        and frequency.get(author_tokens[0], 0) <= NAME_MAX_MENTIONS
    ):
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

    # Anchors: the closest agreeing echo for each comment, smoothed with a
    # running median so one misheard echo cannot bend the clock.
    offset, drift = best_line
    closest: dict[int, tuple[float, float]] = {}
    for index, chat_time, shift, _weight in observations:
        residual = abs(shift - (offset + drift * chat_time))
        if residual <= ANCHOR_TOLERANCE and (
            index not in closest
            or residual < abs(closest[index][1] - (offset + drift * closest[index][0]))
        ):
            closest[index] = (chat_time, shift)
    points = sorted(closest.values())
    smoothed: list[tuple[float, float]] = []
    for position, (chat_time, _shift) in enumerate(points):
        window = sorted(shift for _time, shift in points[max(0, position - 1): position + 2])
        smoothed.append((chat_time, window[len(window) // 2]))
    return ChatClockFit(offset, drift, len(points), "auto", smoothed)


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
        shift = fit.shift_at(original)
        if shift is None:
            adjusted.append(dict(comment, original_start=original, start=original))
            continue
        shift -= lag if fit.method == "auto" else 0.0
        adjusted.append(
            dict(comment, original_start=original, start=max(0.0, original + shift))
        )
    adjusted.sort(key=lambda item: item["start"])
    return adjusted
