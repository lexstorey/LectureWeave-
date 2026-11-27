"""Optional slide-pack and OCR enrichment for LectureWeave+."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable

import cv2
import numpy as np
from rapidfuzz.fuzz import token_set_ratio

from slide_detection import hash_distance, hsv_histogram_similarity, perceptual_hash


ProgressCallback = Callable[[str, int, int, str], None]


@dataclass
class DeckPage:
    number: int
    text: str
    image: str | None = None
    deck: str = "A"

    @property
    def label(self) -> str:
        return f"{self.deck}{self.number}"


def normalise_slide_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_slide_deck(
    deck_path: Path,
    output_dir: Path,
    progress_callback: ProgressCallback | None = None,
) -> list[DeckPage]:
    """Extract native text and, for PDFs, clean rendered page images."""
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = deck_path.suffix.lower()
    pages: list[DeckPage] = []
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(deck_path))
        try:
            for index in range(len(document)):
                page = document[index]
                if progress_callback:
                    progress_callback(
                        "deck", index, len(document),
                        f"Reading supplied PDF slide pack — page {index + 1} of {len(document)}",
                    )
                image_path = output_dir / f"deck_{index + 1:03d}.png"
                page.render(scale=1.8).to_pil().convert("RGB").save(image_path)
                text_page = page.get_textpage()
                pages.append(DeckPage(
                    index + 1,
                    normalise_slide_text(text_page.get_text_range()),
                    str(image_path),
                ))
                text_page.close()
                page.close()
        finally:
            document.close()
    elif suffix in {".pptx", ".pptm"}:
        from pptx import Presentation

        presentation = Presentation(str(deck_path))
        for index, slide in enumerate(presentation.slides):
            if progress_callback:
                progress_callback(
                    "deck", index, len(presentation.slides),
                    f"Reading supplied PowerPoint text — slide {index + 1} of {len(presentation.slides)}",
                )
            chunks = [
                str(getattr(shape, "text", ""))
                for shape in slide.shapes
                if str(getattr(shape, "text", "")).strip()
            ]
            pages.append(DeckPage(index + 1, normalise_slide_text("\n".join(chunks))))
    else:
        raise ValueError("Slide pack must be a PDF or PowerPoint file.")
    if progress_callback:
        progress_callback("deck", len(pages), len(pages), f"Read {len(pages)} supplied slides.")
    return pages


def _image_features(path: str) -> tuple[np.ndarray, np.ndarray] | None:
    image = cv2.imread(path)
    if image is None:
        return None
    small = cv2.resize(image, (256, 144), interpolation=cv2.INTER_AREA)
    return perceptual_hash(small), cv2.resize(small, (96, 54), interpolation=cv2.INTER_AREA)


def _assign_page(slide: dict[str, Any], page: DeckPage, source: str, confidence: str,
                 alternative: DeckPage | None = None) -> None:
    slide["deck_slide_number"] = page.number
    slide["deck_label"] = page.deck
    slide["deck_text"] = page.text
    slide["slide_text"] = page.text or slide.get("slide_text", "")
    if page.image:
        slide["deck_image"] = page.image
    slide["text_source"] = source
    slide["match_confidence"] = confidence
    if alternative is not None:
        slide["match_alternative"] = alternative.label
    else:
        slide.pop("match_alternative", None)


def match_deck_pages(slides: list[dict[str, Any]], pages: list[DeckPage]) -> None:
    """Attach actual deck page numbers and native content to video appearances.

    Pages may come from several decks (``DeckPage.deck`` = "A", "B", …).
    Rendered PDF pages are matched visually and by text; PowerPoint pages,
    which have no rendering, are matched by text alone. Each match records a
    confidence and, when the runner-up was close, the alternative page.
    """
    rendered = [(page, _image_features(page.image)) for page in pages if page.image]
    rendered = [(page, features) for page, features in rendered if features is not None]
    text_only = [page for page in pages if not page.image]
    if rendered:
        for slide in slides:
            features = _image_features(slide["image"])
            if features is None:
                continue
            slide_hash, slide_hist = features
            scored: list[tuple[float, float, float, int, DeckPage]] = []
            for position, (page, page_features) in enumerate(rendered):
                page_hash, page_hist = page_features
                distance = hash_distance(slide_hash, page_hash)
                histogram_penalty = max(
                    0.0, 1.0 - hsv_histogram_similarity(slide_hist, page_hist)
                ) * 18
                visual_score = distance + histogram_penalty
                text_score = float(token_set_ratio(slide.get("slide_text", ""), page.text))
                combined = visual_score + (100.0 - text_score) * 0.25
                scored.append((combined, visual_score, text_score, position, page))
            scored.sort(key=lambda item: (item[0], item[3]))
            combined, visual_score, text_score, _position, page = scored[0]
            margin = scored[1][0] - combined if len(scored) > 1 else 100.0
            if (
                (text_score >= 55 and margin >= 3)
                or text_score >= 78
                or (visual_score <= 14 and margin >= 2)
            ):
                confident = text_score >= 78 or (visual_score <= 8 and margin >= 6)
                _assign_page(
                    slide,
                    page,
                    "supplied slide pack",
                    "high" if confident else "medium",
                    scored[1][4] if len(scored) > 1 and margin < 4 else None,
                )

    # PowerPoint text is useful without rendering: match it to video OCR text
    # with a confidence margin so the app never guesses.
    if text_only:
        for slide in slides:
            if slide.get("deck_slide_number"):
                continue
            scored_text = sorted(
                (
                    (float(token_set_ratio(slide.get("slide_text", ""), page.text)), -position, page)
                    for position, page in enumerate(text_only)
                ),
                key=lambda item: (item[0], item[1]),
            )
            if not scored_text:
                continue
            score, _position, page = scored_text[-1]
            second = scored_text[-2][0] if len(scored_text) > 1 else 0.0
            if score >= 62 and score - second >= 8:
                _assign_page(
                    slide,
                    page,
                    "supplied PowerPoint text",
                    "high" if score >= 80 and score - second >= 15 else "medium",
                    scored_text[-2][2] if len(scored_text) > 1 and score - second < 12 else None,
                )

    # One PowerPoint deck whose distinct captured slides match its slide count
    # can be assigned in order when nothing could be matched directly.
    decks = {page.deck for page in pages}
    canonical_ids = list(dict.fromkeys(
        int(slide.get("canonical_index", slide["index"])) for slide in slides
    ))
    unmatched = [slide for slide in slides if not slide.get("deck_slide_number")]
    if (
        text_only
        and not rendered
        and len(decks) == 1
        and len(canonical_ids) == len(text_only)
        and len(unmatched) == len(slides)
    ):
        mapping = dict(zip(canonical_ids, text_only))
        for slide in slides:
            page = mapping[int(slide.get("canonical_index", slide["index"]))]
            _assign_page(slide, page, "supplied PowerPoint text", "low")


def slide_reference(slide: dict[str, Any], multiple_decks: bool) -> str | None:
    """Human label for a matched slide, e.g. "Deck B slide 12" or "Deck slide 12"."""
    number = slide.get("deck_slide_number")
    if not number:
        return None
    if multiple_decks:
        return f"Deck {slide.get('deck_label', 'A')} slide {number}"
    return f"Deck slide {number}"


def run_slide_ocr(
    slides: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
) -> None:
    """OCR captured slides that do not already have native slide-pack text."""
    from rapidocr import RapidOCR

    engine = RapidOCR()
    canonical_text: dict[int, str] = {}
    for index, slide in enumerate(slides):
        canonical = int(slide.get("canonical_index", slide["index"]))
        if slide.get("deck_text"):
            text = normalise_slide_text(str(slide["deck_text"]))
        elif canonical in canonical_text:
            text = canonical_text[canonical]
        else:
            if progress_callback:
                progress_callback(
                    "ocr", index, len(slides),
                    f"Reading text from captured slides — {index + 1} of {len(slides)}",
                )
            result = engine(slide["image"])
            text = normalise_slide_text("\n".join(result.txts or ()))
            canonical_text[canonical] = text
        slide["slide_text"] = text
        slide.setdefault("text_source", "RapidOCR")
    if progress_callback:
        progress_callback("ocr", len(slides), len(slides), "Slide text extraction complete.")


_CONNECTORS = {"of", "and", "for", "the", "on", "in", "v", "de", "du", "von", "van", "&"}


def _significant_lines(slides: list[dict[str, Any]]) -> list[list[str]]:
    """Return each distinct slide's lines, minus template text repeated on most slides."""
    distinct: dict[Any, str] = {}
    for slide in slides:
        text = str(slide.get("deck_text") or slide.get("slide_text") or "")
        key = slide.get("canonical_index", text) or text
        distinct.setdefault(key, text)
    slide_lines = [
        [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
        for text in distinct.values()
    ]
    if len(slide_lines) >= 4:
        frequency: dict[str, int] = {}
        for lines in slide_lines:
            for line in set(line.casefold() for line in lines):
                frequency[line] = frequency.get(line, 0) + 1
        limit = len(slide_lines) * 0.5
        slide_lines = [
            [line for line in lines if frequency[line.casefold()] <= limit]
            for lines in slide_lines
        ]
    return slide_lines


def _line_terms(line: str) -> list[tuple[str, int]]:
    """Extract (term, base score) pairs from one slide line."""
    terms: list[tuple[str, int]] = []
    for chunk in re.split(r"[,:;()\[\]/|•·–—>!?\"“”]|\.\s", line):
        terms.extend(_chunk_terms(chunk))
    return terms


def _chunk_terms(chunk: str) -> list[tuple[str, int]]:
    from common_words import COMMON_WORDS

    terms: list[tuple[str, int]] = []
    tokens = re.findall(r"[^\W_][\w’'\-]*|&", chunk)
    while tokens and tokens[0].casefold() in {"the", "a", "an"}:
        tokens = tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        stripped = token.strip("’'-")
        is_capital = bool(stripped) and stripped[0].isupper()
        if is_capital:
            phrase = [stripped]
            look = index + 1
            while look < len(tokens):
                nxt = tokens[look].strip("’'-")
                if nxt and nxt[0].isupper():
                    phrase.append(nxt)
                    look += 1
                elif (
                    nxt.casefold() in _CONNECTORS
                    and look + 1 < len(tokens)
                    and tokens[look + 1][:1].isupper()
                ):
                    phrase.append(nxt)
                    look += 1
                elif re.fullmatch(r"(1[5-9]|20)\d\d", nxt) and phrase[-1] == "Act":
                    phrase.append(nxt)
                    look += 1
                else:
                    break
            content = [word for word in phrase if word.casefold() not in _CONNECTORS]
            if len(content) >= 2:
                terms.append((" ".join(phrase), 3))
                index = look
                continue
        lowered = stripped.casefold()
        if len(stripped) < 2 or lowered in COMMON_WORDS or stripped.isdigit():
            index += 1
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9&]{1,}[a-z]?", stripped):
            terms.append((stripped, 3))
        elif any(ord(character) > 127 for character in stripped):
            terms.append((stripped, 3))
        elif is_capital and len(stripped) >= 4 and index > 0:
            terms.append((stripped, 2))
        elif is_capital and len(stripped) >= 5:
            terms.append((stripped, 1))
        elif len(stripped) >= 9 and stripped.isalpha():
            terms.append((stripped, 1))
        index += 1
    return terms


