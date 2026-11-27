from pathlib import Path
import tempfile
import unittest

from lecture_to_docx import whisper_model_files_complete


class ModelCacheTests(unittest.TestCase):
    def test_rejects_incomplete_model_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            self.assertFalse(whisper_model_files_complete(model_dir))

    def test_accepts_nonempty_required_model_files(self):
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder)
            for name in ("config.json", "model.bin", "tokenizer.json"):
                (model_dir / name).write_bytes(b"complete")
            self.assertTrue(whisper_model_files_complete(model_dir))

    def test_rejects_zero_length_model_file(self):
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder)
            (model_dir / "config.json").write_bytes(b"complete")
            (model_dir / "model.bin").write_bytes(b"")
            (model_dir / "tokenizer.json").write_bytes(b"complete")
            self.assertFalse(whisper_model_files_complete(model_dir))


if __name__ == "__main__":
    unittest.main()
