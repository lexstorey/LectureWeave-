from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DRAG_AND_DROP_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    DRAG_AND_DROP_AVAILABLE = False

from lecture_to_docx import (
    align_segments,
    build_docx,
    detect_slides,
    omit_incidental_slide_appearances,
    parse_chat_comments,
    transcribe,
    write_transcript_files,
)


APP_NAME = "Lecture Transcript + Slides"
APP_PURPLE = "#4A148C"
APP_PURPLE_DARK = "#32105D"
APP_PURPLE_LIGHT = "#F2ECFA"
APP_ORANGE = "#C65A19"
APP_BACKGROUND = "#F5F4F8"
APP_TEXT = "#26232B"


SELF_TEST_COMMENTS = """- **AMANDA SCHWARTZ**

  **00:47**

  Yep sure can
- DAO ROCHVARGER-WONG

  **02:44**

  yes. thanks
"""


class QueueWriter(io.TextIOBase):
    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        self.buffer = ""

    def write(self, value: str) -> int:
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.event_queue.put(("log", line.strip()))
        return len(value)

    def flush(self) -> None:
        if self.buffer.strip():
            self.event_queue.put(("log", self.buffer.strip()))
        self.buffer = ""


def create_root():
    if DRAG_AND_DROP_AVAILABLE:
        return TkinterDnD.Tk()
    return tk.Tk()