def load_glossary(path: Path | None) -> list[str]:
    """Read a user glossary: one term per line; blank lines and # comments ignored."""
    if not path:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    terms: list[str] = []
    for line in text.splitlines():
        term = re.sub(r"\s+", " ", line.split("#", 1)[0]).strip(" ,;")
        if term and term.casefold() not in {existing.casefold() for existing in terms}:
            terms.append(term)
    return terms


def transcription_vocabulary(
    slides: list[dict[str, Any]],
    maximum_characters: int = 1200,
    glossary: list[str] | None = None,
) -> str:
    """Rank the terms Whisper is most likely to mishear, best first.

    Glossary terms always come first. Slide terms are ranked by type
    (acronyms, multi-word names and words with diacritics above single
    capitalised words) and by how many distinct slides repeat them. Template
    lines that appear on most slides and common English words are ignored,
    because Whisper's hotword budget is small (about 96 tokens).
    """
    scores: dict[str, list[Any]] = {}
    order = 0
    for lines in _significant_lines(slides):
        seen_here: set[str] = set()
        for line in lines:
            for term, base in _line_terms(line):
                key = term.casefold()
                if key not in scores:
                    scores[key] = [term, base, 0, order]
                    order += 1
                entry = scores[key]
                entry[1] = max(entry[1], base)
                if key not in seen_here:
                    entry[2] += 1
                    seen_here.add(key)
    ranked = sorted(
        scores.values(),
        key=lambda entry: (-(entry[1] + min(entry[2] - 1, 3)), entry[3]),
    )
    candidates = list(glossary or [])
    known = {term.casefold() for term in candidates}
    for term, _base, _count, _order in ranked:
        if term.casefold() in known:
            continue
        # Skip single words already covered by a chosen multi-word phrase.
        if " " not in term and any(
            term.casefold() in chosen.casefold().split() for chosen in candidates if " " in chosen
        ):
            continue
        candidates.append(term)
        known.add(term.casefold())
    result = ", ".join(candidates)
    if len(result) > maximum_characters:
        result = result[:maximum_characters].rsplit(",", 1)[0]
    return result
