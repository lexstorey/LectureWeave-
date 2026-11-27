from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperx_bridge import (
    default_whisperx_python,
    enhance_with_whisperx,
    inspect_whisperx_environment,
    prepare_whisperx_environment,
    whisperx_environment_ready,
)


class WhisperXBridgeTests(unittest.TestCase):
    def test_default_environment_uses_the_private_app_folder(self):
        self.assertEqual(default_whisperx_python().name.lower(), "python.exe")
        self.assertIn("LectureWeavePlus", str(default_whisperx_python()))

    def test_missing_environment_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(whisperx_environment_ready(Path(folder) / "python.exe"))

    def test_environment_inspection_reports_cuda_details(self):
        with tempfile.TemporaryDirectory() as folder:
            python = Path(folder) / "python.exe"
            python.touch()
            with patch("whisperx_bridge.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = (
                    '{"ready": true, "torch": "2.8.0+cu128", '
                    '"cuda_build": "12.8", "cuda_available": true, "gpu": "RTX"}\n'
                )
                result = inspect_whisperx_environment(python)
            self.assertTrue(result["ready"])
            self.assertTrue(result["cuda_available"])
            self.assertEqual(result["gpu"], "RTX")

    def test_token_is_only_passed_in_the_child_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            work_dir = Path(folder)
            output = work_dir / "whisperx_aligned.json"
            output.write_text(json.dumps([]), encoding="utf-8")
            with patch("whisperx_bridge.prepare_whisperx_environment"), patch(
                "whisperx_bridge.subprocess.run"
            ) as run:
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
            with patch("whisperx_bridge.prepare_whisperx_environment"), patch(
                "whisperx_bridge.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                enhance_with_whisperx(
                    work_dir / "lecture.mp4",
                    [{"start": 0.0, "end": 1.0, "text": "hello"}],
                    work_dir,
                    work_dir / "python.exe",
                )
            self.assertNotIn("HF_TOKEN", run.call_args.kwargs["env"])

    def test_private_ffmpeg_is_exposed_under_expected_command_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "ffmpeg-versioned.exe"
            source.write_bytes(b"private ffmpeg")
            environment = {"PATH": "existing-path"}
            with patch("whisperx_bridge.whisperx_ffmpeg_path", return_value=source):
                prepare_whisperx_environment(
                    root / "python.exe", root / "work", environment
                )
            alias = root / "work" / "whisperx-bin" / "ffmpeg.exe"
            self.assertEqual(alias.read_bytes(), b"private ffmpeg")
            self.assertTrue(environment["PATH"].startswith(str(alias.parent)))


if __name__ == "__main__":
    unittest.main()
