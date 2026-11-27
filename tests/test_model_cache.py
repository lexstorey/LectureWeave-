from pathlib import Path
import os
import tempfile
import unittest

from lecture_to_docx import (
    materialize_whisper_snapshot,
    whisper_model_files_complete,
    whisper_model_is_cached,
)


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

    def test_materializes_a_snapshot_without_reparse_points(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo_cache = root / "models--owner--model"
            snapshot = repo_cache / "snapshots" / "revision"
            destination = root / "trusted" / "turbo"
            snapshot.mkdir(parents=True)
            for name in ("config.json", "model.bin", "tokenizer.json"):
                (snapshot / name).write_bytes((name + " data").encode())

            self.assertTrue(materialize_whisper_snapshot(snapshot, destination, repo_cache))
            self.assertTrue(whisper_model_files_complete(destination))
            self.assertFalse((destination / "model.bin").is_symlink())
            self.assertEqual(b"model.bin data", (destination / "model.bin").read_bytes())

    def test_materializes_a_two_layer_xet_shared_blob(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo_cache = root / "models--owner--model"
            snapshot = repo_cache / "snapshots" / "revision"
            repo_blobs = repo_cache / "blobs"
            shared_blob = root / "blobs" / "28" / "shared-model"
            destination = root / "trusted" / "large-v3"
            snapshot.mkdir(parents=True)
            repo_blobs.mkdir(parents=True)
            shared_blob.parent.mkdir(parents=True)
            shared_blob.write_bytes(b"shared model data")
            try:
                os.symlink(
                    str(Path("..") / ".." / "blobs" / "28" / "shared-model"),
                    repo_blobs / "model-hash",
                )
                os.symlink(
                    str(Path("..") / ".." / "blobs" / "model-hash"),
                    snapshot / "model.bin",
                )
            except OSError as exc:
                self.skipTest(f"Windows symbolic links unavailable: {exc}")
            (snapshot / "config.json").write_bytes(b"config")
            (snapshot / "tokenizer.json").write_bytes(b"tokenizer")

            self.assertTrue(materialize_whisper_snapshot(snapshot, destination, repo_cache))
            self.assertEqual(b"shared model data", (destination / "model.bin").read_bytes())
            self.assertFalse((destination / "model.bin").is_symlink())

    def test_finds_a_complete_trusted_model_without_downloading(self):
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder) / "trusted" / "large-v3"
            model_dir.mkdir(parents=True)
            for name in ("config.json", "model.bin", "tokenizer.json"):
                (model_dir / name).write_bytes(b"complete")
            self.assertTrue(whisper_model_is_cached("large-v3", Path(folder)))

    def test_reports_an_uncached_model_without_downloading(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(whisper_model_is_cached("large-v3", Path(folder)))


if __name__ == "__main__":
    unittest.main()
