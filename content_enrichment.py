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


def match_deck_pages(slides: list[dict[str, Any]], pages: list[DeckPage]) -> None:
    """Attach actual deck page numbers and native content to video appearances."""
    rendered = [(page, _image_features(page.image)) for page in pages if page.image]
    rendered = [(page, features) for page, features in rendered if features is not None]
    if rendered:
        for slide in slides:
            features = _image_features(slide["image"])
            if features is None:
                continue
            slide_hash, slide_hist = features
            scored: list[tuple[float, float, float, DeckPage]] = []
            for page, page_features in rendered:
                page_hash, page_hist = page_features
                distance = hash_distance(slide_hash, page_hash)
                histogram_penalty = max(
                    0.0, 1.0 - hsv_histogram_similarity(slide_hist, page_hist)
                ) * 18
                visual_score = distance + histogram_penalty
                text_score = float(token_set_ratio(slide.get("slide_text", ""), page.text))
                combined = visual_score + (100.0 - text_score) * 0.25
                scored.append((combined, visual_score, text_score, page))
            scored.sort(key=lambda item: item[0])
            combined, visual_score, text_score, page = scored[0]
            margin = scored[1][0] - combined if len(scored) > 1 else 100.0
            if (
                (text_score >= 55 and margin >= 3)
                or text_score >= 78
                or (visual_score <= 14 and margin >= 2)
            ):
                slide["deck_slide_number"] = page.number
                slide["deck_text"] = page.text
                slide["slide_text"] = page.text or slide.get("slide_text", "")
                slide["deck_image"] = page.image
                slide["text_source"] = "supplied slide pack"
        return

    # PowerPoint text is still useful without PowerPoint rendering.  Sequential
    # assignment is safe only when the distinct captured slides match the deck.
    canonical_ids = list(dict.fromkeys(
        int(slide.get("canonical_index", slide["index"])) for slide in slides
    ))
    # Native PowerPoint text can often be matched directly to video OCR even
    # without rendering PowerPoint. Use a confidence margin to avoid guessing.
    if pages:
        for slide in slides:
            scored = sorted(
                (
                    float(token_set_ratio(slide.get("slide_text", ""), page.text)),
                    page,
                )
                for page in pages
            )
            if not scored:
                continue
            score, page = scored[-1]
            second = scored[-2][0] if len(scored) > 1 else 0.0
            if score >= 62 and score - second >= 8:
                slide["deck_slide_number"] = page.number
                slide["deck_text"] = page.text
                slide["slide_text"] = page.text or slide.get("slide_text", "")
                slide["text_source"] = "supplied PowerPoint text"

    unmatched = [slide for slide in slides if not slide.get("deck_slide_number")]
    if pages and len(canonical_ids) == len(pages) and len(unmatched) == len(slides):
        mapping = dict(zip(canonical_ids, pages))
        for slide in slides:
            page = mapping[int(slide.get("canonical_index", slide["index"]))]
            slide["deck_slide_number"] = page.number
            slide["deck_text"] = page.text
            slide["slide_text"] = page.text or slide.get("slide_text", "")
            slide["text_source"] = "supplied PowerPoint text"


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


def transcription_vocabulary(slides: list[dict[str, Any]], maximum_characters: int = 1200) -> str:
    """Build a restrained terminology list from slide text for Whisper hotwords."""
    candidates: list[str] = []
    seen: set[str] = set()
    for slide in slides:
        text = str(slide.get("slide_text") or slide.get("deck_text") or "")
        for token in re.findall(
            r"\b[A-Za-z][A-Za-z’'\-]{3,}(?:\s+[A-Z][A-Za-z’'\-]{2,}){0,3}\b", text
        ):
            cleaned = re.sub(r"\s+", " ", token).strip(" -")
            key = cleaned.casefold()
            if key in seen or key in {"this", "that", "with", "from", "have", "will"}:
                continue
            seen.add(key)
            candidates.append(cleaned)
    result = ", ".join(candidates)
    return result[:maximum_characters].rsplit(",", 1)[0] if len(result) > maximum_characters else result

