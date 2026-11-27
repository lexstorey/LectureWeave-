import tempfile
import unittest
from pathlib import Path

import docx

from content_enrichment import normalise_slide_text
from lecture_to_docx import build_docx, parse_chat_comments, xml_safe_text


class XmlSafetyTests(unittest.TestCase):
    def test_pdf_line_break_hyphen_and_controls_are_removed(self):
        self.assertEqual("arbi-tration", xml_safe_text("arbi\x02tration"))
        self.assertEqual("ab", xml_safe_text("a\x00\x0bb"))
        self.assertEqual("keep\nlines\tand tabs", xml_safe_text("keep\nlines\tand tabs"))
        self.assertEqual("Kompetenz-\nkompetenz", normalise_slide_text("Kompetenz\x02\nkompetenz\x07"))

    def test_chat_controls_are_removed(self):
        comments = parse_chat_comments("- ANNA\n  00:10\n  ok\x08 then")
        self.assertEqual("ok then", comments[0]["text"])

    def test_word_document_survives_control_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out.docx"
            image = Path(folder) / "slide.png"
            from PIL import Image
            Image.new("RGB", (64, 36), "white").save(image)
            slides = [{
                "index": 1, "start": 0.0, "end": 10.0, "image": str(image), "deck_slide_number": 3,
                "slide_text": "Separa\x02bility\x0c", "text_source": "supplied slide pack",
                "segments": [{"start": 0.0, "end": 5.0, "text": "Spoken\x00 text"}],
                "comments": [{"author": "A", "timestamp": "00:01", "start": 1.0, "text": "q\x1f?"}],
            }]
            build_docx(Path("lecture.mp4"), slides, 10.0, output, "cpu", "Title\x02")
            text = "\n".join(p.text for p in docx.Document(str(output)).paragraphs)
        self.assertIn("Separa-bility", text)
        self.assertIn("Spoken text", text)


if __name__ == "__main__":
    unittest.main()
