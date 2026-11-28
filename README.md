# LectureWeave+

**Slides, speech and chat—kept in sync.**

[![Download LectureWeave+ for Windows](https://img.shields.io/badge/Download_for_Windows-LectureWeave%2B-4A148C?style=for-the-badge&logo=windows)](https://github.com/lexstorey/LectureWeave-/releases/latest/download/LectureWeavePlus-Setup.exe)

[Download the Windows installer](https://github.com/lexstorey/LectureWeave-/releases/latest/download/LectureWeavePlus-Setup.exe) · [Portable ZIP](https://github.com/lexstorey/LectureWeave-/releases/latest/download/LectureWeavePlus-Portable.zip) · [All releases](https://github.com/lexstorey/LectureWeave-/releases)

LectureWeave+ is a local-first Windows desktop application that turns a lecture
recording into a **Claude pack**: a structured Markdown file that ties every
spoken sentence and chat comment to the slide on screen, ready to upload to
Claude alongside your slide pack, readings and notes. It can also create a Word
reading copy with slide images.

## Current capabilities

- Drag-and-drop local lecture videos
- A short opening animation with sound while the app loads (the sound bars
  turn into transcript lines), skippable with a click and switchable off in
  Settings
- A distinct sound, Windows notification and taskbar flash when a generation
  finishes or fails
- An optional slide-review step before export (on by default; turn it off in
  Settings to go straight from alignment to the finished files)
- **Claude pack (.md):** full verbatim transcript grouped by slide, with each
  slide's own text, a mechanical lecture header (decks, slide coverage, gaps,
  chat clock, terms index, emphasis cues), filtered chat linked to spoken
  answers, low-confidence words marked, and off-slide segments when a slide
  stays up long after the lecturer has moved on
- A visuals PDF of video frames that match no deck, so Claude can see them, and
  a `CLAUDE_INSTRUCTIONS.md` file for your Claude Project
- Students in the chat are pseudonymised (Student A, B, …) by default
- Several PDF or PowerPoint slide decks per lecture, labelled A, B, …, with real
  deck slide numbers, match confidence and deck-switch detection
- Automatic chat clock calibration from spoken echoes of chat comments, with a
  manual override
- An optional unit glossary of names, Acts, cases and acronyms for Whisper
- Local transcription with a maximum-accuracy Large-v3 mode and a faster Turbo mode
- Adaptive multi-signal slide detection, moving-region masking and stable-frame confirmation
- Global repeated-slide recognition without losing later timeline appearances
- Local RapidOCR slide text extraction when no original deck is available
- Ranked slide-derived terminology for Whisper that ignores slide-template text
  and common words
- Speech split at slide changes using word timestamps, so sentences land under
  the right slide
- Timestamped Blackboard-style chat comment import
- Review screen for correcting deck numbers, slide text and transcript before export
- Optional Word reading copy that separates slide text from the lecturer's discussion
- Generation settings lock while a job is running, with cooperative cancellation and cleanup
- Mouse-wheel-safe model selection to prevent accidental accuracy changes while scrolling
- Optional external WhisperX word alignment and anonymous speaker labels
- A masked Hugging Face token field backed by Windows Credential Manager, so
  speaker access can be saved once without placing the token in settings files
  or project output
- Automatic WhisperX environment discovery, remembered custom paths, live
  CUDA/GPU readiness feedback, token access testing, and guided install/repair
- Automatic reuse of CUDA and cuDNN libraries from the private WhisperX
  environment, allowing built-in Faster Whisper and WhisperX to share the GPU
- Private FFmpeg installation for WhisperX audio loading, compatibility with
  current and legacy diarization APIs, and graceful continuation when the
  optional WhisperX stage cannot complete
- Cached transcripts for faster regeneration
- CPU fallback and NVIDIA CUDA acceleration when available
- Live slide-scan, model-download, transcription and export progress with ETA
- Automatic resume and repair of incomplete Whisper model downloads
- Guided one-time model downloads with clear Download, Turbo and Cancel choices
- Model-specific transcript caching, so a Turbo transcript is never reused as Large-v3 output
- One-click, checksum-verified updates from official GitHub releases

## Installing and updating

Download `LectureWeavePlus-Setup.exe` from the button above. The installer adds
Start-menu and optional desktop shortcuts. Installing a newer release over an
existing copy updates the application while leaving locally cached models and
transcripts in place.

Inside LectureWeave+, select **Check for updates** to compare the installed
version with the latest GitHub release. When an update is available, the app
downloads the official installer, verifies its published SHA-256 digest, closes,
installs the update and reopens automatically.
The portable ZIP is available for users who do not want an installer, but it
must be extracted fully before the EXE is opened.

## Project status

LectureWeave+ is under active development. The current version is a working
Windows prototype. The Claude pack format and the remaining work are described
in [CLAUDE_PACK_PLAN.md](CLAUDE_PACK_PLAN.md); see also [ROADMAP.md](ROADMAP.md).

Maintainers can publish an updated installer by following
[RELEASING.md](RELEASING.md).

## Privacy and rights

Processing is local by default. Only process recordings and comments you are
authorised to access and use. Automatic transcripts can contain errors and
should be checked against the recording when accuracy matters.

## Licence

LectureWeave+ is released under the [MIT License](LICENSE). See
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) for bundled dependencies and
attribution requirements.

