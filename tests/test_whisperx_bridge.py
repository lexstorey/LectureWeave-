from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperx_bridge import enhance_with_whisperx, whisperx_environment_ready


class WhisperXBridgeTests(unittest.TestCase):
    def test_missing_environment_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(whisperx_environment_ready(Path(folder) / "python.exe"))

    def test_token_is_only_passed_in_the_child_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            work_dir = Path(folder)
            output = work_dir / "whisperx_aligned.json"
            output.write_text(json.dumps([]), encoding="utf-8")
            with patch("whisperx_bridge.subprocess.run") as run:
                run.return_value.returncode = 0
                enhance_with_whisperx(
                    work_dir / "lecture.mp4",
                    [{"start": 0.0, "end": 1.0, "text": "hello"}],
                    work_dir,
                    work_dir / "python.exe",
                    hf_token="hf_test_secret",
                )
            command = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["HF_TOKEN"], "hf_test_secret")
            self.assertNotIn("hf_test_secret", " ".join(command))
            self.assertNotIn("hf_test_secret", (work_dir / "whisperx_request.json").read_text())

    def test_no_token_is_removed_from_the_child_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"HF_TOKEN": "hf_legacy_secret"}
        ):
            work_dir = Path(folder)
            (work_dir / "whisperx_aligned.json").write_text("[]", encoding="utf-8")
            with patch("whisperx_bridge.subprocess.run") as run:
                run.return_value.returncode = 0
                enhance_with_whisperx(
                    work_dir / "lecture.mp4",
                    [{"start": 0.0, "end": 1.0, "text": "hello"}],
                    work_dir,
                    work_dir / "python.exe",
                )
            self.assertNotIn("HF_TOKEN", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
