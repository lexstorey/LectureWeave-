import unittest

from lecture_to_docx import parse_chat_comments


class ChatParserTests(unittest.TestCase):
    def test_parses_original_bulleted_markdown_export(self):
        pasted = """- **AMANDA SCHWARTZ**

  **00:47**

  Yep sure can
- DAO ROCHVARGER-WONG

  **02:44**

  yes. thanks
"""

        comments = parse_chat_comments(pasted)

        self.assertEqual(2, len(comments))
        self.assertEqual("AMANDA SCHWARTZ", comments[0]["author"])
        self.assertEqual(47, comments[0]["start"])
        self.assertEqual("Yep sure can", comments[0]["text"])
        self.assertEqual("DAO ROCHVARGER-WONG", comments[1]["author"])
        self.assertEqual(164, comments[1]["start"])
        self.assertEqual("yes. thanks", comments[1]["text"])

    def test_parses_plain_blackboard_blocks_and_hour_timestamps(self):
        pasted = """EMILY MILES

**00:11**

always convenient

JOCK PALFREEMAN

**10:13**

What is interesting is that this message wraps
onto another copied line.&#x20;

HASAN EMAD

**01:15:13**

do you have to be lawyer to represent a party in arbitration ?
"""

        comments = parse_chat_comments(pasted)

        self.assertEqual(3, len(comments))
        self.assertEqual(("EMILY MILES", 11, "always convenient"), (
            comments[0]["author"], comments[0]["start"], comments[0]["text"]
        ))
        self.assertEqual(
            "What is interesting is that this message wraps onto another copied line.",
            comments[1]["text"],
        )
        self.assertEqual(4513, comments[2]["start"])

    def test_parses_plain_text_after_rich_text_loses_bold_markers(self):
        pasted = """EMILY MILES
00:11
always convenient
STEPHANIE RAMSEY
07:18
arbitration
"""

        comments = parse_chat_comments(pasted)

        self.assertEqual(2, len(comments))
        self.assertEqual("STEPHANIE RAMSEY", comments[1]["author"])
        self.assertEqual(438, comments[1]["start"])
        self.assertEqual("arbitration", comments[1]["text"])


if __name__ == "__main__":
    unittest.main()
