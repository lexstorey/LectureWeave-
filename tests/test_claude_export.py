import tempfile
import unittest
from pathlib import Path
import zipfile

from lecture_to_docx import claude_output_paths, write_claude_packet


class ClaudeExportTests(unittest.TestCase):
    def test_companion_names_are_short_and_stable(self):
        output = Path("C:/Users/test/Downloads/") / (("Very long lecture title " * 8) + ".docx")
        first = claude_output_paths(output)
        second = claude_output_paths(output)
        self.assertEqual(first, second)
        self.assertTrue(all(len(path.name) < 70 for path in first.values()))
        self.assertTrue(all(path.parent == output.parent for path in first.values()))

    def test_packet_has_extractable_internal_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "slide.jpg"
            image.write_bytes(b"image")
            output = root / (("An exceptionally long lecture name " * 6) + ".docx")
            slides = [{
                "start": 0.0,
                "end": 10.0,
                "image": str(image),
                "segments": [],
                "comments": [],
                "canonical_index": 1,
            }]
            created = write_claude_packet(
                root / "lecture.mp4", slides, 10.0, output, "Full lecture title", "large-v3"
            )
            packet = Path(created["packet"])
            with zipfile.ZipFile(packet) as archive:
                names = archive.namelist()
                self.assertTrue(all(len(name) < 100 for name in names))
                self.assertFalse(any(output.stem in name for name in names))
                destination = root / ("deep-" * 20)
                archive.extractall(destination)
                self.assertTrue(any(destination.rglob("*.jpg")))


if __name__ == "__main__":
    unittest.main()
