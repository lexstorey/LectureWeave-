import tempfile
import unittest
from pathlib import Path

from content_enrichment import load_glossary, transcription_vocabulary

BOILERPLATE = "Click on Insert > new slide to choose your cover, then delete this page"


class VocabularyTests(unittest.TestCase):
    def slides(self, bodies):
        return [
            {"canonical_index": index, "slide_text": f"{BOILERPLATE}\n{body}"}
            for index, body in enumerate(bodies)
        ]

    def test_template_text_repeated_on_most_slides_is_ignored(self):
        vocabulary = transcription_vocabulary(
            self.slides([
                "The UNCITRAL Model Law",
                "New York Convention",
                "Arbitration institutions: ACICA, SIAC",
                "Göbekli Tepe",
            ])
        )
        self.assertNotIn("Click", vocabulary)
        self.assertNotIn("Insert", vocabulary)
        self.assertNotIn("delete", vocabulary)

    def test_specialist_terms_rank_before_ordinary_words(self):
        vocabulary = transcription_vocabulary(
            self.slides([
                "basic legal right which guarantees every person a hearing",
                "The UNCITRAL Model Law and the New York Convention",
                "Arbitration institutions: ACICA, SIAC, HKIAC",
                "Göbekli Tepe",
            ])
        )
        terms = [term.strip() for term in vocabulary.split(",")]
        for expected in ("UNCITRAL Model Law", "New York Convention", "ACICA", "Göbekli Tepe"):
            self.assertIn(expected, terms)
        self.assertNotIn("basic", terms)
        self.assertNotIn("which", terms)
        self.assertLess(terms.index("ACICA"), terms.index("guarantees"))

    def test_phrases_do_not_run_across_punctuation(self):
        vocabulary = transcription_vocabulary([{"slide_text": "Institutions: ACICA, SIAC, HKIAC"}])
        self.assertNotIn("ACICA SIAC", vocabulary)

    def test_glossary_terms_come_first(self):
        vocabulary = transcription_vocabulary(
            [{"slide_text": "New York Convention"}], glossary=["TBARS", "Wyvill"]
        )
        self.assertTrue(vocabulary.startswith("TBARS, Wyvill"))

    def test_load_glossary_ignores_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "glossary.txt"
            path.write_text("# names\nWyvill\n\nTBARS  # acronym\nwyvill\n", encoding="utf-8")
            self.assertEqual(["Wyvill", "TBARS"], load_glossary(path))
        self.assertEqual([], load_glossary(None))


if __name__ == "__main__":
    unittest.main()
