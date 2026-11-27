import unittest

from content_enrichment import DeckPage, match_deck_pages, slide_reference


class MultiDeckTests(unittest.TestCase):
    def test_matches_slides_across_two_text_decks(self):
        pages = [
            DeckPage(1, "History of dispute resolution around the campfire", deck="A"),
            DeckPage(2, "Code of Hammurabi royal law in stone", deck="A"),
            DeckPage(1, "International commercial arbitration overview", deck="B"),
            DeckPage(2, "New York Convention enforcement of foreign awards", deck="B"),
        ]
        slides = [
            {"index": 1, "canonical_index": 1, "image": "missing.jpg",
             "slide_text": "Code of Hammurabi Royal Law in Stone"},
            {"index": 2, "canonical_index": 2, "image": "missing.jpg",
             "slide_text": "New York Convention: enforcement of foreign awards"},
            {"index": 3, "canonical_index": 3, "image": "missing.jpg",
             "slide_text": "Joshua (host camera)"},
        ]
        match_deck_pages(slides, pages)
        self.assertEqual(("A", 2), (slides[0]["deck_label"], slides[0]["deck_slide_number"]))
        self.assertEqual(("B", 2), (slides[1]["deck_label"], slides[1]["deck_slide_number"]))
        self.assertNotIn("deck_slide_number", slides[2])
        self.assertIn(slides[0]["match_confidence"], {"high", "medium"})
        self.assertEqual("Deck B slide 2", slide_reference(slides[1], multiple_decks=True))
        self.assertEqual("Deck slide 2", slide_reference(slides[1], multiple_decks=False))
        self.assertIsNone(slide_reference(slides[2], multiple_decks=True))


if __name__ == "__main__":
    unittest.main()
