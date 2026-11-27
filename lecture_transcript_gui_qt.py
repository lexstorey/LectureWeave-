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
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QProcess, QSettings, QThread, QTimer, QUrl, Qt, Signal
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

from chat_alignment import ChatClockFit, apply_chat_clock, estimate_chat_clock
from claude_pack import build_claude_pack, build_visuals_pdf, write_claude_instructions
from content_enrichment import (
    extract_slide_deck,
    load_glossary,
    match_deck_pages,
    run_slide_ocr,
    transcription_vocabulary,
)
from credential_store import (
    delete_huggingface_token,
    huggingface_token_source,
    load_huggingface_token,
    save_huggingface_token,
)
from whisperx_bridge import (
    default_whisperx_python,
    enhance_with_whisperx,
    inspect_whisperx_environment,
    whisperx_environment_ready,
)

from lecture_to_docx import (
    align_segments,
    build_docx,
    detect_slides,
    format_time,
    omit_incidental_slide_appearances,
    parse_chat_comments,
    prepare_whisper_model,
    TRANSCRIPTION_SETTINGS_VERSION,
    transcribe,
    whisper_model_is_cached,
    write_transcript_files,
)


APP_NAME = "LectureWeave+"
APP_VERSION = "0.4.2"
REPOSITORY_URL = "https://github.com/lexstorey/LectureWeave-"
PURPLE = "#4A148C"
PURPLE_DARK = "#32105D"
PURPLE_LIGHT = "#F2ECFA"
BACKGROUND = "#F5F4F8"
BORDER = "#E4E0E8"
MUTED = "#5F5868"
TEXT = "#26232B"
BLACKBOARD_VIDEO_COMMAND = 'copy(document.querySelector("video")?.currentSrc)'
GPU_SETUP_URL = "https://github.com/SYSTRAN/faster-whisper#gpu"
WHISPERX_SETUP_URL = "https://github.com/m-bain/whisperX"
_DLL_DIRECTORY_HANDLES: list[tuple[str, object]] = []


def model_cache_root() -> Path:
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return app_data / "LectureTranscriptSlides" / "models"


def configure_cuda_runtime(python_executable: Path | None = None) -> list[Path]:
    """Make CUDA/cuDNN DLLs discoverable from the system or WhisperX environment."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    candidates: list[Path] = []
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")
    candidates.append(Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"))
    python_executable = python_executable or default_whisperx_python()
    if python_executable:
        candidates.append(
            python_executable.parent.parent / "Lib" / "site-packages" / "torch" / "lib"
        )
    loaded: list[Path] = []
    existing_handles = {path for path, _handle in _DLL_DIRECTORY_HANDLES}
    for folder in candidates:
        if not folder.is_dir() or str(folder) in existing_handles:
            continue
        try:
            handle = os.add_dll_directory(str(folder))
            _DLL_DIRECTORY_HANDLES.append((str(folder), handle))
            existing_handles.add(str(folder))
            loaded.append(folder)
        except OSError:
            continue
    return loaded


def missing_cuda_runtime_libraries() -> list[str]:
    """Return required Faster Whisper CUDA libraries that Windows cannot load."""
    if os.name != "nt":
        return []
    configure_cuda_runtime()
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


class WheelSafeComboBox(QComboBox):
    """Prevent page scrolling from accidentally changing a selected option."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class DropArea(QLabel):
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__("Drop a lecture video here")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setMinimumHeight(64)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class DeckList(QListWidget):
    """Slide packs in deck order (A, B, …); accepts dropped files."""

    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMaximumHeight(110)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def paths(self) -> list[Path]:
        return [Path(self.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.count())]


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


