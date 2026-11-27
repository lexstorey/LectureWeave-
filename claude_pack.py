"""Write the Claude pack: a faithful, structured Markdown export for Claude.

The pack keeps the full transcript and adds only mechanical structure
(slide spans, coverage, gaps, a terms index, emphasis-keyword hits and
filtered chat). Interpretation is left to the receiving Claude. The format is
specified in CLAUDE_PACK_PLAN.md.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any

from common_words import COMMON_WORDS

PARAGRAPH_MIN_SECONDS = 30.0
PARAGRAPH_MAX_SECONDS = 90.0
LOW_CONFIDENCE = 0.35
LONG_DWELL_SECONDS = 300.0
OFF_SLIDE_RUN_SECONDS = 180.0
DISCUSSED_WORDS = 20

FILLER = re.compile(r"\b(?:um+|uh+|erm+|uhm+|ahm+)\b[,.]?\s*", re.IGNORECASE)
SOCIAL = re.compile(
    r"^(?:(?:thanks?|thank you|thx|ty|cheers|hi|hello|hey|good (?:morning|afternoon|evening|night)|"
    r"bye|see (?:you|ya)|lol|haha\w*|hehe\w*|ok(?:ay)?|can you hear (?:me|us)|"
    r"(?:yes,? )?(?:i|we) can hear(?: you)?|welcome back)\b[\s\S]{0,80})$",
    re.IGNORECASE,
)
EMPHASIS = re.compile(
    r"\b(?:very important|really important|important|remember|assessment|assignment|exam|"
    r"journal|essay|will come up|note this|key point|make sure|don't forget|crucial|vital)\b",
    re.IGNORECASE,
)


def format_clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def _attribute(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("\n", " ")


def _content_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z'’\-]{3,}", text.casefold())
        if token not in COMMON_WORDS
    }


# --- people -----------------------------------------------------------------

class Pseudonymiser:
    """Map chat authors to Student A, B, … and hide their names in text."""

    def __init__(
        self,
        comments: list[dict[str, Any]],
        enabled: bool,
        staff_text: str = "",
    ) -> None:
        self.enabled = enabled
        self.labels: dict[str, str] = {}
        self.staff: dict[str, str] = {}
        staff_lower = staff_text.casefold()
        self.patterns: list[tuple[re.Pattern[str], str]] = []
        self.first_patterns = {}
        for comment in comments:
            author = str(comment.get("author", "")).strip()
            if not author or author.casefold() in self.labels or author.casefold() in self.staff:
                continue
            # A chat author named on a slide is staff (host, lecturer, guest).
            if len(author.split()) >= 2 and author.casefold() in staff_lower:
                self.staff[author.casefold()] = f"STAFF ({author.title()})"
                continue
            index = len(self.labels)
            letters = ""
            while True:
                letters = chr(ord("A") + index % 26) + letters
                index = index // 26 - 1
                if index < 0:
                    break
            self.labels[author.casefold()] = f"STUDENT {letters}"
        if enabled:
            full: list[tuple[str, str]] = []
            self.first_patterns: dict[str, tuple[re.Pattern[str], str]] = {}
            for author_key, label in self.labels.items():
                full.append((author_key, label))
                parts = author_key.split()
                if parts and len(parts[0]) >= 3 and parts[0] not in COMMON_WORDS:
                    self.first_patterns[author_key] = (
                        re.compile(rf"(?<![\w@]){re.escape(parts[0])}(?!\w)", re.IGNORECASE),
                        label,
                    )
            full.sort(key=lambda item: -len(item[0]))
            self.patterns = [
                (re.compile(rf"(?<![\w@]){re.escape(name)}(?!\w)", re.IGNORECASE), label)
                for name, label in full
            ]

    def author(self, name: str) -> str:
        key = str(name).strip().casefold()
        if key in self.staff:
            return self.staff[key]
        if not self.enabled:
            return str(name)
        return self.labels.get(key, "STUDENT")

    def text(self, value: str, first_names_of: set[str] | None = None) -> str:
        """Hide full names everywhere; hide first names only for the given authors.

        A first name alone is ambiguous (a story character, the host), so it
        is replaced only where the speech is linked to that person's chat
        comment, or inside chat text (``first_names_of=None`` means all).
        """
        if not self.enabled:
            return value
        for pattern, label in self.patterns:
            value = pattern.sub(f"[{label.title()}]", value)
        for author_key, (pattern, label) in self.first_patterns.items():
            if first_names_of is None or author_key in first_names_of:
                value = pattern.sub(f"[{label.title()}]", value)
        return value.replace("@[", "[")

    def first_names(self) -> dict[str, str]:
        """Lower-case first name → author key, for linking spoken answers to chat."""
        result: dict[str, str] = {}
        for author_key in self.labels:
            first = author_key.split()[0] if author_key.split() else ""
            if len(first) >= 3 and first not in COMMON_WORDS:
                result[first] = author_key
        return result


def is_social(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s?'’]", " ", text).strip()
    if not cleaned:
        return True  # emoji or punctuation only
    if "?" in text and not re.match(r"(?i)^\s*can you hear", text):
        return False
    return bool(SOCIAL.match(cleaned)) and len(cleaned) <= 90


# --- transcript ---------------------------------------------------------------

def _segment_text(segment: dict[str, Any], known_terms: set[str]) -> str:
    words = segment.get("words") or []
    if words and all("word" in word for word in words):
        parts: list[str] = []
        for word in words:
            raw = str(word["word"])
            core = raw.strip()
            letters = re.sub(r"[^\w]", "", core)
            probability = word.get("probability")
            if (
                probability is not None
                and float(probability) < LOW_CONFIDENCE
                and len(letters) >= 4
                and letters.isalpha()
                and letters.casefold() not in known_terms
                and letters.casefold() not in COMMON_WORDS
            ):
                leading = raw[: len(raw) - len(raw.lstrip())]
                prefix = re.match(r"^\W*", core).group(0)
                suffix = re.search(r"\W*$", core).group(0)
                parts.append(f"{leading}{prefix}⟦{letters}?⟧{suffix}")
            else:
                parts.append(raw)
        text = "".join(parts)
    else:
        text = str(segment.get("text", ""))
    text = FILLER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def build_paragraphs(segments: list[dict[str, Any]], known_terms: set[str]) -> list[dict[str, Any]]:
    """Merge speech into paragraphs of roughly 30–90 seconds with one timestamp."""
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for segment in sorted(segments, key=lambda item: float(item["start"])):
        text = _segment_text(segment, known_terms)
        if not text:
            continue
        speaker = segment.get("speaker")
        start, end = float(segment["start"]), float(segment["end"])
        if current is not None:
            length = current["end"] - current["start"]
            gap = start - current["end"]
            if (
                speaker != current["speaker"]
                or length >= PARAGRAPH_MAX_SECONDS
                or (gap > 2.5 and length >= PARAGRAPH_MIN_SECONDS)
                or gap > 12
            ):
                current = None
        if current is None:
            current = {"start": start, "end": end, "speaker": speaker, "parts": [], "straddles": False}
            paragraphs.append(current)
        current["parts"].append(text)
        current["end"] = max(current["end"], end)
        current["straddles"] = current["straddles"] or bool(segment.get("straddles"))
    for paragraph in paragraphs:
        paragraph["text"] = " ".join(paragraph.pop("parts"))
    return paragraphs


def _split_long_dwell(
    paragraphs: list[dict[str, Any]], slide_text: str, slide_start: float, slide_end: float
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Separate long stretches of a long dwell that no longer relate to the slide."""
    if slide_end - slide_start < LONG_DWELL_SECONDS or not paragraphs:
        return [("slide", paragraphs)]
    slide_tokens = _content_tokens(slide_text)
    if len(slide_tokens) < 3:
        return [("slide", paragraphs)]
    related = [
        paragraph["start"] - slide_start < 120 or bool(_content_tokens(paragraph["text"]) & slide_tokens)
        for paragraph in paragraphs
    ]
    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    index = 0
    while index < len(paragraphs):
        if related[index]:
            run_end = index
            while run_end < len(paragraphs) and related[run_end]:
                run_end += 1
            blocks.append(("slide", paragraphs[index:run_end]))
            index = run_end
            continue
        run_end = index
        while run_end < len(paragraphs) and not related[run_end]:
            run_end += 1
        run = paragraphs[index:run_end]
        span = run[-1]["end"] - run[0]["start"]
        blocks.append(("off-slide" if span >= OFF_SLIDE_RUN_SECONDS else "slide", run))
        index = run_end
    merged: list[tuple[str, list[dict[str, Any]]]] = []
    for kind, items in blocks:
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + items)
        else:
            merged.append((kind, items))
    return merged


