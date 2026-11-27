# LectureWeave+ roadmap

## Product decision

- Final product name: **LectureWeave+**
- Internal package/cache identifier: **LectureWeavePlus** (avoid `+` in URLs,
  registry keys and machine-readable identifiers while retaining it in the
  visible product name).
- Tagline: **Slides, speech and chat—kept in sync.**
- The primary output becomes a Claude-ready Markdown pack; the Word document
  remains an optional reading copy. See [CLAUDE_PACK_PLAN.md](CLAUDE_PACK_PLAN.md).
- LectureWeave+ handles perception and alignment (audio, slides, chat, speakers);
  the receiving Claude handles interpretation. Local LLM summarisation is parked.
- Processing remains local by default.

## Brand and application assets

The supplied icon set has been accepted as the product artwork. The ICO has
16, 24, 32, 48, 64, 128 and 256-pixel layers and includes a simplified design
for small Windows sizes.

Planned branding work:

1. Rename the window, executable, shortcuts, About panel and documentation to
   LectureWeave+.
2. Embed `lectureweave-plus.ico` in the EXE and installer and set it as the
   Qt window/taskbar icon.
3. Use the full SVG for documentation and the simplified SVG for small-size
   regeneration.
4. Keep compatibility with the existing cache. Migrate or continue reading the
   old `%LOCALAPPDATA%\LectureTranscriptSlides` folder so existing transcripts
   are not lost.
5. Add a third-party notices file before importing external code.

## Phase 1 — transcription accuracy

### Quality modes

- **Fast:** Faster Whisper `turbo`, the current default.
- **Best:** `large-v3`, with an explicit warning about processing time and
  memory use.
- **Smart:** transcribe with `turbo`, detect questionable regions, and rerun
  only those regions using the more accurate configuration.

### Course vocabulary

- Add course profiles containing lecturer names, student names, abbreviations,
  legislation, cases and specialist terminology.
- Add an editable glossary box to each job.
- OCR detected slides before transcription and extract uncommon words as
  Whisper hotwords.
- Retain the current legal prompt as an optional "Australian law" preset rather
  than applying it to every subject.

### Timing and confidence

- Enable word-level timestamps.
- Store word probability, segment average log probability, compression ratio
  and no-speech probability in the cached transcript.
- Highlight low-confidence words in the review screen rather than silently
  presenting everything as equally certain.
- Use word timestamps to place chat messages and slide boundaries more
  accurately.

### Automatic repair

- Detect repeated phrases, excessive compression, low-confidence passages and
  speech emitted during silence.
- Rerun suspicious windows with previous-text conditioning disabled and, in
  Best/Smart mode, `large-v3`.
- Preserve the original result in the project data so repairs can be audited or
  reversed.

## Phase 2 — slide detection and OCR

1. Replace fixed thresholds with per-video calibration.
2. Mask always-moving regions such as webcam tiles, cursors and animated logos.
3. Combine SSIM, perceptual hash and HSV-histogram signals with temporal
   confirmation before accepting a slide.
4. Capture a frame from inside the stable dwell period rather than at the
   transition boundary.
5. Perform global perceptual deduplication so returning to an earlier slide is
   recognised without losing its new timeline occurrence.
6. Collapse progressive bullet animations into the final stable state by
   default, with an option to retain each build.
7. Add local OCR using RapidOCR/ONNX. Use OCR both for searchable output and to
   build the transcription glossary.
8. Keep the existing brief-slide filter, but expose duration and spoken-word
   thresholds in Advanced settings.

## Phase 3 — review workspace

- Add a review screen containing video playback, transcript, chat messages and
  a slide thumbnail strip.
- Clicking text or a slide seeks the video to that timestamp.
- Allow transcript corrections, slide deletion/restoration, slide merge and
  boundary adjustment before export.
- Display uncertain transcript words and suspected duplicate/transition slides
  as review items.
- Save edits as project data, not only inside the generated Word file.

## Phase 4 — exports for the real workflow

- Add the Claude pack: XML-tagged Markdown with a mechanical lecture header,
  one element per slide span or off-slide segment, full slide text, verbatim
  transcript paragraphs, filtered chat and pseudonymised students. The format is
  specified in [CLAUDE_PACK_PLAN.md](CLAUDE_PACK_PLAN.md).
- Add a visuals PDF only when no deck was supplied or off-deck frames were shown
  (Claude reads PDF pages visually but not images inside .docx files).
- Generate `CLAUDE_INSTRUCTIONS.md` for the receiving Claude Project.
- Keep the Word handout as an optional reading copy, with deck slide numbers in
  headings.
- Add a self-contained JSON project artifact for future regeneration.
- Add SRT and VTT exports.

## Phase 5 — desktop-product improvements

- Project history with reopen, regenerate and delete controls.
- Course profiles and reusable glossaries.
- Background queue with pause, cancel, retry and per-stage progress.
- Hardware/model status showing CPU or CUDA and estimated model storage.
- Model and cache manager with safe cleanup controls.
- Proper Windows installer, Start-menu shortcut, uninstall entry and upgrade
  support.
- Crash log with a Copy diagnostics button.
- Automated clean-machine packaging and launch tests.

## Phase 6 — optional advanced features