class ProcessingCancelled(RuntimeError):
    """Raised cooperatively when the user cancels the active generation."""


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
    cancelled = Signal()
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
        deck_paths: list[Path],
        whisperx_python: Path | None,
        hf_token: str | None,
        glossary_path: Path | None = None,
        auto_chat_clock: bool = True,
        manual_chat_offset: float = 0.0,
        write_pack: bool = True,
        write_docx: bool = True,
        pseudonymise: bool = True,
    ) -> None:
        super().__init__()
        self.video = video
        self.output = output
        self.title = title
        self.comments = comments
        self.reuse_transcript = reuse_transcript
        self.omit_brief_slides = omit_brief_slides
        self.model_name = model_name
        self.deck_paths = list(deck_paths or [])
        self.glossary_path = glossary_path
        self.auto_chat_clock = auto_chat_clock
        self.manual_chat_offset = manual_chat_offset
        self.write_pack = write_pack
        self.write_docx = write_docx
        self.pseudonymise = pseudonymise
        self.result_files: list[Path] = []
        self.result_notes: list[str] = []
        self.whisperx_python = whisperx_python
        self.hf_token = hf_token
        self.review_event = threading.Event()
        self.cancel_event = threading.Event()
        self.reviewed_slides: list[dict] | None = None

    def apply_review(self, slides: list[dict] | None) -> None:
        self.reviewed_slides = slides
        self.review_event.set()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.requestInterruption()
        self.reviewed_slides = None
        self.review_event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set() or self.isInterruptionRequested():
            raise ProcessingCancelled("Generation cancelled by the user.")

    def report_progress(self, stage: str, current: int, total: int, detail: str) -> None:
        self.raise_if_cancelled()
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
        staging_output = self.output.with_name(
            f".{self.output.stem}.lectureweave-partial.docx"
        )
        slides_dir = work_dir / "slides"
        model_cache_key = re.sub(r"[^0-9A-Za-z._-]+", "-", self.model_name)
        model_cache = cache_root / "models"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.output.parent.mkdir(parents=True, exist_ok=True)

        writer = SignalWriter(self.log)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                self.raise_if_cancelled()
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

                deck_pages = []
                deck_summaries: list[dict] = []
                for deck_index, deck_path in enumerate(self.deck_paths):
                    label = chr(ord("A") + deck_index)
                    deck_fingerprint = hashlib.sha256(
                        (
                            f"{deck_path}|{deck_path.stat().st_size}|"
                            f"{deck_path.stat().st_mtime_ns}"
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    pages = extract_slide_deck(
                        deck_path,
                        work_dir / "deck" / deck_fingerprint,
                        self.report_progress,
                    )
                    for page in pages:
                        page.deck = label
                    deck_pages.extend(pages)
                    deck_summaries.append({"label": label, "name": deck_path.name, "pages": len(pages)})
                self.status.emit("Extracting searchable text from the slides…")
                run_slide_ocr(slides, self.report_progress)
                if deck_pages:
                    match_deck_pages(slides, deck_pages)
                    matched = sum(bool(slide.get("deck_slide_number")) for slide in slides)
                    print(
                        f"Matched {matched} of {len(slides)} video appearances to "
                        f"{len(deck_pages)} slides in {len(deck_summaries)} deck(s)"
                    )
                glossary = load_glossary(self.glossary_path)
                if glossary:
                    print(f"Loaded {len(glossary)} glossary term(s)")
                vocabulary = transcription_vocabulary(slides, glossary=glossary)
                vocabulary_key = hashlib.sha256(
                    f"{TRANSCRIPTION_SETTINGS_VERSION}|{vocabulary}".encode("utf-8")
                ).hexdigest()[:12]
                transcript_dir = work_dir / f"transcript-{model_cache_key}-{vocabulary_key}"
                if vocabulary:
                    print(f"Whisper terminology (best first): {vocabulary[:300]}")

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
                        try:
                            segments = enhance_with_whisperx(
                                self.video,
                                segments,
                                transcript_dir,
                                self.whisperx_python,
                                self.report_progress,
                                self.hf_token,
                            )
                            aligned_cache.write_text(
                                json.dumps(segments, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        except Exception as exc:
                            print(
                                "Optional WhisperX step was skipped; continuing with the completed "
                                f"Whisper transcript: {exc}",
                                file=sys.stderr,
                                flush=True,
                            )
                            self.status.emit(
                                "WhisperX could not finish; continuing without speaker labels…"
                            )

                self.report_progress(
                    "alignment",
                    0,
                    0,
                    f"Aligning {len(segments)} speech segments and {len(self.comments)} chat comments…",
                )
                comments = self.comments
                chat_clock = ChatClockFit(0.0, 0.0, 0, "none")
                if comments:
                    if self.auto_chat_clock:
                        chat_clock = estimate_chat_clock(comments, segments)
                    elif self.manual_chat_offset:
                        chat_clock = ChatClockFit(self.manual_chat_offset, 0.0, 0, "manual")
                    comments = apply_chat_clock(comments, chat_clock)
                    print(f"Chat clock: {chat_clock.describe()}")
                align_segments(slides, segments, comments)
                omitted_slides: list[dict] = []
                if self.omit_brief_slides:
                    slides, omitted_slides = omit_incidental_slide_appearances(slides)
                    print(
                        f"Omitted {len(omitted_slides)} brief navigation slide appearances"
                    )

                self.status.emit("Waiting for slide and transcript review…")
                self.raise_if_cancelled()
                self.review_requested.emit(copy.deepcopy(slides))
                self.review_event.wait()
                if self.reviewed_slides is None:
                    raise ProcessingCancelled("Generation cancelled during review.")
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
                            "deck_label",
                            "match_confidence",
                            "match_alternative",
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

                pack_path = self.output.with_suffix(".md")
                if self.write_pack:
                    self.status.emit("Writing the Claude pack…")
                    visual_labels: dict[int, str] = {}
                    visuals_path = self.output.with_name(f"{self.output.stem} visuals.pdf")
                    if any(not slide.get("deck_slide_number") for slide in slides):
                        visual_labels = build_visuals_pdf(slides, visuals_path)
                    summary = build_claude_pack(
                        output_path=pack_path,
                        title=self.title,
                        video_name=self.video.name,
                        duration=duration,
                        slides=slides,
                        omitted=omitted_slides,
                        decks=deck_summaries,
                        comments_total=len(self.comments),
                        chat_clock=chat_clock.describe(),
                        model_name=self.model_name,
                        glossary=glossary,
                        pseudonymise=self.pseudonymise,
                        visual_labels=visual_labels,
                    )
                    self.result_files.append(pack_path)
                    if visual_labels:
                        self.result_files.append(visuals_path)
                    self.result_files.append(write_claude_instructions(pack_path.parent))
                    self.result_notes.append(
                        f"Claude pack: about {summary['approx_tokens']:,} tokens, "
                        f"{summary['slides']} slide spans, {summary['off_slide']} off-slide segment(s), "
                        f"{summary['chat_kept']} of {summary['chat_total']} chat comments kept, "
                        f"{summary['uncertain_words']} uncertain word(s) marked."
                    )
                    self.result_notes.append(f"Chat clock: {chat_clock.describe()}.")
                    print(f"Created {pack_path}")
                self.raise_if_cancelled()

                if self.write_docx:
                    self.status.emit("Creating and formatting the Word document…")
                    build_docx(
                        self.video,
                        slides,
                        duration,
                        staging_output,
                        device,
                        self.title,
                        len(omitted_slides),
                        progress_callback=self.report_progress,
                    )
                    self.raise_if_cancelled()
                    os.replace(staging_output, self.output)
                    self.result_files.insert(0, self.output)
                    print(f"Created {self.output}")
                print(f"Imported {len(self.comments)} chat comments")
            writer.flush()
            primary = self.output if self.write_docx else self.output.with_suffix(".md")
            self.completed.emit(str(primary))
        except ProcessingCancelled:
            writer.flush()
            if staging_output.exists():
                staging_output.unlink()
            shutil.rmtree(work_dir, ignore_errors=True)
            self.cancelled.emit()
        except Exception as exc:
            writer.flush()
            if staging_output.exists():
                staging_output.unlink()
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
            "The Word document keeps these as separate evidence. Use 0 when a video frame "
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
            label = (
            f"Deck {slide.get('deck_label', 'A')} slide {deck}" if deck else "Unmatched slide"
        )
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
        label = (
            f"Deck {slide.get('deck_label', 'A')} slide {deck}" if deck else "Unmatched slide"
        )
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


class WhisperXStatusThread(QThread):
    completed = Signal(object)

    def __init__(self, python_path: Path) -> None:
        super().__init__()
        self.python_path = python_path

    def run(self) -> None:
        self.completed.emit(inspect_whisperx_environment(self.python_path))


class HuggingFaceAccessThread(QThread):
    completed = Signal(bool, str)

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def run(self) -> None:
        url = (
            "https://huggingface.co/pyannote/speaker-diarization-community-1/"
            "resolve/main/config.yaml"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": f"LectureWeavePlus/{APP_VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status == 200:
                    self.completed.emit(True, "Speaker model access confirmed")
                    return
            self.completed.emit(False, "Hugging Face returned an unexpected response")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                detail = "Token rejected or pyannote model terms have not been accepted"
            else:
                detail = f"Hugging Face returned HTTP {exc.code}"
            self.completed.emit(False, detail)
        except Exception as exc:
            self.completed.emit(False, f"Could not reach Hugging Face: {exc}")


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
        self.whisperx_status_worker: WhisperXStatusThread | None = None
        self.hf_access_worker: HuggingFaceAccessThread | None = None
        self.settings = QSettings("LectureWeave", "LectureWeavePlus")
        self.last_output: Path | None = None

        self.setStyleSheet(
            f"""
            QMainWindow, QDialog, QScrollArea, QWidget#scrollBody {{ background: {BACKGROUND}; }}
            QFrame#header {{ background: {PURPLE_DARK}; }}
            QFrame#card {{ background: white; border: 1px solid {BORDER}; border-radius: 12px; }}
            QLabel {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; background: transparent; }}
            QLabel#headerTitle {{ color: white; font-size: 15pt; font-weight: 700; }}
            QLabel#headerSubtitle {{ color: #E3D6F1; font-size: 9pt; }}
            QLabel#section {{ color: {TEXT}; font-size: 11pt; font-weight: 600; }}
            QLabel#field {{ color: {MUTED}; font-size: 9pt; font-weight: 600; padding-top: 4px; }}
            QLabel#hint {{ color: {MUTED}; font-size: 9pt; }}
            QLabel#count {{ color: {PURPLE}; font-size: 9pt; font-weight: 600; }}
            QLabel#dropArea {{ background: {PURPLE_LIGHT}; color: {PURPLE}; border: 1px dashed #B9A2D6; border-radius: 10px; font-weight: 600; }}
            QFrame#consoleHelp {{ background: #FAF7FD; border: 1px solid #D8C8E7; border-radius: 8px; }}
            QFrame#segmented {{ background: #ECE8F0; border: none; border-radius: 8px; }}
            QLineEdit, QTextEdit, QComboBox {{ background: white; color: {TEXT}; border: 1px solid #CFC9D5; border-radius: 8px; padding: 8px 10px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 2px solid {PURPLE}; }}
            QListWidget {{ background: white; color: {TEXT}; border: 1px solid #CFC9D5; border-radius: 8px; padding: 4px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: {PURPLE_LIGHT}; color: {PURPLE_DARK}; }}
            QLineEdit#mono, QTextEdit#log {{ font-family: Consolas; font-size: 9pt; background: #FAF9FC; }}
            QPushButton {{ background: #ECE8F0; color: {TEXT}; border: 1px solid #D0C8D7; border-radius: 8px; padding: 8px 14px; min-height: 22px; font-family: 'Segoe UI'; font-size: 10pt; }}
            QPushButton:hover {{ background: #E2D8EC; }}
            QPushButton#primary {{ background: {PURPLE}; color: white; border: none; border-radius: 10px; font-size: 11pt; font-weight: 600; padding: 12px 20px; }}
            QPushButton#primary:hover {{ background: #5D229E; }}
            QPushButton#primary:disabled {{ background: #B7A6CC; color: white; }}
            QPushButton#quiet {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
            QPushButton#quiet:hover {{ background: #ECE8F0; }}
            QPushButton#danger {{ background: white; color: #9E2A2A; border: 1px solid #E3B7B7; font-weight: 600; }}
            QPushButton#danger:hover {{ background: #FBEDED; }}
            QPushButton#link {{ background: transparent; border: none; color: {PURPLE}; padding: 4px 2px; text-decoration: underline; font-size: 9pt; }}
            QPushButton#disclosure {{ background: transparent; border: none; color: {TEXT}; padding: 6px 0; text-align: left; font-weight: 600; }}
            QPushButton#disclosure:checked {{ color: {PURPLE}; }}
            QPushButton#segment {{ background: transparent; border: none; border-radius: 6px; color: {MUTED}; padding: 8px 10px; }}
            QPushButton#segment:checked {{ background: white; color: {PURPLE_DARK}; font-weight: 600; }}
            QPushButton#headerButton {{ background: transparent; color: white; border: 1px solid rgba(255, 255, 255, 90); padding: 8px 16px; }}
            QPushButton#headerButton:hover {{ background: rgba(255, 255, 255, 30); }}
            QPushButton:disabled {{ color: #9B96A0; background: #EEECEF; }}
            QPushButton#quiet:disabled, QPushButton#segment:disabled {{ color: #A9A3AF; background: transparent; }}
            QCheckBox {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 9pt; spacing: 8px; }}
            QProgressBar {{ border: none; background: #E6E0EE; max-height: 8px; border-radius: 4px; }}
            QProgressBar::chunk {{ background: {PURPLE}; border-radius: 4px; }}
            """
        )
        self.build_interface()
        QTimer.singleShot(0, self.refresh_whisperx_status)

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

        # Header: product identity on the left, settings on the right.
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.setSpacing(12)
        icon_path = resource_path("assets/lectureweave-plus.ico")
        if icon_path.exists():
            header_icon = QLabel()
            header_icon.setPixmap(QIcon(str(icon_path)).pixmap(36, 36))
            header_icon.setStyleSheet("background: transparent;")
            header_layout.addWidget(header_icon)
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("headerTitle")
        subtitle = QLabel(f"Slides, speech and chat—kept in sync. · Version {APP_VERSION}")
        subtitle.setObjectName("headerSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header_layout.addLayout(title_block)
        header_layout.addStretch(1)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("headerButton")
        self.settings_button.setToolTip("WhisperX, speaker labels and updates")
        self.settings_button.clicked.connect(self.open_settings)
        header_layout.addWidget(self.settings_button)
        central_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("scrollBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 24)
        body_layout.setSpacing(18)
        left_column = QVBoxLayout()
        left_column.setSpacing(14)
        right_column = QVBoxLayout()
        right_column.setSpacing(14)
        body_layout.addLayout(left_column, 3)
        body_layout.addLayout(right_column, 2)

        # Recording
        video_card, video_layout = self.card()
        video_heading = QHBoxLayout()
        video_heading.addWidget(self.section_label("Recording"))
        video_heading.addStretch(1)
        blackboard_toggle = QPushButton("Can’t download from Blackboard?")
        blackboard_toggle.setObjectName("link")
        blackboard_toggle.setCheckable(True)
        video_heading.addWidget(blackboard_toggle)
        video_layout.addLayout(video_heading)
        drop_area = DropArea()
        drop_area.file_dropped.connect(self.set_video)
        video_layout.addWidget(drop_area)
        video_row = QHBoxLayout()
        self.video_edit = DropLineEdit()
        self.video_edit.setPlaceholderText("Choose or drop a lecture recording (MP4 and most video formats)")
        self.video_edit.file_dropped.connect(self.set_video)
        browse_video = QPushButton("Browse…")
        browse_video.clicked.connect(self.browse_video)
        video_row.addWidget(self.video_edit, 1)
        video_row.addWidget(browse_video)
        video_layout.addLayout(video_row)

        console_help = QFrame()
        console_help.setObjectName("consoleHelp")
        console_help.setVisible(False)
        blackboard_toggle.toggled.connect(console_help.setVisible)
        console_help_layout = QVBoxLayout(console_help)
        console_help_layout.setContentsMargins(12, 10, 12, 10)
        console_help_layout.setSpacing(6)
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
        self.console_command.setObjectName("mono")
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
        left_column.addWidget(video_card)

        # Slide pack
        deck_card, deck_layout = self.card()
        deck_heading = QHBoxLayout()
        deck_heading.addWidget(self.section_label("Slide decks"))
        deck_heading.addWidget(self.hint_label("optional, recommended"))
        deck_heading.addStretch(1)
        deck_layout.addLayout(deck_heading)
        self.deck_list = DeckList()
        self.deck_list.files_dropped.connect(lambda paths: [self.set_deck(path) for path in paths])
        deck_layout.addWidget(self.deck_list)
        deck_buttons = QHBoxLayout()
        choose_deck = QPushButton("Add deck…")
        choose_deck.clicked.connect(self.browse_deck)
        remove_deck = QPushButton("Remove")
        remove_deck.setObjectName("quiet")
        remove_deck.clicked.connect(self.remove_selected_deck)
        clear_deck = QPushButton("Clear")
        clear_deck.setObjectName("quiet")
        clear_deck.clicked.connect(self.clear_decks)
        deck_buttons.addWidget(choose_deck)
        deck_buttons.addWidget(remove_deck)
        deck_buttons.addWidget(clear_deck)
        deck_buttons.addStretch(1)
        deck_layout.addLayout(deck_buttons)
        deck_layout.addWidget(
            self.hint_label(
                "Add every pack the lecture uses (PDF or PowerPoint); they are labelled A, B, … "
                "A PDF gives exact slide numbers, native text and clean images. Frames that match "
                "no deck are read from the video with local OCR."
            )
        )
        left_column.addWidget(deck_card)

        # Chat comments
        comments_card, comments_layout = self.card()
        comments_heading = QHBoxLayout()
        comments_heading.addWidget(self.section_label("Chat comments"))
        comments_heading.addWidget(self.hint_label("optional"))
        comments_heading.addStretch(1)
        self.comment_count = QLabel("No timestamped comments detected")
        self.comment_count.setObjectName("count")
        comments_heading.addWidget(self.comment_count)
        comments_layout.addLayout(comments_heading)
        self.comments_edit = QTextEdit()
        self.comments_edit.setAcceptRichText(False)
        self.comments_edit.setPlaceholderText(
            "Paste the copied Blackboard chat list here. Names, timestamps, messages and emoji "
            "are detected automatically."
        )
        self.comments_edit.setMinimumHeight(110)
        self.comments_edit.setMaximumHeight(170)
        self.comments_edit.textChanged.connect(self.schedule_comment_count)
        comments_layout.addWidget(self.comments_edit)
        chat_buttons = QHBoxLayout()
        paste_chat = QPushButton("Paste from clipboard")
        paste_chat.clicked.connect(self.paste_comments)
        clear_chat = QPushButton("Clear")
        clear_chat.setObjectName("quiet")
        clear_chat.clicked.connect(self.comments_edit.clear)
        chat_buttons.addWidget(paste_chat)
        chat_buttons.addWidget(clear_chat)
        chat_buttons.addStretch(1)
        comments_layout.addLayout(chat_buttons)
        chat_options = QHBoxLayout()
        self.chat_clock_check = QCheckBox("Auto-align chat clock to spoken answers")
        self.chat_clock_check.setChecked(True)
        self.chat_offset_spin = QSpinBox()
        self.chat_offset_spin.setRange(-1800, 1800)
        self.chat_offset_spin.setSuffix(" s")
        self.chat_offset_spin.setToolTip("Manual chat clock offset, used when auto-align is off")
        self.chat_offset_spin.setEnabled(False)
        self.chat_clock_check.toggled.connect(lambda checked: self.chat_offset_spin.setEnabled(not checked))
        chat_options.addWidget(self.chat_clock_check)
        chat_options.addStretch(1)
        chat_options.addWidget(self.hint_label("Manual offset"))
        chat_options.addWidget(self.chat_offset_spin)
        comments_layout.addLayout(chat_options)
        self.pseudonymise_check = QCheckBox("Pseudonymise students in the Claude pack (Student A, B, …)")
        self.pseudonymise_check.setChecked(self.settings.value("pack/pseudonymise", True, type=bool))
        self.pseudonymise_check.toggled.connect(
            lambda checked: self.settings.setValue("pack/pseudonymise", checked)
        )
        comments_layout.addWidget(self.pseudonymise_check)
        left_column.addWidget(comments_card)
        left_column.addStretch(1)

        # Output options (locked while generating)
        output_card, output_layout = self.card()
        output_layout.setSpacing(8)
        output_layout.addWidget(self.section_label("Output"))
        output_layout.addWidget(self.field_label("Title"))
        self.title_edit = QLineEdit()
        output_layout.addWidget(self.title_edit)
        output_layout.addWidget(self.field_label("Create"))
        self.pack_check = QCheckBox("Claude pack (.md) — full transcript by slide, for your study Claude")
        self.pack_check.setChecked(self.settings.value("output/pack", True, type=bool))
        self.pack_check.toggled.connect(lambda checked: self.settings.setValue("output/pack", checked))
        self.docx_check = QCheckBox("Word reading copy (.docx) — slide images and transcript")
        self.docx_check.setChecked(self.settings.value("output/docx", True, type=bool))
        self.docx_check.toggled.connect(lambda checked: self.settings.setValue("output/docx", checked))
        output_layout.addWidget(self.pack_check)
        output_layout.addWidget(self.docx_check)
        output_layout.addWidget(self.field_label("Save as"))
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        choose_output = QPushButton("Choose…")
        choose_output.clicked.connect(self.browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose_output)
        output_layout.addLayout(output_row)

        output_layout.addWidget(self.field_label("Accuracy"))
        self.model_combo = WheelSafeComboBox()
        self.model_combo.addItem("Maximum accuracy — Large-v3 (recommended)", "large-v3")
        self.model_combo.addItem("Faster — Turbo", "turbo")
        accuracy_frame = QFrame()
        accuracy_frame.setObjectName("segmented")
        accuracy_layout = QHBoxLayout(accuracy_frame)
        accuracy_layout.setContentsMargins(4, 4, 4, 4)
        accuracy_layout.setSpacing(4)
        self.accuracy_buttons: list[QPushButton] = []
        for index, label in enumerate(("Maximum · Large-v3", "Faster · Turbo")):
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, i=index: self.model_combo.setCurrentIndex(i))
            accuracy_layout.addWidget(button)
            self.accuracy_buttons.append(button)
        self.model_combo.currentIndexChanged.connect(
            lambda i: self.accuracy_buttons[i].setChecked(True) if 0 <= i < len(self.accuracy_buttons) else None
        )
        output_layout.addWidget(accuracy_frame)
        output_layout.addWidget(
            self.hint_label(
                "Large-v3 is the most accurate for long lectures. Its first use needs a one-time "
                "download of about 3 GB; LectureWeave+ asks before downloading."
            )
        )
        output_layout.addWidget(self.field_label("Unit glossary"))
        glossary_row = QHBoxLayout()
        self.glossary_edit = DropLineEdit()
        self.glossary_edit.setPlaceholderText("Optional .txt: one name, acronym or term per line")
        self.glossary_edit.setText(str(self.settings.value("glossary/path", "") or ""))
        self.glossary_edit.file_dropped.connect(self.set_glossary)
        choose_glossary = QPushButton("Choose…")
        choose_glossary.clicked.connect(self.browse_glossary)
        clear_glossary = QPushButton("Clear")
        clear_glossary.setObjectName("quiet")
        clear_glossary.clicked.connect(lambda: self.set_glossary(""))
        glossary_row.addWidget(self.glossary_edit, 1)
        glossary_row.addWidget(choose_glossary)
        glossary_row.addWidget(clear_glossary)
        output_layout.addLayout(glossary_row)
        output_layout.addWidget(
            self.hint_label(
                "Glossary terms go to Whisper first, ahead of terms taken from the slides. Use it "
                "for names, Acts, cases and acronyms the transcript keeps getting wrong."
            )
        )

        advanced_toggle = self.disclosure_button("Advanced options")
        output_layout.addWidget(advanced_toggle)
        advanced = QWidget()
        advanced.setVisible(False)
        advanced_toggle.toggled.connect(advanced.setVisible)
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(4, 0, 0, 0)
        advanced_layout.setSpacing(6)
        self.reuse_check = QCheckBox("Reuse the cached transcript for this video")
        self.reuse_check.setChecked(True)
        self.omit_check = QCheckBox("Skip brief slide flicks (three or fewer spoken words)")
        self.omit_check.setChecked(True)
        self.whisperx_check = QCheckBox("WhisperX word alignment and speaker labels")
        self.whisperx_check.setChecked(self.settings.value("whisperx/enabled", False, type=bool))
        self.whisperx_check.toggled.connect(
            lambda checked: self.settings.setValue("whisperx/enabled", checked)
        )
        for check in (self.reuse_check, self.omit_check, self.whisperx_check):
            check.setMinimumWidth(0)
            advanced_layout.addWidget(check)
        advanced_layout.addWidget(self.hint_label("Set up WhisperX and speaker labels in Settings."))
        output_layout.addWidget(advanced)
        right_column.addWidget(output_card)
        self.configuration_cards = [video_card, deck_card, comments_card, output_card]

        # Run panel: primary action, progress and results
        run_card, run_layout = self.card()
        self.start_button = QPushButton("Create lecture pack")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_processing)
        run_layout.addWidget(self.start_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        run_layout.addWidget(self.progress)
        self.status = self.hint_label("Choose or drop a lecture recording to begin.")
        run_layout.addWidget(self.status)
        result_buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.open_button = QPushButton("Open result")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setEnabled(False)
        self.folder_button.clicked.connect(self.open_output_folder)
        result_buttons.addWidget(self.cancel_button)
        result_buttons.addStretch(1)
        result_buttons.addWidget(self.open_button)
        result_buttons.addWidget(self.folder_button)
        run_layout.addLayout(result_buttons)
        log_toggle = self.disclosure_button("Technical log")
        run_layout.addWidget(log_toggle)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("log")
        self.log.setMinimumHeight(120)
        self.log.setVisible(False)
        log_toggle.toggled.connect(self.log.setVisible)
        run_layout.addWidget(self.log)
        run_layout.addWidget(self.hint_label("Processed on this PC. Nothing is uploaded."))
        right_column.addWidget(run_card)
        right_column.addStretch(1)

        scroll.setWidget(body)
        central_layout.addWidget(scroll, 1)
        self.setCentralWidget(central)

        self.build_settings_dialog()

        self.comment_timer = QTimer(self)
        self.comment_timer.setSingleShot(True)
        self.comment_timer.timeout.connect(self.update_comment_count)

    @staticmethod
    def disclosure_button(text: str) -> QPushButton:
        button = QPushButton(f"▸  {text}")
        button.setObjectName("disclosure")
        button.setCheckable(True)
        button.toggled.connect(
            lambda checked: button.setText(f"{'▾' if checked else '▸'}  {text}")
        )
        return button

    @staticmethod
    def field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("field")
        return label

    def build_settings_dialog(self) -> None:
        """One-time setup lives here, away from the per-lecture inputs."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Settings — {APP_NAME}")
        dialog.setMinimumWidth(720)
        dialog.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        speakers_card, speakers_layout = self.card()
        speakers_layout.addWidget(self.section_label("Speakers and word alignment (WhisperX)"))
        speakers_layout.addWidget(
            self.hint_label(
                "WhisperX is installed separately because its PyTorch models are very large. "
                "Word alignment works without an account; anonymous speaker labels also need an "
                "accepted pyannote model and a Hugging Face read token."
            )
        )
        whisperx_row = QHBoxLayout()
        configured_whisperx = str(
            os.environ.get("LECTUREWEAVE_WHISPERX_PYTHON")
            or self.settings.value("whisperx/python", "")
            or (default_whisperx_python() if default_whisperx_python().is_file() else "")
        )
        self.whisperx_edit = QLineEdit(configured_whisperx)
        self.whisperx_edit.setReadOnly(True)
        self.whisperx_edit.setPlaceholderText("Automatic (Python executable containing WhisperX)")
        choose_whisperx = QPushButton("Choose Python…")
        choose_whisperx.clicked.connect(self.browse_whisperx)
        install_whisperx = QPushButton("Install / repair")
        install_whisperx.clicked.connect(self.install_or_repair_whisperx)
        whisperx_row.addWidget(self.whisperx_edit, 1)
        whisperx_row.addWidget(choose_whisperx)
        whisperx_row.addWidget(install_whisperx)
        speakers_layout.addLayout(whisperx_row)
        whisperx_status_row = QHBoxLayout()
        self.whisperx_status = QLabel("Checking WhisperX setup…")
        self.whisperx_status.setWordWrap(True)
        check_whisperx = QPushButton("Check setup")
        check_whisperx.clicked.connect(self.refresh_whisperx_status)
        reset_whisperx = QPushButton("Reset path")
        reset_whisperx.setObjectName("quiet")
        reset_whisperx.clicked.connect(self.reset_whisperx_path)
        whisperx_status_row.addWidget(self.whisperx_status, 1)
        whisperx_status_row.addWidget(check_whisperx)
        whisperx_status_row.addWidget(reset_whisperx)
        speakers_layout.addLayout(whisperx_status_row)

        speakers_layout.addWidget(self.field_label("Hugging Face read token"))
        token_row = QHBoxLayout()
        self.hf_token_edit = QLineEdit()
        self.hf_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token_edit.setPlaceholderText("Paste a token beginning with hf_")
        self.save_token_button = QPushButton("Save / replace")
        self.save_token_button.clicked.connect(self.save_hf_token)
        test_token = QPushButton("Test access")
        test_token.clicked.connect(self.test_hf_token_access)
        remove_token = QPushButton("Reset token")
        remove_token.setObjectName("quiet")
        remove_token.clicked.connect(self.remove_hf_token)
        token_row.addWidget(self.hf_token_edit, 1)
        token_row.addWidget(self.save_token_button)
        token_row.addWidget(test_token)
        token_row.addWidget(remove_token)
        speakers_layout.addLayout(token_row)
        self.hf_token_status = QLabel("")
        self.hf_token_status.setWordWrap(True)
        speakers_layout.addWidget(self.hf_token_status)
        speakers_layout.addWidget(
            self.hint_label("The token is stored securely in Windows Credential Manager, never in settings or output files.")
        )
        layout.addWidget(speakers_card)
        self.refresh_hf_token_status()

        updates_card, updates_layout = self.card()
        updates_row = QHBoxLayout()
        updates_text = QVBoxLayout()
        updates_text.addWidget(self.section_label("Updates"))
        updates_text.addWidget(
            self.hint_label(f"Version {APP_VERSION} · checksum-verified installers from GitHub releases")
        )
        updates_row.addLayout(updates_text, 1)
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self.check_for_updates)
        updates_row.addWidget(self.update_button)
        updates_layout.addLayout(updates_row)
        layout.addWidget(updates_card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self.settings_dialog = dialog

    def open_settings(self) -> None:
        self.settings_dialog.exec()

    def paste_comments(self) -> None:
        text = QApplication.clipboard().text()
        if text.strip():
            self.comments_edit.setPlainText(text)
        else:
            self.status.setText("The clipboard does not contain any text to paste.")

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
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Add slide packs",
            "",
            "Slide packs (*.pdf *.pptx *.pptm);;All files (*.*)",
        )
        for path in selected:
            self.set_deck(path)

    def set_deck(self, value: str) -> None:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() not in {".pdf", ".pptx", ".pptm"}:
            QMessageBox.warning(self, APP_NAME, "Choose a PDF or PowerPoint slide pack.")
            return
        if path in self.deck_list.paths():
            return
        if self.deck_list.count() >= 26:
            QMessageBox.warning(self, APP_NAME, "Up to 26 slide packs can be added.")
            return
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        self.deck_list.addItem(item)
        self.relabel_decks()
        self.status.setText("Slide pack added. It will be matched to the video timeline automatically.")

    def relabel_decks(self) -> None:
        for row in range(self.deck_list.count()):
            item = self.deck_list.item(row)
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            item.setText(f"{chr(ord('A') + row)}   {path.name}")
            item.setToolTip(str(path))

    def remove_selected_deck(self) -> None:
        for item in self.deck_list.selectedItems():
            self.deck_list.takeItem(self.deck_list.row(item))
        self.relabel_decks()

    def clear_decks(self) -> None:
        self.deck_list.clear()

    def browse_glossary(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose a unit glossary", "", "Text files (*.txt);;All files (*.*)"
        )
        if selected:
            self.set_glossary(selected)

    def set_glossary(self, value: str) -> None:
        value = str(value).strip().strip('"')
        self.glossary_edit.setText(value)
        self.settings.setValue("glossary/path", value)
        if value:
            terms = load_glossary(Path(value))
            self.status.setText(f"Glossary loaded: {len(terms)} term(s).")

    def browse_whisperx(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Python executable containing WhisperX",
            "",
            "Python executable (python.exe);;Executables (*.exe);;All files (*.*)",
        )
        if selected:
            self.whisperx_edit.setText(selected)
            self.settings.setValue("whisperx/python", selected)
            self.whisperx_check.setChecked(True)
            self.refresh_whisperx_status()

    @staticmethod
    def set_setup_status(label: QLabel, text: str, state: str) -> None:
        colours = {
            "success": "#267A3E",
            "warning": "#9A6200",
            "error": "#B3261E",
            "checking": "#68626E",
        }
        label.setText(text)
        label.setStyleSheet(
            f"color: {colours.get(state, colours['checking'])}; font-weight: 600;"
        )

    def refresh_whisperx_status(self) -> None:
        if self.whisperx_status_worker and self.whisperx_status_worker.isRunning():
            return
        value = self.whisperx_edit.text().strip().strip('"')
        if not value and default_whisperx_python().is_file():
            value = str(default_whisperx_python())
            self.whisperx_edit.setText(value)
        if not value:
            self.set_setup_status(
                self.whisperx_status,
                "WhisperX is not installed. Use Install / repair for guided setup.",
                "warning",
            )
            return
        python_path = Path(value).resolve()
        self.set_setup_status(self.whisperx_status, "Checking WhisperX and GPU…", "checking")
        self.whisperx_status_worker = WhisperXStatusThread(python_path)
        self.whisperx_status_worker.completed.connect(
            lambda result, path=python_path: self.whisperx_status_complete(path, result)
        )
        self.whisperx_status_worker.finished.connect(self.whisperx_status_worker_finished)
        self.whisperx_status_worker.start()

    def whisperx_status_worker_finished(self) -> None:
        if self.whisperx_status_worker:
            self.whisperx_status_worker.deleteLater()
        self.whisperx_status_worker = None

    def whisperx_status_complete(self, python_path: Path, result: dict) -> None:
        if not result.get("ready"):
            self.set_setup_status(
                self.whisperx_status,
                f"WhisperX needs repair: {result.get('reason', 'environment check failed')}",
                "error",
            )
            return
        self.settings.setValue("whisperx/python", str(python_path))
        self.whisperx_edit.setText(str(python_path))
        self.whisperx_check.setChecked(True)
        if result.get("cuda_available"):
            configure_cuda_runtime(python_path)
            detail = (
                f"✓ WhisperX ready · {result.get('gpu', 'NVIDIA GPU')} · "
                f"CUDA {result.get('cuda_build', '?')} · PyTorch {result.get('torch', '?')}"
            )
            self.set_setup_status(self.whisperx_status, detail, "success")
        else:
            self.set_setup_status(
                self.whisperx_status,
                "WhisperX is installed but GPU acceleration is unavailable; processing will use CPU.",
                "warning",
            )

    def reset_whisperx_path(self) -> None:
        self.settings.remove("whisperx/python")
        standard = default_whisperx_python()
        self.whisperx_edit.setText(str(standard) if standard.is_file() else "")
        self.refresh_whisperx_status()

    def install_or_repair_whisperx(self) -> None:
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Install or repair WhisperX automatically?\n\n"
            "LectureWeave+ will open a PowerShell setup window, create a private Python 3.12 "
            "environment and download WhisperX plus CUDA-enabled PyTorch from their official "
            "package servers. Allow several gigabytes of disk space.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        script = resource_path("assets/install_whisperx.ps1")
        if not script.is_file():
            QMessageBox.critical(self, APP_NAME, "The bundled WhisperX setup script is missing.")
            return
        started, _process_id = QProcess.startDetached(
            "powershell.exe",
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            str(script.parent),
        )
        if not started:
            QMessageBox.critical(self, APP_NAME, "The WhisperX setup window could not be opened.")
            return
        self.set_setup_status(
            self.whisperx_status,
            "WhisperX setup is running in PowerShell. Click Check setup when it finishes.",
            "checking",
        )

    def refresh_hf_token_status(self) -> None:
        try:
            source = huggingface_token_source()
        except OSError:
            source = ""
        if source == "credential":
            self.set_setup_status(
                self.hf_token_status,
                "✓ Token saved securely in Windows Credential Manager · access not yet tested",
                "success",
            )
            self.save_token_button.setText("Replace token")
        elif source == "environment":
            self.set_setup_status(
                self.hf_token_status,
                "✓ Token available from the legacy HF_TOKEN environment variable",
                "success",
            )
            self.save_token_button.setText("Save securely")
        else:
            self.set_setup_status(
                self.hf_token_status,
                "No token saved · word alignment works, but speaker labels require a token",
                "warning",
            )
            self.save_token_button.setText("Save token")

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

    def test_hf_token_access(self) -> None:
        if self.hf_access_worker and self.hf_access_worker.isRunning():
            return
        try:
            token = load_huggingface_token()
        except OSError as exc:
            self.set_setup_status(self.hf_token_status, f"Token could not be read: {exc}", "error")
            return
        if not token:
            self.set_setup_status(
                self.hf_token_status,
                "No token is saved. Paste a read token and choose Save token.",
                "warning",
            )
            return
        self.set_setup_status(self.hf_token_status, "Testing pyannote model access…", "checking")
        self.hf_access_worker = HuggingFaceAccessThread(token)
        self.hf_access_worker.completed.connect(self.hf_token_access_complete)
        self.hf_access_worker.finished.connect(self.hf_access_worker_finished)
        self.hf_access_worker.start()

    def hf_access_worker_finished(self) -> None:
        if self.hf_access_worker:
            self.hf_access_worker.deleteLater()
        self.hf_access_worker = None

    def hf_token_access_complete(self, success: bool, detail: str) -> None:
        if success:
            self.set_setup_status(
                self.hf_token_status,
                "✓ Token valid · pyannote speaker-model access confirmed",
                "success",
            )
        else:
            self.set_setup_status(
                self.hf_token_status,
                f"Token needs attention: {detail}. Replace or reset it.",
                "error",
            )

    def remove_hf_token(self) -> None:
        if QMessageBox.question(
            self,
            APP_NAME,
            "Reset the saved Hugging Face token? Speaker detection will be unavailable until a new token is saved.",
        ) != QMessageBox.StandardButton.Yes:
            return
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

    def set_generation_active(self, active: bool) -> None:
        """Lock every generation setting while preserving the cancel control."""
        for card in self.configuration_cards:
            card.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.cancel_button.setEnabled(active)
        self.cancel_button.setVisible(active)
        self.update_button.setEnabled(not active)
        self.settings_button.setEnabled(not active)

    def cancel_processing(self) -> None:
        if not self.worker or not self.worker.isRunning():
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Cancel this generation and delete its temporary progress files? "
            "Previously completed Word documents will not be changed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cancel_button.setEnabled(False)
        self.status.setText("Cancelling and removing temporary progress…")
        self.worker.cancel()

    def start_processing(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        video = Path(self.video_edit.text().strip().strip('"'))
        if not video.is_file():
            QMessageBox.critical(self, APP_NAME, "Choose a valid lecture video first.")
            return
        output_text = self.output_edit.text().strip().strip('"')
        if not output_text:
            QMessageBox.critical(self, APP_NAME, "Choose where to save the results.")
            return
        if not (self.pack_check.isChecked() or self.docx_check.isChecked()):
            QMessageBox.critical(self, APP_NAME, "Choose at least one output: the Claude pack or the Word copy.")
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

        deck_paths = self.deck_list.paths()
        for deck_path in deck_paths:
            if not deck_path.is_file() or deck_path.suffix.lower() not in {".pdf", ".pptx", ".pptm"}:
                QMessageBox.critical(self, APP_NAME, f"This slide pack is missing or not a PDF or PowerPoint file:\n\n{deck_path}")
                return
        glossary_text = self.glossary_edit.text().strip().strip('"')
        glossary_path = Path(glossary_text) if glossary_text else None
        if glossary_path and not glossary_path.is_file():
            QMessageBox.critical(self, APP_NAME, "The unit glossary file could not be found.")
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
        self.set_generation_active(True)
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
            deck_paths,
            whisperx_python,
            hf_token,
            glossary_path=glossary_path,
            auto_chat_clock=self.chat_clock_check.isChecked(),
            manual_chat_offset=float(self.chat_offset_spin.value()),
            write_pack=self.pack_check.isChecked(),
            write_docx=self.docx_check.isChecked(),
            pseudonymise=self.pseudonymise_check.isChecked(),
        )
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(self.processing_progress)
        self.worker.log.connect(self.append_log)
        self.worker.review_requested.connect(self.review_processing)
        self.worker.completed.connect(self.processing_complete)
        self.worker.failed.connect(self.processing_failed)
        self.worker.cancelled.connect(self.processing_cancelled)
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
            self.status.setText("Review accepted. Creating the Word document…")
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
        self.set_generation_active(False)
        self.open_button.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.last_output = Path(value)
        files = list(getattr(self.worker, "result_files", []) or [Path(value)])
        notes = list(getattr(self.worker, "result_notes", []) or [])
        self.status.setText("Finished. " + (notes[0] if notes else "Your files are ready."))
        message = "Finished creating:\n\n" + "\n".join(f"• {path.name}" for path in files)
        message += f"\n\nIn: {files[0].parent}"
        if notes:
            message += "\n\n" + "\n".join(notes)
        QMessageBox.information(self, APP_NAME, message)

    def processing_cancelled(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.set_generation_active(False)
        self.open_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.last_output = None
        self.log.clear()
        self.status.setText("Generation cancelled. Temporary progress files were removed.")

    def processing_failed(self, value: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.set_generation_active(False)
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

