from pathlib import Path
import tempfile
import unittest

from whisperx_bridge import whisperx_environment_ready


class WhisperXBridgeTests(unittest.TestCase):
    def test_missing_environment_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(whisperx_environment_ready(Path(folder) / "python.exe"))


if __name__ == "__main__":
    unittest.main()