# --- header -------------------------------------------------------------------

def _ranges(labels: list[tuple[str, int]]) -> str:
    """("B", 1), ("B", 2), ("B", 3) → "B1–B3"."""
    parts: list[str] = []
    index = 0
    while index < len(labels):
        deck, first = labels[index]
        last = first
        while index + 1 < len(labels) and labels[index + 1] == (deck, last + 1):
            index += 1
            last += 1
        parts.append(f"{deck}{first}" if first == last else f"{deck}{first}–{deck}{last}")
        index += 1
    return ", ".join(parts)


def coverage_summary(
    decks: list[dict[str, Any]],
    slides: list[dict[str, Any]],
    omitted: list[dict[str, Any]],
) -> str:
    discussed: set[tuple[str, int]] = set()
    shown: set[tuple[str, int]] = set()
    for slide in slides:
        number = slide.get("deck_slide_number")
        if not number:
            continue
        key = (str(slide.get("deck_label", "A")), int(number))
        words = sum(len(str(segment.get("text", "")).split()) for segment in slide.get("segments", []))
        (discussed if words >= DISCUSSED_WORDS else shown).add(key)
    for slide in omitted:
        if slide.get("deck_slide_number"):
            shown.add((str(slide.get("deck_label", "A")), int(slide["deck_slide_number"])))
    groups: dict[str, list[tuple[str, int]]] = {"discussed": [], "brief": [], "not shown": []}
    for deck in decks:
        for number in range(1, int(deck["pages"]) + 1):
            key = (deck["label"], number)
            status = "discussed" if key in discussed else "brief" if key in shown else "not shown"
            groups[status].append(key)
    return " · ".join(
        f"{status}: {_ranges(keys)}" for status, keys in groups.items() if keys
    ) or "no deck supplied"


