from __future__ import annotations

import contextlib
import copy
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QProcess, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from content_enrichment import (
    extract_slide_deck,
    match_deck_pages,
    run_slide_ocr,
    transcription_vocabulary,
)
from credential_store import (
    delete_huggingface_token,
    load_huggingface_token,
    save_huggingface_token,
)
from whisperx_bridge import enhance_with_whisperx, whisperx_environment_ready

from lecture_to_docx import (
    align_segments,
    build_docx,
    detect_slides,
    format_time,
    omit_incidental_slide_appearances,
    parse_chat_comments,
    prepare_whisper_model,
    transcribe,
    whisper_model_is_cached,
    write_transcript_files,
    write_claude_packet,
)


APP_NAME = "LectureWeave+"
APP_VERSION = "0.3.1"
REPOSITORY_URL = "https://github.com/lexstorey/LectureWeave-"
PURPLE = "#4A148C"
PURPLE_DARK = "#32105D"
PURPLE_LIGHT = "#F2ECFA"
BACKGROUND = "#F5F4F8"
TEXT = "#26232B"
BLACKBOARD_VIDEO_COMMAND = 'copy(document.querySelector("video")?.currentSrc)'
GPU_SETUP_URL = "https://github.com/SYSTRAN/faster-whisper#gpu"
WHISPERX_SETUP_URL = "https://github.com/m-bain/whisperX"


def model_cache_root() -> Path:
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return app_data / "LectureTranscriptSlides" / "models"


def missing_cuda_runtime_libraries() -> list[str]:
    """Return required Faster Whisper CUDA libraries that Windows cannot load."""
    if os.name != "nt":
        return []
    missing: list[str] = []
    for name in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.WinDLL(name)
        except OSError:
            missing.append(name)
    return missing

SELF_TEST_COMMENTS = """- **AMANDA SCHWARTZ**

  **00:47**

  Yep sure can
- DAO ROCHVARGER-WONG

  **02:44**

  yes. thanks
"""

SELF_TEST_PLAIN_COMMENTS = """EMILY MILES

**00:11**

always convenient

JOCK PALFREEMAN

**01:05:23**

It's almost a quasi privatisation of justice&#x20;
"""


