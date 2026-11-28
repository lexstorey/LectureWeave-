"""LectureWeave+ entry point: show the opening animation while the app loads.

The main module imports OpenCV, NumPy, RapidOCR and python-docx, which takes
seconds on a cold start. This launcher imports only PySide6, shows the
opening animation at once, loads the main module on a background thread and
builds the main window when it is ready. The animation fades only once both
it and the window are done, so it covers loading instead of adding a wait.
"""

from __future__ import annotations

import sys
import threading

TEST_FLAGS = ("--self-test", "--model-cache-test", "--content-feature-test")


def load_main_module(result: dict) -> None:
    try:
        import lecture_transcript_gui_qt

        result["module"] = lecture_transcript_gui_qt
    except BaseException as exc:  # reported on the main thread
        result["error"] = exc


def main() -> None:
    if any(flag in sys.argv for flag in TEST_FLAGS):
        import lecture_transcript_gui_qt

        lecture_transcript_gui_qt.main()
        return

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    settings = QSettings("LectureWeave", "LectureWeavePlus")
    animate = settings.value("ui/startup_animation", True, type=bool)
    sounds = settings.value("ui/sounds", True, type=bool)

    splash = None
    if animate:
        from splash import StartupSplash

        splash = StartupSplash()
        splash.start()
        if sounds:
            import app_sounds

            app_sounds.play("startup")

    result: dict = {}
    # Let the animation's timer run between steps of the import.
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.001)
    loader = threading.Thread(target=load_main_module, args=(result,), daemon=True)
    loader.start()
    windows: list = []

    def check_loaded() -> None:
        if loader.is_alive():
            return
        poll.stop()
        sys.setswitchinterval(previous_interval)
        if "error" in result:
            if splash is not None:
                splash.close()
            QMessageBox.critical(None, "LectureWeave+", f"LectureWeave+ could not start:\n\n{result['error']}")
            app.exit(1)
            return
        module = result["module"]
        module.configure_application(app)
        window = module.MainWindow()
        windows.append(window)
        if splash is not None:
            splash.hand_over(window)
        else:
            window.show()

    poll = QTimer()
    poll.setInterval(20)
    poll.timeout.connect(check_loaded)
    poll.start()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