def gaps_summary(segments: list[dict[str, Any]], duration: float) -> str:
    ordered = sorted(segments, key=lambda item: float(item["start"]))
    if not ordered:
        return "no speech detected"
    notes: list[str] = []
    first = ordered[0]
    first_text = str(first.get("text", "")).strip()
    if float(first["start"]) <= 2.0 and (
        first_text[:1].islower()
        or re.match(r"(?i)^(and|so|but|thanks|thank you|okay|right)\b", first_text)
    ):
        notes.append("recording may start mid-lecture (speech begins immediately)")
    elif float(first["start"]) > 30:
        notes.append(f"no speech until {format_clock(float(first['start']))}")
    for previous, following in zip(ordered, ordered[1:]):
        silence = float(following["start"]) - float(previous["end"])
        if silence > 60:
            notes.append(
                f"silence {format_clock(float(previous['end']))}–{format_clock(float(following['start']))}"
            )
    tail = duration - float(ordered[-1]["end"])
    if tail > 60:
        notes.append(f"no speech in the final {format_clock(tail)}")
    elif tail < 2 and not re.search(r"[.?!][\"'”’)]*$", str(ordered[-1].get("text", "")).strip()):
        notes.append("recording may end mid-sentence")
    return " · ".join(notes) or "none detected"