def resource_path(relative: str) -> Path:
    """Return a bundled PyInstaller resource or a source-tree resource."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def version_key(value: str) -> tuple[int, int, int]:
    numbers = [int(number) for number in re.findall(r"\d+", value)[:3]]
    return tuple((numbers + [0, 0, 0])[:3])


def release_asset_sha256(value: str) -> str:
    """Return the hexadecimal SHA-256 value published by GitHub Releases."""
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", value.strip())
    if not match:
        raise ValueError("The release does not include a valid SHA-256 digest.")
    return match.group(1).lower()


def release_tag_from_url(value: str) -> str:
    match = re.search(r"/releases/tag/([^/?#]+)", value)
    if not match:
        raise ValueError("GitHub did not identify the latest release version.")
    tag = urllib.parse.unquote(match.group(1))
    if not re.fullmatch(r"v?\d+(?:\.\d+){1,3}", tag):
        raise ValueError(f"GitHub returned an unexpected release version: {tag}")
    return tag


def sha256_from_checksum_file(value: str, filename: str) -> str:
    for line in value.splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*", line)
        if match and Path(match.group(2)).name == filename:
            return match.group(1).lower()
    raise ValueError(f"The release checksum file does not contain {filename}.")


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
            headers = {"User-Agent": f"LectureWeavePlus/{APP_VERSION}"}
            latest_request = urllib.request.Request(
                f"{REPOSITORY_URL}/releases/latest",
                headers=headers,
                method="HEAD",
            )
            with urllib.request.urlopen(latest_request, timeout=12) as response:
                tag = release_tag_from_url(response.geturl())

            release_url = f"{REPOSITORY_URL}/releases/tag/{urllib.parse.quote(tag)}"
            asset_base = f"{REPOSITORY_URL}/releases/download/{urllib.parse.quote(tag)}"
            checksum_request = urllib.request.Request(
                f"{asset_base}/SHA256SUMS.txt",
                headers=headers,
            )
            with urllib.request.urlopen(checksum_request, timeout=12) as response:
                checksum_text = response.read().decode("ascii")
            installer_name = "LectureWeavePlus-Setup.exe"
            checksum = sha256_from_checksum_file(checksum_text, installer_name)
            self.completed.emit(
                {
                    "tag_name": tag,
                    "html_url": release_url,
                    "assets": [
                        {
                            "name": installer_name,
                            "browser_download_url": f"{asset_base}/{installer_name}",
                            "digest": f"sha256:{checksum}",
                        }
                    ],
                }
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.failed.emit("No published LectureWeave+ release is available yet.")
            else:
                self.failed.emit(f"GitHub returned HTTP {exc.code} while checking for updates.")
        except Exception as exc:
            self.failed.emit(f"Could not check for updates: {exc}")


class UpdateDownloadThread(QThread):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, expected_digest: str, tag: str) -> None:
        super().__init__()
        self.url = url
        self.expected_digest = expected_digest
        safe_tag = re.sub(r"[^0-9A-Za-z._-]+", "-", tag).strip("-") or "latest"
        self.destination = Path(tempfile.gettempdir()) / f"LectureWeavePlus-{safe_tag}-Setup.exe"

    def run(self) -> None:
        partial = self.destination.with_suffix(self.destination.suffix + ".part")
        try:
            expected_sha256 = release_asset_sha256(self.expected_digest)
            request = urllib.request.Request(
                self.url,
                headers={"User-Agent": f"LectureWeavePlus/{APP_VERSION}"},
            )
            hasher = hashlib.sha256()
            received = 0
            if partial.exists():
                partial.unlink()
            with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    self.progress.emit(received, total)
            actual_sha256 = hasher.hexdigest()
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    "The downloaded installer failed its SHA-256 safety check. "
                    "No update was installed."
                )
            if self.destination.exists():
                self.destination.unlink()
            partial.replace(self.destination)
            self.completed.emit(str(self.destination))
        except Exception as exc:
            if partial.exists():
                partial.unlink()
            self.failed.emit(f"Could not download the update: {exc}")


class ProcessingThread(QThread):
    status = Signal(str)
    progress = Signal(int, int)
    log = Signal(str)
    completed = Signal(str)
    failed = Signal(str)
    review_requested = Signal(object)

    def __init__(
        self,
        video: Path,
        output: Path,
        title: str,
        comments: list[dict],
        reuse_transcript: bool,
        omit_brief_slides: bool,
        model_name: str,
        deck_path: Path | None,
        whisperx_python: Path | None,
        hf_token: str | None,
    ) -> None:
        super().__init__()
        self.video = video
        self.output = output
        self.title = title
        self.comments = comments
        self.reuse_transcript = reuse_transcript
        self.omit_brief_slides = omit_brief_slides
        self.model_name = model_name
        self.deck_path = deck_path
        self.whisperx_python = whisperx_python
        self.hf_token = hf_token
        self.review_event = threading.Event()
        self.reviewed_slides: list[dict] | None = None

    def apply_review(self, slides: list[dict] | None) -> None:
        self.reviewed_slides = slides
        self.review_event.set()

    def report_progress(self, stage: str, current: int, total: int, detail: str) -> None:
        self.status.emit(detail)
        if total > 0:
            scaled = min(1000, max(0, int(current * 1000 / total)))
            self.progress.emit(scaled, 1000)
        else:
            self.progress.emit(0, 0)

    def run(self) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_root = app_data / "LectureTranscriptSlides"
        stat = self.video.stat()
        fingerprint = hashlib.sha256(
            f"{self.video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:20]
        work_dir = cache_root / "jobs" / fingerprint
        slides_dir = work_dir / "slides"
        model_cache_key = re.sub(r"[^0-9A-Za-z._-]+", "-", self.model_name)
        model_cache = cache_root / "models"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.output.parent.mkdir(parents=True, exist_ok=True)

        writer = SignalWriter(self.log)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                self.status.emit(f"Preparing the Whisper {self.model_name} speech model…")
                prepare_whisper_model(
                    self.model_name,
                    model_cache,
                    self.report_progress,
                )
                self.status.emit("Detecting stable slide changes…")
                slides, duration = detect_slides(
                    self.video,
                    slides_dir,
                    progress_callback=self.report_progress,
                )
                print(f"Detected {len(slides)} stable slide appearances")

                if self.deck_path:
                    deck_fingerprint = hashlib.sha256(
                        (
                            f"{self.deck_path}|{self.deck_path.stat().st_size}|"
                            f"{self.deck_path.stat().st_mtime_ns}"
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    deck_pages = extract_slide_deck(
                        self.deck_path,
                        work_dir / "deck" / deck_fingerprint,
                        self.report_progress,
                    )
                    self.status.emit("Extracting searchable text from the captured slides…")
                    run_slide_ocr(slides, self.report_progress)
                    match_deck_pages(slides, deck_pages)
                    matched = sum(bool(slide.get("deck_slide_number")) for slide in slides)
                    print(
                        f"Matched {matched} of {len(slides)} video appearances to "
                        f"{len(deck_pages)} supplied deck slides"
                    )
                else:
                    self.status.emit("Extracting searchable text from the slides…")
                    run_slide_ocr(slides, self.report_progress)
                vocabulary = transcription_vocabulary(slides)
                vocabulary_key = hashlib.sha256(vocabulary.encode("utf-8")).hexdigest()[:12]
                transcript_dir = work_dir / f"transcript-{model_cache_key}-{vocabulary_key}"
                if vocabulary:
                    print(f"Prepared {len(vocabulary)} characters of slide-derived terminology")

                transcript_json = transcript_dir / "transcript.json"
                if self.reuse_transcript and transcript_json.exists():
                    self.status.emit("Loading the cached local transcript…")
                    segments = json.loads(transcript_json.read_text(encoding="utf-8"))
                    device = "reused"
                    self.progress.emit(1, 1)
                    print(f"Loaded {len(segments)} cached speech segments")
                else:
                    self.status.emit(
                        "Transcribing locally—this is the longest step, especially on CPU…"
                    )
                    segments, device = transcribe(
                        self.video,
                        work_dir,
                        self.model_name,
                        model_cache=model_cache,
                        progress_callback=self.report_progress,
                        hotwords=vocabulary,
                    )
                    write_transcript_files(segments, transcript_dir)
                    print(f"Transcribed {len(segments)} timestamped speech segments")

                if self.whisperx_python:
                    cache_name = (
                        "whisperx-aligned-speakers.json"
                        if self.hf_token
                        else "whisperx-aligned.json"
                    )
                    aligned_cache = transcript_dir / cache_name
                    if self.reuse_transcript and aligned_cache.is_file():
                        segments = json.loads(aligned_cache.read_text(encoding="utf-8"))
                        print("Loaded cached WhisperX word alignment")
                    else:
                        segments = enhance_with_whisperx(
                            self.video,
                            segments,
                            transcript_dir,
                            self.whisperx_python,
                            self.report_progress,
                            self.hf_token,
                        )
                        aligned_cache.write_text(
                            json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8"
                        )

                self.report_progress(
                    "alignment",
                    0,
                    0,
                    f"Aligning {len(segments)} speech segments and {len(self.comments)} chat comments…",
                )
                align_segments(slides, segments, self.comments)
                omitted_slides: list[dict] = []
                if self.omit_brief_slides:
                    slides, omitted_slides = omit_incidental_slide_appearances(slides)
                    print(
                        f"Omitted {len(omitted_slides)} brief navigation slide appearances"
                    )

                self.status.emit("Waiting for slide and transcript review…")
                self.review_requested.emit(copy.deepcopy(slides))
                self.review_event.wait()
                if self.reviewed_slides is None:
                    raise RuntimeError("Processing was cancelled during review.")
                slides = self.reviewed_slides
                print(f"Review accepted with {len(slides)} slide appearances")

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
                            "canonical_index",
                            "deck_slide_number",
                            "deck_text",
                            "deck_image",
                            "slide_text",
                            "text_source",
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
                    progress_callback=self.report_progress,
                )
                outputs = write_claude_packet(
                    self.video,
                    slides,
                    duration,
                    self.output,
                    self.title,
                    self.model_name,
                    self.report_progress,
                )
                print(f"Imported {len(self.comments)} chat comments")
                print(f"Created {self.output}")
                print(f"Created Claude packet {outputs['packet']}")
            writer.flush()
            self.completed.emit(str(self.output))
        except Exception as exc:
            writer.flush()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ReviewDialog(QDialog):
    """Review deck numbering, extracted text and transcript before export."""

    def __init__(self, slides: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.slides = slides
        self.current_row = -1
        self.setWindowTitle("Review lecture alignment — LectureWeave+")
        self.resize(1180, 760)

        root = QVBoxLayout(self)
        heading = QLabel("Review slides before export")
        heading.setObjectName("section")
        root.addWidget(heading)
        explanation = QLabel(
            "Check the actual deck slide number, slide text and lecturer transcript. "
            "The Claude packet keeps these as separate evidence. Use 0 when a video frame "
            "does not confidently match the supplied deck."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.slide_list = QListWidget()
        self.slide_list.setMinimumWidth(300)
        splitter.addWidget(self.slide_list)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        self.preview = QLabel("Slide preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(250)
        self.preview.setStyleSheet("background: #17131B; color: white; border-radius: 6px;")
        editor_layout.addWidget(self.preview)

        controls = QHBoxLayout()
        self.include_check = QCheckBox("Include this appearance")
        self.include_check.setChecked(True)
        controls.addWidget(self.include_check)
        controls.addWidget(QLabel("Actual deck slide number (0 = unmatched)"))
        self.deck_number = QSpinBox()
        self.deck_number.setRange(0, 9999)
        controls.addWidget(self.deck_number)
        self.merge_button = QPushButton("Merge into previous appearance")
        self.merge_button.clicked.connect(self.merge_previous)
        controls.addWidget(self.merge_button)
        controls.addStretch(1)
        editor_layout.addLayout(controls)

        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        editor_layout.addWidget(self.source_label)
        editor_layout.addWidget(QLabel("Slide text — material already printed on the slide"))
        self.slide_text_edit = QTextEdit()
        self.slide_text_edit.setMinimumHeight(110)
        editor_layout.addWidget(self.slide_text_edit)
        editor_layout.addWidget(QLabel("Lecturer discussion — timestamped speech while this slide was displayed"))
        self.transcript_edit = QTextEdit()
        self.transcript_edit.setMinimumHeight(170)
        editor_layout.addWidget(self.transcript_edit)
        splitter.addWidget(editor)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("Approve and create outputs")
        save_button.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for position, slide in enumerate(self.slides, 1):
            slide.setdefault("_include", True)
            deck = slide.get("deck_slide_number")
            label = f"Deck slide {deck}" if deck else "Unmatched slide"
            self.slide_list.addItem(
                f"{position}. {label} · {format_time(slide['start'])}"
            )
        self.slide_list.currentRowChanged.connect(self.load_row)
        if self.slides:
            self.slide_list.setCurrentRow(0)

    def _transcript_text(self, slide: dict) -> str:
        return "\n".join(
            f"[{format_time(segment['start'])}] {segment['text']}"
            for segment in slide.get("segments", [])
        )

    def save_row(self) -> None:
        if self.current_row < 0 or self.current_row >= len(self.slides):
            return
        slide = self.slides[self.current_row]
        slide["_include"] = self.include_check.isChecked()
        number = self.deck_number.value()
        if number:
            slide["deck_slide_number"] = number
        else:
            slide.pop("deck_slide_number", None)
            slide.pop("deck_image", None)
        slide["slide_text"] = self.slide_text_edit.toPlainText().strip()

        existing = slide.get("segments", [])
        by_second = {round(float(segment["start"])): segment for segment in existing}
        edited: list[dict] = []
        current: dict | None = None
        for raw_line in self.transcript_edit.toPlainText().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\]\s*(.*)$", line)
            if match:
                hours = int(match.group(1) or 0)
                start = hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))
                original = copy.deepcopy(by_second.get(start, {}))
                original.update({
                    "start": float(start),
                    "end": float(original.get("end", start + 5)),
                    "text": match.group(4).strip(),
                })
                current = original
                edited.append(current)
            elif current is not None:
                current["text"] = (current["text"] + " " + line).strip()
        slide["segments"] = edited
        self._refresh_list_item(self.current_row)

    def _refresh_list_item(self, row: int) -> None:
        slide = self.slides[row]
        deck = slide.get("deck_slide_number")
        label = f"Deck slide {deck}" if deck else "Unmatched slide"
        included = "" if slide.get("_include", True) else " · EXCLUDED"
        self.slide_list.item(row).setText(
            f"{row + 1}. {label} · {format_time(slide['start'])}{included}"
        )

    def load_row(self, row: int) -> None:
        if row == self.current_row:
            return
        self.save_row()
        self.current_row = row
        if row < 0 or row >= len(self.slides):
            return
        slide = self.slides[row]
        self.include_check.setChecked(bool(slide.get("_include", True)))
        self.deck_number.setValue(int(slide.get("deck_slide_number") or 0))
        self.slide_text_edit.setPlainText(str(slide.get("slide_text") or ""))
        self.transcript_edit.setPlainText(self._transcript_text(slide))
        self.source_label.setText(
            f"Video appearance {row + 1} · {format_time(slide['start'])}–"
            f"{format_time(slide['end'])} · text source: {slide.get('text_source', 'not available')}"
        )
        image_path = str(slide.get("deck_image") or slide.get("image") or "")
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.preview.setText("Preview unavailable")
            self.preview.setPixmap(QPixmap())
        else:
            self.preview.setPixmap(
                pixmap.scaled(
                    760, 360,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.merge_button.setEnabled(row > 0)

    def merge_previous(self) -> None:
        row = self.current_row
        if row <= 0:
            return
        self.save_row()
        current = self.slides[row]
        previous = self.slides[row - 1]
        previous.setdefault("segments", []).extend(current.get("segments", []))
        previous.setdefault("comments", []).extend(current.get("comments", []))
        previous["end"] = current["end"]
        current["segments"] = []
        current["comments"] = []
        current["_include"] = False
        self._refresh_list_item(row)
        self.slide_list.setCurrentRow(row - 1)

    def reviewed_slides(self) -> list[dict]:
        self.save_row()
        kept = [slide for slide in self.slides if slide.get("_include", True)]
        if not kept:
            return []
        all_segments = [segment for slide in self.slides for segment in slide.get("segments", [])]
        all_comments = [comment for slide in self.slides for comment in slide.get("comments", [])]
        final_end = max(float(slide["end"]) for slide in self.slides)
        for index, slide in enumerate(kept):
            slide.pop("_include", None)
            slide["index"] = index + 1
            slide["alignment_start"] = 0.0 if index == 0 else float(slide["start"])
            slide["end"] = float(kept[index + 1]["start"]) if index + 1 < len(kept) else final_end
        align_segments(kept, all_segments, all_comments)
        return kept

    def accept(self) -> None:
        self.save_row()
        if not any(slide.get("_include", True) for slide in self.slides):
            QMessageBox.warning(self, APP_NAME, "Keep at least one slide appearance.")
            return
        super().accept()


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
        self.update_download_worker: UpdateDownloadThread | None = None
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
            QLineEdit, QTextEdit, QComboBox {{ background: white; color: {TEXT}; border: 1px solid #CFC9D5; border-radius: 5px; padding: 7px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 2px solid {PURPLE}; }}
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
            f"Version {APP_VERSION} · Align a lecture with its real deck slides, spoken discussion "
            "and chat—then export Word and Claude-ready notes."
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
        deck_row = QHBoxLayout()
        deck_label = self.hint_label("Slide pack")
        deck_label.setFixedWidth(72)
        self.deck_edit = DropLineEdit()
        self.deck_edit.setPlaceholderText("Optional PDF or PowerPoint slide pack")
        self.deck_edit.file_dropped.connect(self.set_deck)
        choose_deck = QPushButton("Choose…")
        choose_deck.clicked.connect(self.browse_deck)
        clear_deck = QPushButton("Clear")
        clear_deck.clicked.connect(self.deck_edit.clear)
        deck_row.addWidget(deck_label)
        deck_row.addWidget(self.deck_edit, 1)
        deck_row.addWidget(choose_deck)
        deck_row.addWidget(clear_deck)
        details_layout.addLayout(deck_row)
        details_layout.addWidget(
            self.hint_label(
                "Optional but recommended: a PDF provides exact page numbers, native text and clean "
                "slide images. PowerPoint provides native slide text and numbering; unmatched video "
                "frames still use local RapidOCR."
            )
        )
        self.whisperx_check = QCheckBox(
            "Optional enhanced word alignment and anonymous speakers using an external WhisperX environment"
        )
        self.whisperx_check.setChecked(False)
        details_layout.addWidget(self.whisperx_check)
        whisperx_row = QHBoxLayout()
        whisperx_label = self.hint_label("WhisperX")
        whisperx_label.setFixedWidth(72)
        self.whisperx_edit = QLineEdit(os.environ.get("LECTUREWEAVE_WHISPERX_PYTHON", ""))
        self.whisperx_edit.setPlaceholderText("Optional Python executable containing WhisperX")
        choose_whisperx = QPushButton("Choose Python…")
        choose_whisperx.clicked.connect(self.browse_whisperx)
        whisperx_row.addWidget(whisperx_label)
        whisperx_row.addWidget(self.whisperx_edit, 1)
        whisperx_row.addWidget(choose_whisperx)
        details_layout.addLayout(whisperx_row)
        token_row = QHBoxLayout()
        token_label = self.hint_label("HF token")
        token_label.setFixedWidth(72)
        self.hf_token_edit = QLineEdit()
        self.hf_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token_edit.setPlaceholderText("Paste a Hugging Face read token beginning with hf_")
        save_token = QPushButton("Save token")
        save_token.clicked.connect(self.save_hf_token)
        remove_token = QPushButton("Remove")
        remove_token.clicked.connect(self.remove_hf_token)
        token_row.addWidget(token_label)
        token_row.addWidget(self.hf_token_edit, 1)
        token_row.addWidget(save_token)
        token_row.addWidget(remove_token)
        details_layout.addLayout(token_row)
        self.hf_token_status = self.hint_label("")
        details_layout.addWidget(self.hf_token_status)
        self.refresh_hf_token_status()
        details_layout.addWidget(
            self.hint_label(
                "WhisperX is deliberately separate because its PyTorch models are very large. "
                "Word alignment works without an account; anonymous speaker labels additionally require "
                "an accepted pyannote model and a saved Hugging Face read token. The token is stored "
                "securely in Windows Credential Manager."
            )
        )
        quality_row = QHBoxLayout()
        quality_label = self.hint_label("Accuracy")
        quality_label.setFixedWidth(72)
        self.model_combo = QComboBox()
        self.model_combo.addItem("Maximum accuracy — Large-v3 (recommended)", "large-v3")
        self.model_combo.addItem("Faster — Turbo", "turbo")
        quality_row.addWidget(quality_label)
        quality_row.addWidget(self.model_combo, 1)
        details_layout.addLayout(quality_row)
        details_layout.addWidget(
            self.hint_label(
                "Large-v3 gives the best transcript accuracy for long lectures. Its first use "
                "requires an approximately 3 GB download; LectureWeave+ will ask before downloading it."
            )
        )
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
        self.folder_button = QPushButton("Open output folder")
        self.folder_button.setEnabled(False)
        self.folder_button.clicked.connect(self.open_output_folder)
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self.check_for_updates)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.folder_button)
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

    def browse_deck(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose optional slide pack",
            "",
            "Slide packs (*.pdf *.pptx *.pptm);;All files (*.*)",
        )
        if selected:
            self.set_deck(selected)

    def set_deck(self, value: str) -> None:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() not in {".pdf", ".pptx", ".pptm"}:
            QMessageBox.warning(self, APP_NAME, "Choose a PDF or PowerPoint slide pack.")
            return
        self.deck_edit.setText(str(path))
        self.status.setText("Slide pack selected. It will be matched to the video timeline automatically.")

    def browse_whisperx(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Python executable containing WhisperX",
            "",
            "Python executable (python.exe);;Executables (*.exe);;All files (*.*)",
        )
        if selected:
            self.whisperx_edit.setText(selected)
            self.whisperx_check.setChecked(True)

    def refresh_hf_token_status(self) -> None:
        try:
            saved = bool(load_huggingface_token())
        except OSError:
            saved = False
        self.hf_token_status.setText(
            "Hugging Face token is saved and will be reused automatically."
            if saved
            else "No Hugging Face token saved. Word alignment still works; speaker labels require one."
        )

    def save_hf_token(self) -> None:
        token = self.hf_token_edit.text().strip()
        try:
            save_huggingface_token(token)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, APP_NAME, f"The token could not be saved: {exc}")
            return
        self.hf_token_edit.clear()
        self.refresh_hf_token_status()
        QMessageBox.information(
            self,
            APP_NAME,
            "The Hugging Face token was saved securely in Windows Credential Manager. "
            "You will not need to enter it again.",
        )

    def remove_hf_token(self) -> None:
        try:
            removed = delete_huggingface_token()
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"The saved token could not be removed: {exc}")
            return
        self.hf_token_edit.clear()
        self.refresh_hf_token_status()
        if removed:
            QMessageBox.information(self, APP_NAME, "The saved Hugging Face token was removed.")

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

        deck_text = self.deck_edit.text().strip().strip('"')
        deck_path = Path(deck_text).resolve() if deck_text else None
        if deck_path and (not deck_path.is_file() or deck_path.suffix.lower() not in {".pdf", ".pptx", ".pptm"}):
            QMessageBox.critical(self, APP_NAME, "The selected slide pack is not a valid PDF or PowerPoint file.")
            return

        model_name = self.preflight_transcription_model()
        if not model_name:
            return
        proceed, whisperx_python = self.preflight_whisperx()
        if not proceed:
            return
        try:
            hf_token = load_huggingface_token() or None
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"The saved Hugging Face token could not be read: {exc}")
            return

        self.log.clear()
        self.start_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.folder_button.setEnabled(False)
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
            model_name,
            deck_path,
            whisperx_python,
            hf_token,
        )
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(self.processing_progress)
        self.worker.log.connect(self.append_log)
        self.worker.review_requested.connect(self.review_processing)
        self.worker.completed.connect(self.processing_complete)
        self.worker.failed.connect(self.processing_failed)
        self.worker.start()

    def preflight_whisperx(self) -> tuple[bool, Path | None]:
        if not self.whisperx_check.isChecked():
            return True, None
        while True:
            value = self.whisperx_edit.text().strip().strip('"')
            python_path = Path(value).resolve() if value else Path()
            if value and whisperx_environment_ready(python_path):
                return True, python_path

            prompt = QMessageBox(self)
            prompt.setWindowTitle(APP_NAME)
            prompt.setIcon(QMessageBox.Icon.Information)
            prompt.setText("The optional WhisperX environment is not ready.")
            prompt.setInformativeText(
                "LectureWeave+ keeps WhisperX separate so the normal installer does not gain several "
                "gigabytes of PyTorch dependencies. Choose a Python environment where WhisperX is "
                "installed, open its setup guide, or continue with LectureWeave+'s built-in word timing."
            )
            choose = prompt.addButton("Choose Python environment", QMessageBox.ButtonRole.ActionRole)
            guide = prompt.addButton("Open WhisperX setup guide", QMessageBox.ButtonRole.HelpRole)
            standard = prompt.addButton("Continue without WhisperX", QMessageBox.ButtonRole.AcceptRole)
            cancel = prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.setDefaultButton(choose)
            prompt.exec()
            clicked = prompt.clickedButton()
            if clicked is choose:
                self.browse_whisperx()
                continue
            if clicked is guide:
                QDesktopServices.openUrl(QUrl(WHISPERX_SETUP_URL))
                return False, None
            if clicked is standard:
                self.whisperx_check.setChecked(False)
                return True, None
            if clicked is cancel:
                return False, None
            return False, None

    def review_processing(self, slides: list[dict]) -> None:
        if not self.worker:
            return
        dialog = ReviewDialog(slides, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            reviewed = dialog.reviewed_slides()
            self.status.setText("Review accepted. Creating Word and Claude-ready outputs…")
            self.worker.apply_review(reviewed)
        else:
            self.status.setText("Processing cancelled during review.")
            self.worker.apply_review(None)

    def preflight_transcription_model(self) -> str | None:
        """Resolve model choice while explaining downloads and GPU requirements."""
        model_name = str(self.model_combo.currentData())
        models_dir = model_cache_root()

        while True:
            if not whisper_model_is_cached(model_name, models_dir):
                prompt = QMessageBox(self)
                prompt.setWindowTitle(APP_NAME)
                prompt.setIcon(QMessageBox.Icon.Question)
                display_name = "Large-v3" if model_name == "large-v3" else "Turbo"
                prompt.setText(f"The Whisper {display_name} model is not downloaded yet.")
                if model_name == "large-v3":
                    prompt.setInformativeText(
                        "Large-v3 provides the maximum transcript accuracy and is an approximately "
                        "3 GB one-time download. Download progress will be shown in LectureWeave+, "
                        "and the model will be reused for future lectures."
                    )
                    download = prompt.addButton(
                        "Download Large-v3 and continue",
                        QMessageBox.ButtonRole.AcceptRole,
                    )
                    use_turbo = prompt.addButton(
                        "Use Turbo this time",
                        QMessageBox.ButtonRole.ActionRole,
                    )
                else:
                    prompt.setInformativeText(
                        "Turbo is a one-time download. Download progress will be shown in "
                        "LectureWeave+, and the model will be reused for future lectures."
                    )
                    download = prompt.addButton(
                        "Download Turbo and continue",
                        QMessageBox.ButtonRole.AcceptRole,
                    )
                    use_turbo = None
                cancel = prompt.addButton(QMessageBox.StandardButton.Cancel)
                prompt.setDefaultButton(download)
                prompt.exec()
                clicked = prompt.clickedButton()
                if clicked is cancel:
                    return None
                if use_turbo is not None and clicked is use_turbo:
                    model_name = "turbo"
                    continue
                if clicked is not download:
                    return None

            if model_name != "large-v3":
                return model_name

            missing = missing_cuda_runtime_libraries()
            if not missing:
                return model_name

            prompt = QMessageBox(self)
            prompt.setWindowTitle(APP_NAME)
            prompt.setIcon(QMessageBox.Icon.Warning)
            prompt.setText("Maximum-accuracy transcription cannot currently use your NVIDIA GPU.")
            prompt.setInformativeText(
                "Windows could not load " + ", ".join(missing) + ". Large-v3 can continue on "
                "the CPU, but a 1–2 hour lecture will take much longer. You can open the official "
                "Faster Whisper GPU setup guide, use Turbo, or continue on CPU."
            )
            setup = prompt.addButton(
                "Open GPU setup guide",
                QMessageBox.ButtonRole.HelpRole,
            )
            cpu = prompt.addButton(
                "Continue with Large-v3 on CPU",
                QMessageBox.ButtonRole.AcceptRole,
            )
            turbo = prompt.addButton(
                "Use Turbo this time",
                QMessageBox.ButtonRole.ActionRole,
            )
            cancel = prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.setDefaultButton(setup)
            prompt.exec()
            clicked = prompt.clickedButton()
            if clicked is setup:
                QDesktopServices.openUrl(QUrl(GPU_SETUP_URL))
                self.status.setText("GPU setup guide opened. Try again after installing the required NVIDIA runtime.")
                return None
            if clicked is cpu:
                return model_name
            if clicked is turbo:
                model_name = "turbo"
                continue
            if clicked is cancel:
                return None
            return None

    def processing_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
        else:
            self.progress.setRange(0, 0)

    def append_log(self, message: str) -> None:
        self.log.append(message)

    def processing_complete(self, value: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.start_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.last_output = Path(value)
        packet = self.last_output.with_suffix("").with_name(
            self.last_output.with_suffix("").name + " - Claude Packet.zip"
        )
        self.status.setText("Finished. Word and Claude-ready outputs are ready.")
        QMessageBox.information(
            self,
            APP_NAME,
            f"Finished creating:\n\nWord: {value}\n\nClaude packet: {packet}",
        )

    def processing_failed(self, value: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.start_button.setEnabled(True)
        if "cancelled during review" in value.lower():
            self.status.setText("Processing cancelled during review. No outputs were overwritten.")
            self.append_log(value)
            return
        self.status.setText("Processing stopped because of an error.")
        self.append_log(value)
        QMessageBox.critical(self, APP_NAME, value)

    def open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output)))

    def open_output_folder(self) -> None:
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output.parent)))

    def check_for_updates(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(
                self,
                APP_NAME,
                "Finish creating the current document before installing an update.",
            )
            return
        if (self.update_worker and self.update_worker.isRunning()) or (
            self.update_download_worker and self.update_download_worker.isRunning()
        ):
            return
        self.update_button.setEnabled(False)
        self.status.setText("Checking GitHub for a newer LectureWeave+ release…")
        self.update_worker = UpdateCheckThread(self)
        self.update_worker.completed.connect(self.update_check_complete)
        self.update_worker.failed.connect(self.update_check_failed)
        self.update_worker.start()

    def update_check_complete(self, release: dict) -> None:
        tag = str(release.get("tag_name") or "").strip()
        latest = version_key(tag)
        current = version_key(APP_VERSION)
        if latest <= current:
            self.update_button.setEnabled(True)
            self.status.setText(f"LectureWeave+ {APP_VERSION} is up to date.")
            QMessageBox.information(
                self,
                APP_NAME,
                f"You already have the latest version of LectureWeave+ ({APP_VERSION}).",
            )
            return

        assets = release.get("assets") or []
        installer = next(
            (asset for asset in assets if asset.get("name") == "LectureWeavePlus-Setup.exe"),
            None,
        )
        if not installer:
            self.update_check_failed("The latest release does not contain a Windows installer.")
            return
        download_url = str(installer.get("browser_download_url") or "")
        digest = str(installer.get("digest") or "")
        try:
            release_asset_sha256(digest)
        except ValueError as exc:
            self.update_check_failed(str(exc))
            return

        self.status.setText(f"LectureWeave+ {tag.lstrip('v')} is available.")
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"LectureWeave+ {tag.lstrip('v')} is available.\n\n"
            f"Installed version: {APP_VERSION}\n\n"
            "Download and install it now?\n\n"
            "LectureWeave+ will verify the installer, close, install the update, and "
            "reopen automatically. Cached models and transcripts are preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start_update_download(download_url, digest, tag)
        else:
            self.update_button.setEnabled(True)
            self.status.setText(f"LectureWeave+ {tag.lstrip('v')} is available.")

    def start_update_download(self, url: str, digest: str, tag: str) -> None:
        self.start_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(f"Downloading LectureWeave+ {tag.lstrip('v')}…")
        self.update_download_worker = UpdateDownloadThread(url, digest, tag)
        self.update_download_worker.progress.connect(self.update_download_progress)
        self.update_download_worker.completed.connect(self.update_download_complete)
        self.update_download_worker.failed.connect(self.update_download_failed)
        self.update_download_worker.start()

    def update_download_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(received)
            percent = received * 100 // total
            self.status.setText(
                f"Downloading update… {received / 1048576:.1f} of {total / 1048576:.1f} MB ({percent}%)"
            )
        else:
            self.progress.setRange(0, 0)
            self.status.setText(f"Downloading update… {received / 1048576:.1f} MB")

    def update_download_complete(self, installer: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText("Update verified. Closing LectureWeave+ and installing…")
        result = QProcess.startDetached(
            installer,
            ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        )
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            self.update_download_failed(
                "The verified installer was downloaded, but Windows could not start it."
            )
            return
        QApplication.instance().quit()

    def update_download_failed(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.start_button.setEnabled(True)
        self.update_button.setEnabled(True)
        self.status.setText(message)
        QMessageBox.warning(self, APP_NAME, message)

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
    plain = parse_chat_comments(SELF_TEST_PLAIN_COMMENTS)
    if len(plain) != 2:
        return 5
    if plain[0]["author"] != "EMILY MILES" or plain[0]["start"] != 11:
        return 6
    if plain[1]["start"] != 3923 or plain[1]["text"] != "It's almost a quasi privatisation of justice":
        return 7
    if release_asset_sha256("sha256:" + "a" * 64) != "a" * 64:
        return 8
    try:
        import hf_xet  # noqa: F401
    except ImportError:
        return 9
    return 0


def run_model_cache_test() -> int:
    try:
        from faster_whisper import WhisperModel

        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        model_path = prepare_whisper_model(
            "turbo",
            app_data / "LectureTranscriptSlides" / "models",
        )
        WhisperModel(str(model_path), device="cpu", compute_type="int8")
        return 0
    except Exception:
        return 10


def run_content_feature_test() -> int:
    """Verify packaged OCR and PDF slide-pack support without network access."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image_path = root / "slide.png"
            image = np.full((300, 1000, 3), 255, dtype=np.uint8)
            cv2.putText(
                image,
                "International Arbitration Section 18",
                (20, 170),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.5,
                (0, 0, 0),
                3,
            )
            cv2.imwrite(str(image_path), image)
            slides = [{"index": 1, "canonical_index": 1, "image": str(image_path)}]
            run_slide_ocr(slides)
            if "Arbitration" not in slides[0].get("slide_text", ""):
                return 11

            pdf_path = root / "deck.pdf"
            with Image.open(image_path) as source:
                source.convert("RGB").save(pdf_path, "PDF")
            pages = extract_slide_deck(pdf_path, root / "deck-pages")
            if len(pages) != 1 or not pages[0].image or not Path(pages[0].image).is_file():
                return 12
        return 0
    except Exception:
        return 13


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    if "--model-cache-test" in sys.argv:
        raise SystemExit(run_model_cache_test())
    if "--content-feature-test" in sys.argv:
        raise SystemExit(run_content_feature_test())
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

