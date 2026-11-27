import unittest

from chat_alignment import ChatClockFit, apply_chat_clock, estimate_chat_clock


def segment(start, text):
    return {"start": start, "end": start + 10, "text": text}


class ChatClockTests(unittest.TestCase):
    def build(self, shift):
        filler = [segment(t, "general discussion about the procedure and the courts") for t in range(0, 4000, 20)]
        echoes = [
            (600, "Answering Jock, that is a fair question", {"author": "JOCK SMITH", "text": "Why go to court at all?"}),
            (1500, "Yes, extremely costly in big projects", {"author": "ANNA LEE", "text": "can be very costly?"}),
            (2400, "Hasan asks about TBARS and adjudication", {"author": "HASAN ALI", "text": "What is TBARS"}),
            (3300, "No, Priya, you need not be a lawyer", {"author": "PRIYA RAO", "text": "do you have to be lawyer?"}),
        ]
        segments = sorted(filler + [segment(t, text) for t, text, _ in echoes], key=lambda s: s["start"])
        comments = [dict(c, start=t - shift) for t, _, c in echoes]
        return segments, comments

    def test_detects_late_chat_clock(self):
        segments, comments = self.build(shift=-150)
        fit = estimate_chat_clock(comments, segments)
        self.assertEqual("auto", fit.method)
        self.assertAlmostEqual(-150, fit.shift_at(1500), delta=12)
        adjusted = apply_chat_clock(comments, fit)
        for original, moved in zip(comments, adjusted):
            self.assertLess(moved["start"], original["start"])
            self.assertEqual(original["start"], moved["original_start"])

    def test_no_echoes_leaves_chat_unchanged(self):
        segments = [segment(t, "general discussion") for t in range(0, 1000, 20)]
        comments = [{"author": "A B", "text": "thanks", "start": 100.0}]
        fit = estimate_chat_clock(comments, segments)
        self.assertEqual("none", fit.method)
        self.assertEqual(100.0, apply_chat_clock(comments, fit)[0]["start"])

    def test_manual_offset_applies_without_lag(self):
        fit = ChatClockFit(-120.0, 0.0, 0, "manual")
        moved = apply_chat_clock([{"author": "A", "text": "q", "start": 300.0}], fit)
        self.assertEqual(180.0, moved[0]["start"])
        self.assertEqual("-2:00 (manual)", fit.describe())


if __name__ == "__main__":
    unittest.main()