def terms_index(
    segments: list[dict[str, Any]], known_terms: set[str], limit: int = 40
) -> str:
    counts: dict[str, list[Any]] = {}
    phrase = re.compile(r"\b[A-Z][a-z’']+(?:\s+(?:of|and|the|for|v)?\s*[A-Z][\w’']+){1,4}\b")
    acronym = re.compile(r"\b[A-Z][A-Z0-9&]{1,9}s?\b")
    for segment in sorted(segments, key=lambda item: float(item["start"])):
        text = str(segment.get("text", ""))
        found = [match.group(0) for match in acronym.finditer(text)]
        found += [match.group(0) for match in phrase.finditer(text)]
        for term in found:
            term = re.sub(r"\s+", " ", term).strip()
            words = term.split()
            while len(words) > 1 and words[0].casefold() in COMMON_WORDS | {"a", "an", "so", "and", "but", "now", "well"}:
                words = words[1:]
            term = " ".join(words)
            if " " not in term and not term.isupper():
                continue
            if term in {"I", "OK", "TV", "AM", "PM"}:
                continue
            first_word = term.split()[0]
            if " " in term and first_word.casefold() in COMMON_WORDS and len(term.split()) == 2:
                continue
            entry = counts.setdefault(term, [0, float(segment["start"])])
            entry[0] += 1
    ranked = sorted(
        (item for item in counts.items() if item[1][0] >= 2 or item[0].isupper()),
        key=lambda item: (-item[1][0], item[1][1]),
    )[:limit]
    parts: list[str] = []
    for term, (count, first) in ranked:
        known = term.casefold() in known_terms or all(
            word.casefold() in known_terms for word in term.split() if word[:1].isupper()
        )
        flag = "" if known else " ⚠"
        parts.append(f"{term} ×{count} ({format_clock(first)}){flag}")
    return " · ".join(parts) or "none"


def emphasis_hits(segments: list[dict[str, Any]], limit: int = 30) -> str:
    hits: list[str] = []
    for segment in sorted(segments, key=lambda item: float(item["start"])):
        text = str(segment.get("text", ""))
        for match in EMPHASIS.finditer(text):
            start = max(0, match.start() - 50)
            snippet = re.sub(r"\s+", " ", text[start: match.end() + 50]).strip()
            hits.append(f'{format_clock(float(segment["start"]))} "…{snippet}…"')
            break
        if len(hits) >= limit:
            break
    return "\n".join(hits) or "none"


# --- pack ---------------------------------------------------------------------

def slide_identity(slide: dict[str, Any], position: int) -> str:
    number = slide.get("deck_slide_number")
    if number:
        return f"{slide.get('deck_label', 'A')}{number}"
    return f"V{position}"


