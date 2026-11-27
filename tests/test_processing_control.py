import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lecture_transcript_gui_qt import ProcessingThread, WheelSafeComboBox


class _WheelEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


class ProcessingControlTests(unittest.TestCase):
    def test_model_combo_ignores_mouse_wheel(self):
        event = _WheelEvent()
        WheelSafeComboBox.wheelEvent(None, event)
        self.assertTrue(event.ignored)

    def test_pre_cancelled_generation_removes_job_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "lecture.mp4"
            video.write_bytes(b"video")
            output = root / "notes.docx"
            output.write_bytes(b"existing document")
            worker = ProcessingThread(
                video,
                output,
                "Lecture",
                [],
                True,
                True,
                "large-v3",
                None,
                None,
                None,
            )
            cancelled = []
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.cancel()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "appdata")}):
                worker.run()
            stat = video.stat()
            fingerprint = hashlib.sha256(
                f"{video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:20]
            self.assertEqual([True], cancelled)
            self.assertFalse((root / "appdata" / "LectureTranscriptSlides" / "jobs" / fingerprint).exists())
            self.assertEqual(b"existing document", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
