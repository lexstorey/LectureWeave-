import tempfile
import unittest
import zipfile
from pathlib import Path

from content_enrichment import normalise_slide_text, unreliable_deck_text
from lecture_transcript_gui_qt import bundle_outputs


def garble(text):
    return text.replace("o", "i")


class DeckTextTests(unittest.TestCase):
    def test_broken_text_layer_falls_back_to_ocr(self):
        texts = [
            "Procedural fairness and the right to be heard",
            "Kompetenz-kompetenz: tribunal rules on its own jurisdiction",
            "Scope of the arbitration agreement and model clauses",
            "Litigation compared with arbitration costs",
        ]
        slides = [
            {"deck_slide_number": n, "deck_label": "A", "deck_text": garble(t),
             "slide_text": garble(t), "ocr_text": t}
            for n, t in enumerate(texts, 1)
        ] + [
            {"deck_slide_number": n, "deck_label": "B", "deck_text": t, "slide_text": t, "ocr_text": t}
            for n, t in enumerate(texts, 1)
        ]
        self.assertEqual({"A"}, unreliable_deck_text(slides))
        self.assertEqual(texts[0], slides[0]["slide_text"])
        self.assertIn("video OCR", slides[0]["text_source"])
        self.assertEqual(texts[0], slides[4]["slide_text"])

    def test_pdf_soft_hyphen_becomes_hyphen(self):
        self.assertEqual("hunter-gatherer", normalise_slide_text("hunter￾gatherer"))


class BundleTests(unittest.TestCase):
    def test_bundle_zips_files_and_removes_loose_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            files = [folder / "Lecture.md", folder / "Lecture.docx", folder / "sub" / "CLAUDE_INSTRUCTIONS.md"]
            for path in files:
                path.parent.mkdir(exist_ok=True)
                path.write_text(path.name, encoding="utf-8")
            bundle = bundle_outputs(folder / "Lecture.zip", files)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(
                    ["Lecture.md", "Lecture.docx", "CLAUDE_INSTRUCTIONS.md"], archive.namelist()
                )
            self.assertFalse(any(path.exists() for path in files))


if __name__ == "__main__":
    unittest.main()
