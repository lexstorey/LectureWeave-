import logging
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import lecture_transcript_gui_qt as gui


class DeadSignal:
    def emit(self, _line):
        raise RuntimeError("Signal source has been deleted")


class ListSignal:
    def __init__(self):
        self.lines = []

    def emit(self, line):
        self.lines.append(line)


class LoggingTests(unittest.TestCase):
    def test_writer_ignores_deleted_signal(self):
        writer = gui.SignalWriter(DeadSignal())
        self.assertEqual(6, writer.write("hello\n"))

    def test_library_handlers_follow_the_current_run(self):
        logger = logging.getLogger("RapidOCR")
        old = gui.SignalWriter(DeadSignal())
        handler = logging.StreamHandler(old)
        logger.addHandler(handler)
        try:
            current_signal = ListSignal()
            current = gui.SignalWriter(current_signal)
            gui.route_library_logging(current)
            self.assertIs(current, handler.stream)
            self.assertEqual(logging.WARNING, logger.level)
            logger.warning("visible warning")
            self.assertEqual(["visible warning"], current_signal.lines)
        finally:
            logger.removeHandler(handler)


class NamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_output_name_follows_title_until_changed(self):
        window = gui.MainWindow()
        window.set_video(str(Path("/tmp/lectures/7bec7b11_261127T075842398Z.mp4")))
        self.assertTrue(window.output_edit.text().endswith("7bec7b11_261127T075842398Z Transcript and Slides.docx"))
        window.title_edit.setText("Recording 2")
        window.title_edit.textEdited.emit("Recording 2")
        self.assertEqual(Path("/tmp/lectures/Recording 2.docx"), Path(window.output_edit.text()))
        window.title_edit.setText("Day 5: part 2?")
        window.title_edit.textEdited.emit("Day 5: part 2?")
        self.assertEqual("Day 5 part 2.docx", Path(window.output_edit.text()).name)
        window.output_edit.textEdited.emit("/tmp/custom.docx")
        window.output_edit.setText("/tmp/custom.docx")
        window.title_edit.textEdited.emit("Another title")
        self.assertEqual("/tmp/custom.docx", window.output_edit.text())


if __name__ == "__main__":
    unittest.main()
