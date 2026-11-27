import unittest

from content_enrichment import (
    DeckPage,
    match_deck_pages,
    normalise_slide_text,
    transcription_vocabulary,
)


class ContentEnrichmentTests(unittest.TestCase):
    def test_normalises_native_slide_text(self):
        self.assertEqual(
            "Native Title\nSection 18",
            normalise_slide_text(" Native  Title \n\n Section 18 "),
        )

    def test_builds_deduplicated_vocabulary(self):
        slides = [
            {"slide_text": "International Commercial Arbitration\nCameron Ford"},
            {"slide_text": "Cameron Ford\nNew York Convention"},
        ]
        vocabulary = transcription_vocabulary(slides)
        self.assertIn("International Commercial Arbitration", vocabulary)
        self.assertEqual(1, vocabulary.count("Cameron Ford"))

    def test_matches_powerpoint_native_text_to_video_ocr(self):
        pages = [
            DeckPage(1, "Introduction to mediation and dispute resolution"),
            DeckPage(2, "Section 48 Local Court Act mediation conference"),
            DeckPage(3, "Concluding questions"),
        ]
        slides = [
            {
                "index": 1,
                "canonical_index": 1,
                "image": "missing.jpg",
                "slide_text": "Section 48 Local Court Act mediation conference",
            }
        ]
        match_deck_pages(slides, pages)
        self.assertEqual(2, slides[0]["deck_slide_number"])
        self.assertEqual(pages[1].text, slides[0]["slide_text"])


if __name__ == "__main__":
    unittest.main()
