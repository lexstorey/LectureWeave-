import tempfile
import unittest
from pathlib import Path

from claude_pack import (
    Pseudonymiser,
    build_claude_pack,
    build_paragraphs,
    coverage_summary,
    is_social,
)


def seg(start, end, text, **extra):
    return dict({"start": start, "end": end, "text": text}, **extra)


class ClaudePackTests(unittest.TestCase):
    def test_social_chat_is_dropped_but_short_answers_kept(self):
        for text in ("Thanks!", "thank you so much", "hahaha", "🙂", "Hi everyone", "can you hear me?"):
            self.assertTrue(is_social(text), text)
        for text in ("arbitration", "yes", "Is that binding?", "Pax Romanus"):
            self.assertFalse(is_social(text), text)

    def test_pseudonymiser_hides_full_names_and_linked_first_names(self):
        comments = [{"author": "JOCK SMITH"}, {"author": "Anna Lee"}, {"author": "Host Person"}]
        people = Pseudonymiser(comments, True, staff_text="Welcome from Host Person")
        self.assertEqual("STUDENT A", people.author("Jock Smith"))
        self.assertEqual("STAFF (Host Person)", people.author("HOST PERSON"))
        self.assertEqual("Ask [Student A]", people.text("Ask Jock Smith", set()))
        self.assertEqual("Answering Jock", people.text("Answering Jock", set()))
        self.assertEqual("Answering [Student A]", people.text("Answering Jock", {"jock smith"}))

    def test_paragraphs_merge_speech_and_drop_fillers(self):
        paragraphs = build_paragraphs(
            [seg(0, 5, "Um, the first point."), seg(5.5, 12, "Uh and the second."),
             seg(40, 45, "After a long pause.")],
            set(),
        )
        self.assertEqual(2, len(paragraphs))
        self.assertEqual("the first point. and the second.", paragraphs[0]["text"])

    def test_low_confidence_words_are_marked(self):
        words = [
            {"start": 0, "end": 1, "word": " the", "probability": 0.9},
            {"start": 1, "end": 2, "word": " Bunsertral,", "probability": 0.2},
            {"start": 2, "end": 3, "word": " UNCITRAL", "probability": 0.2},
        ]
        paragraphs = build_paragraphs([seg(0, 3, "x", words=words)], {"uncitral"})
        self.assertEqual("the ⟦Bunsertral?⟧, UNCITRAL", paragraphs[0]["text"])

    def test_coverage_groups_slide_ranges(self):
        slides = [
            {"deck_slide_number": 1, "deck_label": "A", "segments": [seg(0, 9, "word " * 30)]},
            {"deck_slide_number": 2, "deck_label": "A", "segments": [seg(9, 10, "brief")]},
        ]
        summary = coverage_summary([{"label": "A", "pages": 4}], slides, [])
        self.assertEqual("discussed: A1 · brief: A2 · not shown: A3–A4", summary)

    def test_pack_contains_header_slides_and_linked_chat(self):
        slides = [{
            "index": 1, "canonical_index": 1, "start": 0.0, "end": 60.0,
            "deck_slide_number": 4, "deck_label": "A", "match_confidence": "high",
            "slide_text": "Code of Hammurabi\nRoyal law in stone",
            "segments": [seg(1, 20, "What would it be? Answering Jock, it is arbitration.")],
            "comments": [
                {"author": "JOCK SMITH", "start": 10.0, "text": "arbitration"},
                {"author": "ANNA LEE", "start": 12.0, "text": "thanks!"},
            ],
        }]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pack.md"
            summary = build_claude_pack(
                output_path=path, title="Lecture", video_name="lecture.mp4", duration=60.0,
                slides=slides, omitted=[], decks=[{"label": "A", "name": "deck.pdf", "pages": 5}],
                comments_total=2, chat_clock="not adjusted", model_name="large-v3",
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn('<slide id="A4" deck="A" slide="4" appearance="1"', text)
        self.assertIn('match_conf="high"', text)
        self.assertIn("↩chat-1", text)
        self.assertIn("Answering [Student A]", text)
        self.assertIn("chat-1 STUDENT A: arbitration", text)
        self.assertNotIn("thanks!", text)
        self.assertNotIn("Jock", text)
        self.assertIn("not shown: A1–A3, A5", text)
        self.assertEqual(1, summary["chat_kept"])


if __name__ == "__main__":
    unittest.main()