- WhisperX forced alignment for more precise timestamps.
- Optional anonymous speaker diarization for lecturer/student Q&A.
- Search across a course's transcripts and slide OCR.
- Local LLM summarisation is parked: the receiving Claude produces better notes
  from the verbatim pack. Revisit only for Claude-free or no-upload use.

## GitHub reuse shortlist

Only reuse code with an explicit compatible license. Record the exact upstream
commit, retain attribution and include the applicable license in
`THIRD_PARTY_NOTICES.txt`.

The exact file/function audit and pinned commits for the first two ports are in
[`UPSTREAM_ADOPTION.md`](UPSTREAM_ADOPTION.md).

| Repository | License | What LectureWeave+ should reuse | Decision |
|---|---|---|---|
| [aggarwal-shiv/LectureLens](https://github.com/aggarwal-shiv/LectureLens) | MIT | SSIM + pHash + HSV majority voting, temporal smoothing, transition rejection and queue ideas | **High priority.** Port the detector concepts into the existing Python pipeline and benchmark them against the current lecture fixture. |
| [itsyujay/video-slide-extractor](https://github.com/itsyujay/video-slide-extractor) | MIT | Block-based frame difference, Otsu threshold calibration, moving-region masks and stable-dwell recapture | **High priority.** Port the algorithms to NumPy/OpenCV instead of adding Node.js to the desktop package. |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | Lightweight ONNX OCR, model downloading and text coordinates | **High priority.** Best fit for the existing Python/ONNX Runtime package. Make the OCR model an optional first-use download. |
| [rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | MIT | Fuzzy matching of uncertain transcript words against slide OCR and the course glossary | **High priority.** Apply only to low-confidence words and always keep the original text. |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Word timestamps, confidence metadata, hotwords, VAD tuning and selective clip transcription | **Already used.** Expose more of its existing capabilities before replacing the ASR engine. |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | BSD-2-Clause | Forced alignment and optional speaker diarization | **Optional plugin.** Do not include in the default build because PyTorch/pyannote greatly increase size and setup complexity; check model licences separately. |
| [Huyhoclaptrinh/lecturelens](https://github.com/Huyhoclaptrinh/lecturelens) | MIT | Global slide deduplication, Markdown/JSON output and interactive HTML viewer patterns | **Medium priority.** Reuse focused components, not its heavy VLM/Ollama default pipeline. |
| [vaibhav-space/vindex](https://github.com/vaibhav-space/vindex) | MIT | Typed, versioned timeline/ASR/OCR artifacts and confidence-preserving schemas | **Architecture reference.** Its default runtime is not Windows-first, but its project-artifact design is valuable. |
| [wansui976/CoursePilot](https://github.com/wansui976/CoursePilot) | MIT | Course profiles, background queue, retry-by-stage, transcript versioning, timestamped notes and handout UX | **UX reference.** Tauri/React/Rust is not directly compatible with the current PySide6 application. |
| [StaffBao/meeting-slide-extractor](https://github.com/StaffBao/meeting-slide-extractor) | MIT | Editable HTML viewer, speaker-oriented transcript export and validation tests | **Medium priority.** Inspect individual scripts and tests before choosing what to port. |
| [chidiwilliams/Buzz](https://github.com/chidiwilliams/buzz) | MIT | Plugin pipeline, transcript regrouping and export-after-edit patterns | **Ideas only.** Do not adopt the whole application or architecture; independently test any isolated component. |
| [SubtitleEdit/subtitleedit](https://github.com/SubtitleEdit/subtitleedit) | MIT | Video/transcript/waveform editing interaction patterns | **Design reference.** It is a large C# application, so direct code reuse would not suit the Python/Qt codebase. |
| [Bernardo-Andreatta/Stradiz-Transcriber](https://github.com/Bernardo-Andreatta/Stradiz-Transcriber) | MIT | Hallucination recovery, editable timestamped catalog and separately versioned engine downloads | **Design reference.** Electron code is not a direct fit, but the failure-recovery and updater approach are useful. |

## Repositories not to absorb wholesale

- Do not replace the current application with another project's complete UI or
  pipeline. LectureWeave+ already has a smaller working core tailored to the
  slide/transcript/chat Word workflow.
- Do not import GPL or source-available code without a deliberate licensing
  decision.
- Do not assume that a repository's licence also covers its model weights,
  fonts, sample media or cloud services.
- Do not add Ollama, PyTorch or a browser runtime to the default installer until
  the value justifies the download and maintenance cost.

## Recommended implementation order

Items 1–4 of the original order (branding, word timestamps, RapidOCR hotwords,
adaptive detection) and the installer/updater and optional WhisperX stages are
in place. The order from here follows [CLAUDE_PACK_PLAN.md](CLAUDE_PACK_PLAN.md):

1. Vocabulary pipeline fix: filter template text and common words, rank terms,
   per-unit glossary, generic default prompt.
2. Chat clock offset calibration.
3. Word-timestamp slide boundary splitting.
4. Repetition guard and suspicious-segment flags.
5. Claude pack exporter, paragraph merging, pseudonymisation and header.
6. Multi-deck matching, deck re-matching and long-dwell off-slide detection.
7. Visuals PDF and speaker naming.
8. Smart selective retranscription and the full review workspace.

Each stage should be measured against the existing Legislation lecture fixture
before it replaces the current behaviour. Preserve the current result as the
baseline and compare slide precision, missed slides, false transition slides,
transcription corrections, runtime and installer size.

