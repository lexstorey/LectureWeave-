import unittest

from lecture_to_docx import bounded_whisper_hotwords


class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return FakeEncoding(list(range(len(text.split()))))

    def decode(self, ids):
        return " ".join(f"term{token}" for token in ids)


class FakeModel:
    hf_tokenizer = FakeTokenizer()


class WhisperPromptTests(unittest.TestCase):
    def test_short_hotwords_are_preserved(self):
        self.assertEqual(
            bounded_whisper_hotwords(FakeModel(), "court based mediation"),
            "court based mediation",
        )

    def test_long_hotwords_are_limited_by_tokens(self):
        source = " ".join(f"word{index}" for index in range(200))
        result = bounded_whisper_hotwords(FakeModel(), source, maximum_tokens=96)
        self.assertEqual(len(result.split()), 96)

    def test_empty_hotwords_remain_empty(self):
        self.assertEqual(bounded_whisper_hotwords(FakeModel(), "   "), "")


if __name__ == "__main__":
    unittest.main()
