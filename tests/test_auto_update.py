import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

import lecture_transcript_gui_qt as gui


def release(tag):
    return {
        "tag_name": tag,
        "assets": [{
            "name": "LectureWeavePlus-Setup.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
            "digest": "sha256:" + "a" * 64,
        }],
    }


class AutoUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_installer_runs_invisibly(self):
        self.assertIn("/VERYSILENT", gui.installer_arguments(True))
        self.assertIn("/RELAUNCH=1", gui.installer_arguments(True))
        self.assertIn("/RELAUNCH=0", gui.installer_arguments(False))

    def test_background_check_downloads_newer_release_without_prompting(self):
        window = gui.MainWindow()
        window.update_auto = True
        with mock.patch.object(window, "start_update_download") as download, \
                mock.patch.object(QMessageBox, "question") as question, \
                mock.patch.object(QMessageBox, "information") as information:
            window.update_check_complete(release("v99.0.0"))
            window.update_check_complete(release(f"v{gui.APP_VERSION}"))
        download.assert_called_once()
        question.assert_not_called()
        information.assert_not_called()

    def test_ready_update_shows_button_and_installs_on_close(self):
        window = gui.MainWindow()
        window.update_auto = True
        with tempfile.TemporaryDirectory() as folder:
            installer = Path(folder) / "setup.exe"
            installer.write_bytes(b"x")
            window.update_download_worker = mock.Mock(tag="v99.0.0")
            window.update_download_complete(str(installer))
            self.assertFalse(window.update_ready_button.isHidden())
            self.assertIn("99.0.0", window.update_ready_button.text())
            with mock.patch.object(gui.QProcess, "startDetached", return_value=(True, 1)) as start:
                window.close()
            start.assert_called_once()
            self.assertIn("/RELAUNCH=0", start.call_args[0][1])

    def test_background_failures_stay_quiet(self):
        window = gui.MainWindow()
        window.update_auto = True
        with mock.patch.object(QMessageBox, "warning") as warning:
            window.update_check_failed("offline")
            window.update_download_failed("bad checksum")
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