def build_claude_pack(
    *,
    output_path: Path,
    title: str,
    video_name: str,
    duration: float,
    slides: list[dict[str, Any]],
    omitted: list[dict[str, Any]],
    decks: list[dict[str, Any]],
    comments_total: int,
    chat_clock: str,
    model_name: str,
    glossary: list[str] | None = None,
    pseudonymise: bool = True,
    visual_labels: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Write the pack and return a short summary for the app to display."""
    visual_labels = visual_labels or {}
    all_comments = sorted(
        (comment for slide in slides for comment in slide.get("comments", [])),
        key=lambda item: float(item["start"]),
    )
    people = Pseudonymiser(
        all_comments,
        pseudonymise,
        "\n".join(str(slide.get("slide_text", "")) for slide in slides),
    )
    first_names = people.first_names()
    chat_ids: dict[int, str] = {}
    kept_chat = 0
    for number, comment in enumerate(all_comments, 1):
        chat_ids[id(comment)] = f"chat-{number}"
        if not is_social(str(comment.get("text", ""))):
            kept_chat += 1

    known_terms = {term.casefold() for term in glossary or []}
    for slide in slides:
        known_terms |= {
            token.casefold() for token in re.findall(r"[\w’'\-]+", str(slide.get("slide_text", "")))
        }
        for phrase in str(slide.get("slide_text", "")).splitlines():
            known_terms.add(phrase.strip().casefold())
    all_segments = [segment for slide in slides for segment in slide.get("segments", [])]
    speakers = sorted({str(segment["speaker"]) for segment in all_segments if segment.get("speaker")})

    def paragraph_lines(paragraphs: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for paragraph in paragraphs:
            raw = paragraph["text"]
            links = []
            linked_authors: set[str] = set()
            lowered = raw.casefold()
            for comment in all_comments:
                first = str(comment.get("author", "")).split()[0].casefold() if comment.get("author") else ""
                if (
                    first in first_names
                    and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", lowered)
                    and paragraph["start"] - 180 <= float(comment["start"]) <= paragraph["end"] + 5
                ):
                    links.append(chat_ids[id(comment)])
                    linked_authors.add(str(comment.get("author", "")).strip().casefold())
            speaker = f" {paragraph['speaker']}" if paragraph.get("speaker") else ""
            marker = f" ↩{','.join(dict.fromkeys(links))}" if links else ""
            colon = ":" if speaker or marker else ""
            lines.append(
                f"[{format_clock(paragraph['start'])}]{speaker}{marker}{colon} "
                f"{people.text(raw, linked_authors)}"
            )
        return lines

    def chat_lines(start: float, end: float) -> list[str]:
        lines: list[str] = []
        for comment in all_comments:
            moment = float(comment["start"])
            if not (start <= moment < end):
                continue
            text = str(comment.get("text", ""))
            if is_social(text):
                continue
            lines.append(
                f"[{format_clock(moment)}] {chat_ids[id(comment)]} "
                f"{people.author(comment.get('author', ''))}: {people.text(text)}"
            )
        return lines

    body: list[str] = []
    appearances: dict[str, int] = {}
    previous_deck: str | None = None
    deck_switches: list[str] = []
    off_slide_count = 0
    for position, slide in enumerate(slides, 1):
        identity = slide_identity(slide, position)
        appearances[identity] = appearances.get(identity, 0) + 1
        start, end = float(slide["start"]), float(slide["end"])
        if slide.get("deck_slide_number"):
            deck = str(slide.get("deck_label", "A"))
            if previous_deck and deck != previous_deck:
                deck_switches.append(f"{previous_deck}→{deck} at {format_clock(start)}")
            previous_deck = deck
        slide_text = str(slide.get("slide_text") or "").strip()
        title_line = slide_text.splitlines()[0][:80] if slide_text else ""
        matched = bool(slide.get("deck_slide_number"))
        attributes = [
            f'id="{identity}"',
            f'deck="{_attribute(slide.get("deck_label", "A"))}" slide="{slide["deck_slide_number"]}"'
            if matched else "",
            f'appearance="{appearances[identity]}"',
            f'title="{_attribute(title_line)}"' if title_line else "",
            f'start="{format_clock(start)}" end="{format_clock(end)}"',
            f'match="{"deck" if matched else "ocr" if slide_text else "none"}"',
            f'match_conf="{slide["match_confidence"]}"' if slide.get("match_confidence") else "",
            f'alt="{slide["match_alternative"]}"' if slide.get("match_alternative") else "",
        ]
        flicks = [
            f"{slide_identity(item, 0) if item.get('deck_slide_number') else 'unmatched'}"
            f"@{format_clock(float(item['start']))}({max(1, round(float(item['end']) - float(item['start'])))}s)"
            for item in omitted
            if start <= float(item["start"]) < end
        ]
        if flicks:
            attributes.append(f'flicks="{" ".join(flicks)}"')
        visual = visual_labels.get(int(slide.get("canonical_index", slide.get("index", position))))
        if visual:
            attributes.append(f'visual="{visual}"')
        if end - start >= LONG_DWELL_SECONDS:
            attributes.append(f'long_dwell="{round((end - start) / 60)}m"')
        paragraphs = build_paragraphs(slide.get("segments", []), known_terms)
        if any(paragraph["straddles"] for paragraph in paragraphs):
            attributes.append('straddles="yes"')
        source = "deck" if matched else "ocr"
        blocks = _split_long_dwell(paragraphs, slide_text, start, end)
        for block_index, (kind, block) in enumerate(blocks):
            block_start = start if block_index == 0 else block[0]["start"]
            block_end = blocks[block_index + 1][1][0]["start"] if block_index + 1 < len(blocks) else end
            if kind == "off-slide":
                off_slide_count += 1
                body.append(
                    f'<segment id="S{off_slide_count}" kind="off-slide" start="{format_clock(block_start)}" '
                    f'end="{format_clock(block_end)}" during="{identity}">'
                )
                body.append(
                    "Slide " + identity + " stayed on screen, but this speech no longer relates to its text."
                )
            else:
                tag_attributes = " ".join(item for item in attributes if item)
                if block_index:
                    tag_attributes = re.sub(r'start="[^"]*" end="[^"]*"',
                                            f'start="{format_clock(block_start)}" end="{format_clock(block_end)}"',
                                            tag_attributes) + ' continued="yes"'
                body.append(f"<slide {tag_attributes}>")
                if slide_text and block_index == 0:
                    body.append(f'<on_slide source="{source}">\n{people.text(slide_text, set())}\n</on_slide>')
            lines = paragraph_lines(block)
            body.append("<transcript>\n" + ("\n".join(lines) if lines else "(no speech)") + "\n</transcript>")
            chats = chat_lines(block_start, block_end)
            if chats:
                body.append("<chat>\n" + "\n".join(chats) + "\n</chat>")
            body.append("</segment>" if kind == "off-slide" else "</slide>")
            body.append("")

    deck_text = " · ".join(
        f'{deck["label"]} = {deck["name"]} ({deck["pages"]} slides)'
        + (" — text layer unreadable, slide text read from the video" if deck.get("unreadable_text") else "")
        for deck in decks
    ) or "none supplied (slide text read from the video with OCR)"
    speaker_text = (
        ", ".join(speakers) + " (automatic labels; names not assigned)"
        if speakers else "not labelled"
    )
    staff_note = f" · staff in chat: {', '.join(people.staff.values())}" if people.staff else ""
    student_text = staff_note.lstrip(" ·") + (" · " if staff_note else "") + (
        f"{len(people.labels)} students pseudonymised as STUDENT A, B, …"
        if pseudonymise and people.labels else
        f"{len(people.labels)} chat participants, real names kept" if people.labels else "no chat"
    )
    header = [
        f"# {title}",
        "",
        "LectureWeave+ Claude pack. Each `<slide>` holds the slide's own text (`<on_slide>`), "
        "the verbatim speech while it was on screen (`<transcript>`, timestamped paragraphs, "
        "fillers removed) and chat posted at the time (`<chat>`). `<segment>` marks speech that "
        "is not about the slide on screen. `⟦word?⟧` is a low-confidence word; `↩chat-N` marks "
        "speech answering that chat comment. Slide identity is the deck letter plus the deck's "
        "own slide number; `V` ids are video captures with no deck match.",
        "",
        f'<lecture title="{_attribute(title)}" source="{_attribute(video_name)}" '
        f'duration="{format_clock(duration)}" generated="{date.today().isoformat()}" '
        f'transcription="Whisper {_attribute(model_name)}" chat_clock="{_attribute(chat_clock)}">',
        f"<speakers>{speaker_text} · {student_text}</speakers>",
        f"<decks>{deck_text}</decks>",
        f"<deck_switches>{' · '.join(deck_switches) or 'none'}</deck_switches>",
        f"<coverage>{coverage_summary(decks, slides, omitted)}</coverage>",
        f"<gaps>{gaps_summary(all_segments, duration)}</gaps>",
        f"<chat_summary>{len(all_comments)} of {comments_total} comments placed · "
        f"{kept_chat} kept · {len(all_comments) - kept_chat} greetings or reactions dropped</chat_summary>",
        f"<terms>{people.text(terms_index(all_segments, known_terms), set())}</terms>",
        f"<emphasis_hits>\n{people.text(emphasis_hits(all_segments), set())}\n</emphasis_hits>",
        "</lecture>",
        "",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(header + body).rstrip() + "\n"
    content = re.sub(
        r"[\x00-\x01\x03-\x08\x0b\x0c\x0e-\x1f\x7f\uffff]",
        "",
        content.replace("\x02", "-").replace("\ufffe", "-"),
    )
    output_path.write_text(content, encoding="utf-8")
    text = output_path.read_text(encoding="utf-8")
    return {
        "path": output_path,
        "slides": len(slides),
        "off_slide": off_slide_count,
        "chat_kept": kept_chat,
        "chat_total": comments_total,
        "uncertain_words": "\n".join(body).count("⟦"),
        "approx_tokens": round(len(text) / 3.8),
    }


def build_visuals_pdf(
    slides: list[dict[str, Any]], output_path: Path, maximum_pages: int = 100
) -> dict[int, str]:
    """PDF of distinct captured frames with no deck match, each labelled V1, V2, …

    Claude reads PDF pages visually, so these frames fix OCR errors and show
    diagrams. Returns canonical slide index → V label.
    """
    from PIL import Image, ImageDraw, ImageFont

    labels: dict[int, str] = {}
    pages: list[Image.Image] = []
    for position, slide in enumerate(slides, 1):
        if slide.get("deck_slide_number"):
            continue
        canonical = int(slide.get("canonical_index", slide.get("index", position)))
        if canonical in labels or len(pages) >= maximum_pages:
            continue
        image_path = slide.get("image")
        if not image_path or not Path(image_path).is_file():
            continue
        label = f"V{position}"
        with Image.open(image_path) as source:
            frame = source.convert("RGB")
        frame.thumbnail((1600, 900))
        canvas = Image.new("RGB", (frame.width, frame.height + 44), "white")
        canvas.paste(frame, (0, 44))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default(size=24)
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
        draw.text((12, 9), f"{label} · video capture · {format_clock(float(slide['start']))}",
                  fill=(38, 35, 43), font=font)
        pages.append(canvas)
        labels[canonical] = label
    if pages:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pages[0].save(output_path, "PDF", resolution=110, save_all=True, append_images=pages[1:])
    return labels


CLAUDE_INSTRUCTIONS = """# Reading LectureWeave+ packs

These instructions tell you how to read LectureWeave+ Claude packs. Your own
project instructions still decide what you produce from them.

## Before anything else
- Read the `<lecture>` header: decks, deck switches, coverage, gaps, chat clock
  and speakers. If a gap leaves part of the slide pack uncovered, say in one
  line which recording or segment is probably missing.

## Reading rules
- Slide identity is the deck letter plus the deck's own slide number (`B12`).
  `V` ids are video captures with no deck match; a visuals PDF, when attached,
  shows them. `appearance` counts revisits: merge them, don't duplicate content.
- `<on_slide>` is what the slide says. `<transcript>` is what was said. Note
  what the speaker added (examples, stories, caveats, updates, disagreements
  with the slide) rather than restating the slide.
- `<segment kind="off-slide">` is speech that drifted away from the slide on
  screen. It often holds the best material; don't skip it.
- Attribute views correctly to the lecturer, a guest or a student. Students are
  pseudonymised (STUDENT A, B…); never name them in anything submitted.
- `↩chat-N` marks speech answering that chat comment: read them together.
- `⟦word?⟧` marks a low-confidence word. Resolve it from the slides, readings or
  a term list; if still unsure, keep it and flag it.
- Never "correct" a statute, section, case or figure from the transcript alone.
  Use the slide or a primary source, and flag what you changed.
- `<emphasis_hits>` are keyword matches only. Confirm each from context.
- If a visuals PDF is attached, look at the referenced V images for diagrams,
  tables and charts rather than trusting OCR text.
"""


def write_claude_instructions(folder: Path) -> Path:
    path = folder / "CLAUDE_INSTRUCTIONS.md"
    if not path.exists():
        path.write_text(CLAUDE_INSTRUCTIONS, encoding="utf-8")
    return path