class LectureTranscriptApp:
    def __init__(self, root) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x820")
        self.root.minsize(820, 680)
        self.root.configure(bg=APP_BACKGROUND)

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_output: Path | None = None
        self.comment_parse_after: str | None = None

        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose or drop a lecture recording to begin.")
        self.comment_count_var = tk.StringVar(value="No timestamped comments detected")
        self.reuse_var = tk.BooleanVar(value=True)
        self.omit_brief_var = tk.BooleanVar(value=True)

        self.configure_styles()
        self.build_interface()
        self.root.after(100, self.process_events)

    def configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=APP_BACKGROUND)
        style.configure("Card.TFrame", background="white")
        style.configure(
            "Section.TLabel",
            background="white",
            foreground=APP_TEXT,
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Hint.TLabel",
            background="white",
            foreground="#66616C",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Count.TLabel",
            background="white",
            foreground=APP_PURPLE,
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI Semibold", 11),
            padding=(18, 9),
        )
        style.configure("Secondary.TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("TCheckbutton", background="white", font=("Segoe UI", 9))
        style.configure(
            "Purple.Horizontal.TProgressbar",
            background=APP_PURPLE,
            troughcolor="#DED9E6",
        )

    def build_interface(self) -> None:
        header = tk.Frame(self.root, bg=APP_PURPLE_DARK, padx=30, pady=22)
        header.pack(fill="x")
        tk.Label(
            header,
            text=APP_NAME,
            bg=APP_PURPLE_DARK,
            fg="white",
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Turn a lecture video into a timestamped Word transcript with its slides and chat comments.",
            bg=APP_PURPLE_DARK,
            fg="#E8DEF2",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        canvas = tk.Canvas(self.root, bg=APP_BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas, style="App.TFrame", padding=(26, 20, 26, 28))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_window, width=event.width))
        canvas.bind_all(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
        )

        video_card = ttk.Frame(body, style="Card.TFrame", padding=18)
        video_card.pack(fill="x", pady=(0, 14))
        ttk.Label(video_card, text="1. Lecture video", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            video_card,
            text="Drop an MP4 here, or browse to it. Other common video formats may also work.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 10))

        drop_row = ttk.Frame(video_card, style="Card.TFrame")
        drop_row.pack(fill="x")
        self.video_entry = ttk.Entry(drop_row, textvariable=self.video_var, font=("Segoe UI", 10))
        self.video_entry.pack(side="left", fill="x", expand=True, ipady=7)
        ttk.Button(
            drop_row,
            text="Browse…",
            style="Secondary.TButton",
            command=self.browse_video,
        ).pack(side="left", padx=(10, 0))

        self.drop_zone = tk.Label(
            video_card,
            text="DROP VIDEO HERE" if DRAG_AND_DROP_AVAILABLE else "Use Browse to choose a video",
            bg=APP_PURPLE_LIGHT,
            fg=APP_PURPLE,
            font=("Segoe UI Semibold", 10),
            relief="solid",
            bd=1,
            padx=12,
            pady=12,
        )
        self.drop_zone.pack(fill="x", pady=(10, 0))
        if DRAG_AND_DROP_AVAILABLE:
            for widget in (self.drop_zone, self.video_entry):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)

        comments_card = ttk.Frame(body, style="Card.TFrame", padding=18)
        comments_card.pack(fill="both", expand=True, pady=(0, 14))
        comment_heading = ttk.Frame(comments_card, style="Card.TFrame")
        comment_heading.pack(fill="x")
        ttk.Label(comment_heading, text="2. Chat comments (optional)", style="Section.TLabel").pack(
            side="left"
        )
        ttk.Label(comment_heading, textvariable=self.comment_count_var, style="Count.TLabel").pack(
            side="right"
        )
        ttk.Label(
            comments_card,
            text="Paste the copied Blackboard chat list. Names, timestamps and messages are detected automatically.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 9))

        text_frame = ttk.Frame(comments_card, style="Card.TFrame")
        text_frame.pack(fill="both", expand=True)
        self.comments_text = tk.Text(
            text_frame,
            height=12,
            wrap="word",
            undo=True,
            font=("Segoe UI", 10),
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            foreground=APP_TEXT,
        )
        comment_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.comments_text.yview)
        self.comments_text.configure(yscrollcommand=comment_scroll.set)
        self.comments_text.pack(side="left", fill="both", expand=True)
        comment_scroll.pack(side="right", fill="y")
        self.comments_text.bind("<KeyRelease>", self.schedule_comment_count)

        output_card = ttk.Frame(body, style="Card.TFrame", padding=18)
        output_card.pack(fill="x", pady=(0, 14))
        ttk.Label(output_card, text="3. Document details", style="Section.TLabel").pack(anchor="w")

        title_row = ttk.Frame(output_card, style="Card.TFrame")
        title_row.pack(fill="x", pady=(11, 8))
        ttk.Label(title_row, text="Title", width=11, style="Hint.TLabel").pack(side="left")
        ttk.Entry(title_row, textvariable=self.title_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, ipady=5
        )

        output_row = ttk.Frame(output_card, style="Card.TFrame")
        output_row.pack(fill="x")
        ttk.Label(output_row, text="Save as", width=11, style="Hint.TLabel").pack(side="left")
        ttk.Entry(output_row, textvariable=self.output_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, ipady=5
        )
        ttk.Button(
            output_row,
            text="Choose…",
            style="Secondary.TButton",
            command=self.browse_output,
        ).pack(side="left", padx=(10, 0))

        ttk.Checkbutton(
            output_card,
            text="Reuse a cached local transcript when this exact video has already been processed",
            variable=self.reuse_var,
        ).pack(anchor="w", pady=(11, 0))
        ttk.Checkbutton(
            output_card,
            text="Omit brief slide flicks with three or fewer spoken words (recommended)",
            variable=self.omit_brief_var,
        ).pack(anchor="w", pady=(5, 0))

        action_card = ttk.Frame(body, style="Card.TFrame", padding=18)
        action_card.pack(fill="x")
        self.progress = ttk.Progressbar(
            action_card,
            mode="indeterminate",
            style="Purple.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")
        ttk.Label(action_card, textvariable=self.status_var, style="Hint.TLabel").pack(
            anchor="w", pady=(7, 9)
        )

        self.log_text = tk.Text(
            action_card,
            height=5,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            bg="#FAF9FC",
            fg="#4B4650",
            relief="solid",
            bd=1,
            padx=8,
            pady=6,
        )
        self.log_text.pack(fill="x", pady=(0, 11))

        button_row = ttk.Frame(action_card, style="Card.TFrame")
        button_row.pack(fill="x")
        self.start_button = ttk.Button(
            button_row,
            text="Create Word document",
            style="Primary.TButton",
            command=self.start_processing,
        )
        self.start_button.pack(side="left")
        self.open_button = ttk.Button(
            button_row,
            text="Open finished document",
            style="Secondary.TButton",
            command=self.open_output,
            state="disabled",
        )
        self.open_button.pack(side="left", padx=(10, 0))

    def browse_video(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose lecture recording",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.set_video(Path(selected))

    def on_drop(self, event) -> None:
        dropped = self.root.tk.splitlist(event.data)
        if dropped:
            self.set_video(Path(dropped[0]))

    def set_video(self, path: Path) -> None:
        path = path.expanduser().resolve()
        self.video_var.set(str(path))
        self.title_var.set(f"{path.stem} Transcript and Slides")
        self.output_var.set(str(path.with_name(f"{path.stem} Transcript and Slides.docx")))
        self.status_var.set("Video selected. Paste chat comments if you have them, then create the document.")

    def browse_output(self) -> None:
        initial = Path(self.output_var.get()) if self.output_var.get() else Path.cwd() / "Transcript.docx"
        selected = filedialog.asksaveasfilename(
            title="Save Word document",
            defaultextension=".docx",
            initialdir=str(initial.parent),
            initialfile=initial.name,
            filetypes=[("Word document", "*.docx")],
        )
        if selected:
            self.output_var.set(selected)

    def schedule_comment_count(self, _event=None) -> None:
        if self.comment_parse_after:
            self.root.after_cancel(self.comment_parse_after)
        self.comment_parse_after = self.root.after(250, self.update_comment_count)

    def update_comment_count(self) -> None:
        self.comment_parse_after = None
        text = self.comments_text.get("1.0", "end-1c")
        comments = parse_chat_comments(text) if text.strip() else []
        if comments:
            self.comment_count_var.set(f"{len(comments)} timestamped comment(s) detected")
        elif text.strip():
            self.comment_count_var.set("No recognised timestamped comments")
        else:
            self.comment_count_var.set("No timestamped comments detected")

    def append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start_processing(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        video = Path(self.video_var.get().strip().strip('"'))
        output = Path(self.output_var.get().strip().strip('"'))
        if not video.is_file():
            messagebox.showerror(APP_NAME, "Choose a valid lecture video first.")
            return
        if output.suffix.lower() != ".docx":
            output = output.with_suffix(".docx")
            self.output_var.set(str(output))
        title = self.title_var.get().strip() or f"{video.stem} Transcript and Slides"
        raw_comments = self.comments_text.get("1.0", "end-1c")
        comments = parse_chat_comments(raw_comments) if raw_comments.strip() else []
        if raw_comments.strip() and not comments:
            if not messagebox.askyesno(
                APP_NAME,
                "No timestamped chat comments were recognised. Continue without importing them?",
            ):
                return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Starting local processing…")
        self.last_output = None

        self.worker = threading.Thread(
            target=self.process_video,
            args=(
                video.resolve(),
                output.resolve(),
                title,
                comments,
                bool(self.reuse_var.get()),
                bool(self.omit_brief_var.get()),
            ),
            daemon=True,
        )
        self.worker.start()

    def process_video(
        self,
        video: Path,
        output: Path,
        title: str,
        comments: list[dict],
        reuse_transcript: bool,
        omit_brief_slides: bool,
    ) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_root = app_data / "LectureTranscriptSlides"
        stat = video.stat()
        fingerprint = hashlib.sha256(
            f"{video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:20]
        work_dir = cache_root / "jobs" / fingerprint
        slides_dir = work_dir / "slides"
        transcript_dir = work_dir / "transcript"
        model_cache = cache_root / "models"
        work_dir.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)

        writer = QueueWriter(self.events)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                self.events.put(("status", "Detecting stable slide changes…"))
                slides, duration = detect_slides(video, slides_dir)
                print(f"Detected {len(slides)} stable slide appearances")

                transcript_json = transcript_dir / "transcript.json"
                if reuse_transcript and transcript_json.exists():
                    self.events.put(("status", "Loading the cached local transcript…"))
                    segments = json.loads(transcript_json.read_text(encoding="utf-8"))
                    device = "reused"
                    print(f"Loaded {len(segments)} cached speech segments")
                else:
                    self.events.put(
                        (
                            "status",
                            "Transcribing locally—this is the longest step, especially on CPU…",
                        )
                    )
                    segments, device = transcribe(video, work_dir, "turbo", model_cache=model_cache)
                    write_transcript_files(segments, transcript_dir)
                    print(f"Transcribed {len(segments)} timestamped speech segments")

                align_segments(slides, segments, comments)
                omitted_slides: list[dict] = []
                if omit_brief_slides:
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

                self.events.put(("status", "Creating and formatting the Word document…"))
                build_docx(
                    video,
                    slides,
                    duration,
                    output,
                    device,
                    title,
                    len(omitted_slides),
                )
                print(f"Imported {len(comments)} chat comments")
                print(f"Created {output}")
            writer.flush()
            self.events.put(("done", str(output)))
        except Exception as exc:
            writer.flush()
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self.progress.stop()
                    self.start_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.last_output = Path(payload)
                    self.status_var.set("Finished. The Word document is ready.")
                    messagebox.showinfo(APP_NAME, f"Finished creating:\n\n{payload}")
                elif kind == "error":
                    self.progress.stop()
                    self.start_button.configure(state="normal")
                    self.status_var.set("Processing stopped because of an error.")
                    self.append_log(payload)
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            os.startfile(self.last_output)


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
    root = create_root()
    LectureTranscriptApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

