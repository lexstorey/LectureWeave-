from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lecture_to_docx import (
    align_segments,
    build_docx,
    detect_slides,
    omit_incidental_slide_appearances,
    parse_chat_comments,
    transcribe,
    write_transcript_files,
)


APP_NAME = "LectureWeave+"
APP_VERSION = "0.2.0"
REPOSITORY_URL = "https://github.com/lexstorey/LectureWeave-"
LATEST_RELEASE_API = "https://api.github.com/repos/lexstorey/LectureWeave-/releases/latest"
PURPLE = "#4A148C"
PURPLE_DARK = "#32105D"
PURPLE_LIGHT = "#F2ECFA"
BACKGROUND = "#F5F4F8"
TEXT = "#26232B"
BLACKBOARD_VIDEO_COMMAND = 'copy(document.querySelector("video")?.currentSrc)'

SELF_TEST_COMMENTS = """- **AMANDA SCHWARTZ**

  **00:47**

  Yep sure can
- DAO ROCHVARGER-WONG

  **02:44**

  yes. thanks
"""


def resource_path(relative: str) -> Path:
    """Return a bundled PyInstaller resource or a source-tree resource."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def version_key(value: str) -> tuple[int, int, int]:
    numbers = [int(number) for number in re.findall(r"\d+", value)[:3]]
    return tuple((numbers + [0, 0, 0])[:3])


class DropLineEdit(QLineEdit):
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class DropArea(QLabel):
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__("DROP VIDEO HERE")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setMinimumHeight(48)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class SignalWriter(io.TextIOBase):
    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self.buffer = ""

    def write(self, value: str) -> int:
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.signal.emit(line.strip())
        return len(value)

    def flush(self) -> None:
        if self.buffer.strip():
            self.signal.emit(self.buffer.strip())
        self.buffer = ""


class UpdateCheckThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            request = urllib.request.Request(
                LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"LectureWeavePlus/{APP_VERSION}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.completed.emit(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.failed.emit("No published LectureWeave+ release is available yet.")
            else:
                self.failed.emit(f"GitHub returned HTTP {exc.code} while checking for updates.")
        except Exception as exc:
            self.failed.emit(f"Could not check for updates: {exc}")


class ProcessingThread(QThread):
    status = Signal(str)
    log = Signal(str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        video: Path,
        output: Path,
        title: str,
        comments: list[dict],
        reuse_transcript: bool,
        omit_brief_slides: bool,
    ) -> None:
        super().__init__()
        self.video = video
        self.output = output
        self.title = title
        self.comments = comments
        self.reuse_transcript = reuse_transcript
        self.omit_brief_slides = omit_brief_slides

    def run(self) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_root = app_data / "LectureTranscriptSlides"
        stat = self.video.stat()
        fingerprint = hashlib.sha256(
            f"{self.video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:20]
        work_dir = cache_root / "jobs" / fingerprint
        slides_dir = work_dir / "slides"
        transcript_dir = work_dir / "transcript"
        model_cache = cache_root / "models"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.output.parent.mkdir(parents=True, exist_ok=True)

        writer = SignalWriter(self.log)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                self.status.emit("Detecting stable slide changes…")
                slides, duration = detect_slides(self.video, slides_dir)
                print(f"Detected {len(slides)} stable slide appearances")

                transcript_json = transcript_dir / "transcript.json"
                if self.reuse_transcript and transcript_json.exists():
                    self.status.emit("Loading the cached local transcript…")
                    segments = json.loads(transcript_json.read_text(encoding="utf-8"))
                    device = "reused"
                    print(f"Loaded {len(segments)} cached speech segments")
                else:
                    self.status.emit(
                        "Transcribing locally—this is the longest step, especially on CPU…"
                    )
                    segments, device = transcribe(
                        self.video,
                        work_dir,
                        "turbo",
                        model_cache=model_cache,
                    )
                    write_transcript_files(segments, transcript_dir)
                    print(f"Transcribed {len(segments)} timestamped speech segments")

                align_segments(slides, segments, self.comments)
                omitted_slides: list[dict] = []
                if self.omit_brief_slides:
                    slides, omitted_slides = omit_incidental_slide_appearances(slides)
                    print(
                        f"Omitted {len(omitted_slides)} brief navigation slide appearances"
                    )

                alignment = [
                    {
                        key: value
                        for key, value in slide.items()
                        if key
                        in {
                            "index",
                            "start",
                            "end",
                            "capture_time",
                            "image",
                            "segments",
                            "comments",
                        }
                    }
                    for slide in slides
                ]
                (work_dir / "alignment.json").write_text(
                    json.dumps(alignment, indent=2), encoding="utf-8"
                )

                self.status.emit("Creating and formatting the Word document…")
                build_docx(
                    self.video,
                    slides,
                    duration,
                    self.output,
                    device,
                    self.title,
                    len(omitted_slides),
                )
                print(f"Imported {len(self.comments)} chat comments")
                print(f"Created {self.output}")
            writer.flush()
            self.completed.emit(str(self.output))
        except Exception as exc:
            writer.flush()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon_path = resource_path("assets/lectureweave-plus.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1000, 850)
        self.setMinimumSize(820, 680)
        self.worker: ProcessingThread | None = None
        self.update_worker: UpdateCheckThread | None = None
        self.last_output: Path | None = None

        self.setStyleSheet(
            f"""
            QMainWindow, QScrollArea, QWidget#scrollBody {{ background: {BACKGROUND}; }}
            QFrame#card {{ background: white; border: 1px solid #E4E0E8; border-radius: 9px; }}
            QLabel {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QLabel#headerTitle {{ color: white; font-size: 22pt; font-weight: 600; }}
            QLabel#headerSubtitle {{ color: #E8DEF2; font-size: 10pt; }}
            QLabel#section {{ color: {TEXT}; font-size: 11pt; font-weight: 600; }}
            QLabel#hint {{ color: #68626E; font-size: 9pt; }}
            QLabel#count {{ color: {PURPLE}; font-size: 9pt; font-weight: 600; }}
            QLabel#dropArea {{ background: {PURPLE_LIGHT}; color: {PURPLE}; border: 1px solid #BFA8D7; border-radius: 6px; font-weight: 600; }}
            QFrame#consoleHelp {{ background: #FAF7FD; border: 1px solid #D8C8E7; border-radius: 6px; }}
            QLineEdit, QTextEdit {{ background: white; color: {TEXT}; border: 1px solid #CFC9D5; border-radius: 5px; padding: 7px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QLineEdit:focus, QTextEdit:focus {{ border: 2px solid {PURPLE}; }}
            QPushButton {{ background: #ECE8F0; color: {TEXT}; border: 1px solid #D0C8D7; border-radius: 6px; padding: 8px 14px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QPushButton:hover {{ background: #E2D8EC; }}
            QPushButton#primary {{ background: {PURPLE}; color: white; border: none; font-weight: 600; padding: 10px 20px; }}
            QPushButton#primary:hover {{ background: #5D229E; }}
            QPushButton:disabled {{ color: #9B96A0; background: #EEECEF; }}
            QCheckBox {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 9pt; spacing: 7px; }}
            QProgressBar {{ border: none; background: #DED9E6; height: 7px; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {PURPLE}; border-radius: 3px; }}
            """
        )
        self.build_interface()

    @staticmethod
    def card() -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        return frame, layout

    @staticmethod
    def section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    @staticmethod
    def hint_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    def build_interface(self) -> None:
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background: {PURPLE_DARK};")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(30, 20, 30, 20)
        title = QLabel(APP_NAME)
        title.setObjectName("headerTitle")
        subtitle = QLabel(
            f"Version {APP_VERSION} · Turn a lecture video into a timestamped Word transcript "
            "with its slides and chat comments."
        )
        subtitle.setObjectName("headerSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        central_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("scrollBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(26, 20, 26, 28)
        body_layout.setSpacing(14)

        video_card, video_layout = self.card()
        video_layout.addWidget(self.section_label("1. Lecture video"))
        video_layout.addWidget(
            self.hint_label(
                "Drop an MP4 here, or browse to it. Other common video formats may also work."
            )
        )
        video_row = QHBoxLayout()
        self.video_edit = DropLineEdit()
        self.video_edit.setPlaceholderText("Choose or drop a lecture recording")
        self.video_edit.file_dropped.connect(self.set_video)
        browse_video = QPushButton("Browse…")
        browse_video.clicked.connect(self.browse_video)
        video_row.addWidget(self.video_edit, 1)
        video_row.addWidget(browse_video)
        video_layout.addLayout(video_row)
        drop_area = DropArea()
        drop_area.file_dropped.connect(self.set_video)
        video_layout.addWidget(drop_area)

        console_help = QFrame()
        console_help.setObjectName("consoleHelp")
        console_help_layout = QVBoxLayout(console_help)
        console_help_layout.setContentsMargins(12, 10, 12, 10)
        console_help_layout.setSpacing(6)
        console_title = QLabel("Can’t download the Blackboard recording?")
        console_title.setStyleSheet(f"font-weight: 600; color: {PURPLE}; border: none;")
        console_help_layout.addWidget(console_title)
        console_help_layout.addWidget(
            self.hint_label(
                "While the authorised recording is open and playing, press F12, choose Console, "
                "paste the command below and press Enter. It copies the video element’s current "
                "source URL. This does not bypass course access; the app still requires a local video file."
            )
        )
        command_row = QHBoxLayout()
        self.console_command = QLineEdit(BLACKBOARD_VIDEO_COMMAND)
        self.console_command.setReadOnly(True)
        self.console_command.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        copy_command = QPushButton("Copy F12 command")
        copy_command.clicked.connect(self.copy_blackboard_command)
        command_row.addWidget(self.console_command, 1)
        command_row.addWidget(copy_command)
        console_help_layout.addLayout(command_row)
        console_help_layout.addWidget(
            self.hint_label(
                "If the browser blocks pasting into Developer Tools, type the command manually. "
                "Only download and process recordings you are permitted to access."
            )
        )
        video_layout.addWidget(console_help)
        body_layout.addWidget(video_card)

        comments_card, comments_layout = self.card()
        comments_heading = QHBoxLayout()
        comments_heading.addWidget(self.section_label("2. Chat comments (optional)"))
        comments_heading.addStretch(1)
        self.comment_count = QLabel("No timestamped comments detected")
        self.comment_count.setObjectName("count")
        comments_heading.addWidget(self.comment_count)
        comments_layout.addLayout(comments_heading)
        comments_layout.addWidget(
            self.hint_label(
                "Paste the copied Blackboard chat list. Names, timestamps, messages and emoji are detected automatically."
            )
        )
        self.comments_edit = QTextEdit()
        self.comments_edit.setPlaceholderText(
            "Paste comments here, for example:\n\n- AMANDA SCHWARTZ\n  00:47\n  Yep sure can"
        )
        self.comments_edit.setMinimumHeight(220)
        self.comments_edit.textChanged.connect(self.schedule_comment_count)
        comments_layout.addWidget(self.comments_edit)
        body_layout.addWidget(comments_card)

        details_card, details_layout = self.card()
        details_layout.addWidget(self.section_label("3. Document details"))
        title_row = QHBoxLayout()
        title_label = self.hint_label("Title")
        title_label.setFixedWidth(72)
        self.title_edit = QLineEdit()
        title_row.addWidget(title_label)
        title_row.addWidget(self.title_edit, 1)
        details_layout.addLayout(title_row)
        output_row = QHBoxLayout()
        output_label = self.hint_label("Save as")
        output_label.setFixedWidth(72)
        self.output_edit = QLineEdit()
        choose_output = QPushButton("Choose…")
        choose_output.clicked.connect(self.browse_output)
        output_row.addWidget(output_label)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose_output)
        details_layout.addLayout(output_row)
        self.reuse_check = QCheckBox(
            "Reuse a cached local transcript when this exact video has already been processed"
        )
        self.reuse_check.setChecked(True)
        self.omit_check = QCheckBox(
            "Omit brief slide flicks with three or fewer spoken words (recommended)"
        )
        self.omit_check.setChecked(True)
        details_layout.addWidget(self.reuse_check)
        details_layout.addWidget(self.omit_check)
        body_layout.addWidget(details_card)

        action_card, action_layout = self.card()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        action_layout.addWidget(self.progress)
        self.status = self.hint_label("Choose or drop a lecture recording to begin.")
        action_layout.addWidget(self.status)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(105)
        self.log.setMaximumHeight(145)
        self.log.setStyleSheet("font-family: Consolas; font-size: 9pt; background: #FAF9FC;")
        action_layout.addWidget(self.log)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Create Word document")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_processing)
        self.open_button = QPushButton("Open finished document")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self.check_for_updates)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.update_button)
        buttons.addStretch(1)
        action_layout.addLayout(buttons)
        body_layout.addWidget(action_card)
        body_layout.addStretch(1)

        scroll.setWidget(body)
        central_layout.addWidget(scroll, 1)
        self.setCentralWidget(central)

        self.comment_timer = QTimer(self)
        self.comment_timer.setSingleShot(True)
        self.comment_timer.timeout.connect(self.update_comment_count)

    def browse_video(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose lecture recording",
            "",
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All files (*.*)",
        )
        if selected:
            self.set_video(selected)

    def copy_blackboard_command(self) -> None:
        QApplication.clipboard().setText(BLACKBOARD_VIDEO_COMMAND)
        self.status.setText("Blackboard F12 command copied to the clipboard.")

    def set_video(self, value: str) -> None:
        path = Path(value).expanduser().resolve()
        self.video_edit.setText(str(path))
        self.title_edit.setText(f"{path.stem} Transcript and Slides")
        self.output_edit.setText(str(path.with_name(f"{path.stem} Transcript and Slides.docx")))
        self.status.setText(
            "Video selected. Paste chat comments if you have them, then create the document."
        )

    def browse_output(self) -> None:
        initial = self.output_edit.text().strip() or str(Path.cwd() / "Transcript.docx")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Word document",
            initial,
            "Word document (*.docx)",
        )
        if selected:
            if not selected.lower().endswith(".docx"):
                selected += ".docx"
            self.output_edit.setText(selected)

    def schedule_comment_count(self) -> None:
        self.comment_timer.start(250)

    def update_comment_count(self) -> None:
        text = self.comments_edit.toPlainText()
        comments = parse_chat_comments(text) if text.strip() else []
        if comments:
            self.comment_count.setText(f"{len(comments)} timestamped comment(s) detected")
        elif text.strip():
            self.comment_count.setText("No recognised timestamped comments")
        else:
            self.comment_count.setText("No timestamped comments detected")

    def start_processing(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        video = Path(self.video_edit.text().strip().strip('"'))
        if not video.is_file():
            QMessageBox.critical(self, APP_NAME, "Choose a valid lecture video first.")
            return
        output_text = self.output_edit.text().strip().strip('"')
        if not output_text:
            QMessageBox.critical(self, APP_NAME, "Choose where to save the Word document.")
            return
        output = Path(output_text)
        if output.suffix.lower() != ".docx":
            output = output.with_suffix(".docx")
            self.output_edit.setText(str(output))
        title = self.title_edit.text().strip() or f"{video.stem} Transcript and Slides"
        raw_comments = self.comments_edit.toPlainText()
        comments = parse_chat_comments(raw_comments) if raw_comments.strip() else []
        if raw_comments.strip() and not comments:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "No timestamped chat comments were recognised. Continue without importing them?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.log.clear()
        self.start_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText("Starting local processing…")
        self.last_output = None

        self.worker = ProcessingThread(
            video.resolve(),
            output.resolve(),
            title,
            comments,
            self.reuse_check.isChecked(),
            self.omit_check.isChecked(),
        )
        self.worker.status.connect(self.status.setText)
        self.worker.log.connect(self.append_log)
        self.worker.completed.connect(self.processing_complete)
        self.worker.failed.connect(self.processing_failed)
        self.worker.start()

    def append_log(self, message: str) -> None:
        self.log.append(message)

    def processing_complete(self, value: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.start_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.last_output = Path(value)
        self.status.setText("Finished. The Word document is ready.")
        QMessageBox.information(self, APP_NAME, f"Finished creating:\n\n{value}")

    def processing_failed(self, value: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.start_button.setEnabled(True)
        self.status.setText("Processing stopped because of an error.")
        self.append_log(value)
        QMessageBox.critical(self, APP_NAME, value)

    def open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output)))

    def check_for_updates(self) -> None:
        if self.update_worker and self.update_worker.isRunning():
            return
        self.update_button.setEnabled(False)
        self.status.setText("Checking GitHub for a newer LectureWeave+ release…")
        self.update_worker = UpdateCheckThread(self)
        self.update_worker.completed.connect(self.update_check_complete)
        self.update_worker.failed.connect(self.update_check_failed)
        self.update_worker.start()

    def update_check_complete(self, release: dict) -> None:
        self.update_button.setEnabled(True)
        tag = str(release.get("tag_name") or "").strip()
        latest = version_key(tag)
        current = version_key(APP_VERSION)
        if latest <= current:
            self.status.setText(f"LectureWeave+ {APP_VERSION} is up to date.")
            QMessageBox.information(
                self,
                APP_NAME,
                f"You already have the latest version of LectureWeave+ ({APP_VERSION}).",
            )
            return

        assets = release.get("assets") or []
        preferred_names = ("LectureWeavePlus-Setup.exe", "LectureWeavePlus-Portable.zip")
        download_url = ""
        for preferred in preferred_names:
            match = next((asset for asset in assets if asset.get("name") == preferred), None)
            if match:
                download_url = str(match.get("browser_download_url") or "")
                break
        if not download_url:
            download_url = str(release.get("html_url") or f"{REPOSITORY_URL}/releases/latest")

        self.status.setText(f"LectureWeave+ {tag.lstrip('v')} is available.")
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"LectureWeave+ {tag.lstrip('v')} is available.\n\n"
            f"Installed version: {APP_VERSION}\n\n"
            "Open the official GitHub download now? Installing the newer version over "
            "the current copy preserves locally cached models and transcripts.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(download_url))

    def update_check_failed(self, message: str) -> None:
        self.update_button.setEnabled(True)
        self.status.setText(message)
        QMessageBox.warning(self, APP_NAME, message)


def run_self_test() -> int:
    parsed = parse_chat_comments(SELF_TEST_COMMENTS)
    if len(parsed) != 2:
        return 2
    if parsed[0]["author"] != "AMANDA SCHWARTZ" or parsed[0]["start"] != 47:
        return 3
    if parsed[1]["text"] != "yes. thanks":
        return 4
    return 0


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    icon_path = resource_path("assets/lectureweave-plus.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

