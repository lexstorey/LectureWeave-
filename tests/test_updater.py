import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from lecture_transcript_gui_qt import UpdateDownloadThread, release_asset_sha256, version_key


class UpdaterTests(unittest.TestCase):
    def test_parses_github_sha256_digest(self):
        digest = "A1" * 32
        self.assertEqual(digest.lower(), release_asset_sha256("sha256:" + digest))

    def test_rejects_missing_or_malformed_digest(self):
        for digest in ("", "A1" * 32, "sha256:1234", "sha512:" + "a" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(ValueError):
                    release_asset_sha256(digest)

    def test_compares_semantic_versions(self):
        self.assertGreater(version_key("v0.2.2"), version_key("0.2.1"))
        self.assertEqual((1, 4, 0), version_key("v1.4"))

    def test_downloads_and_verifies_installer_before_completing(self):
        content = b"small fake installer used only by the updater test"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as source:
            source.write(content)
            source_path = Path(source.name)
        worker = UpdateDownloadThread(
            source_path.as_uri(),
            "sha256:" + hashlib.sha256(content).hexdigest(),
            f"test-{os.getpid()}",
        )
        completed = []
        failed = []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        try:
            worker.run()
            self.assertFalse(failed)
            self.assertEqual([str(worker.destination)], completed)
            self.assertEqual(content, worker.destination.read_bytes())
        finally:
            source_path.unlink(missing_ok=True)
            worker.destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
