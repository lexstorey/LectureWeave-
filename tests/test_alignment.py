import unittest

from lecture_to_docx import align_segments, split_at_slide_boundaries


def words(*items):
    return [{"start": s, "end": e, "word": w} for s, e, w in items]


class BoundarySplitTests(unittest.TestCase):
    def test_splits_at_sentence_end_near_slide_change(self):
        segment = {
            "start": 8.0, "end": 14.0, "text": "End of point. Next slide begins",
            "words": words((8, 9, " End"), (9, 10, " of"), (10, 10.8, " point."),
                           (11, 12, " Next"), (12, 13, " slide"), (13, 14, " begins")),
        }
        pieces = split_at_slide_boundaries(segment, [11.5])
        self.assertEqual(["End of point.", "Next slide begins"], [p["text"] for p in pieces])

    def test_keeps_segment_whole_without_nearby_sentence_end(self):
        segment = {
            "start": 0.0, "end": 20.0, "text": "one long clause",
            "words": words((0, 1, " one"), (10, 11, " long"), (19, 20, " clause")),
        }
        pieces = split_at_slide_boundaries(segment, [10.5])
        self.assertEqual(1, len(pieces))
        self.assertTrue(pieces[0]["straddles"])

    def test_align_files_each_sentence_under_its_slide(self):
        slides = [
            {"index": 1, "start": 0.0, "end": 11.5, "alignment_start": 0.0},
            {"index": 2, "start": 11.5, "end": 30.0, "alignment_start": 11.5},
        ]
        segment = {
            "start": 2.0, "end": 14.0, "text": "First idea here. Second idea",
            "words": words((2, 3, " First"), (3, 4, " idea"), (4, 11, " here."),
                           (11.2, 12, " Second"), (12, 14, " idea")),
        }
        align_segments(slides, [segment])
        self.assertEqual("First idea here.", slides[0]["segments"][0]["text"])
        self.assertEqual("Second idea", slides[1]["segments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
