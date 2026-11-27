# LectureWeave+

**Slides, speech and chat—kept in sync.**

LectureWeave+ is a local-first Windows desktop application that converts a
lecture recording into a timestamped Word document containing detected slides,
machine-generated transcript text and optional chat comments.

## Current capabilities

- Drag-and-drop local lecture videos
- Local transcription with Faster Whisper
- Stable slide detection and brief-transition filtering
- Timestamped Blackboard-style chat comment import
- Word document generation with slide images and aligned transcript text
- Cached transcripts for faster regeneration
- CPU fallback and NVIDIA CUDA acceleration when available

## Project status

LectureWeave+ is under active development. The current version is a working
Windows prototype. Planned improvements include OCR-derived terminology,
confidence-aware transcription repair, adaptive slide detection, a synchronized
review workspace and Claude-ready Markdown export. See [ROADMAP.md](ROADMAP.md).

## Privacy and rights

Processing is local by default. Only process recordings and comments you are
authorised to access and use. Automatic transcripts can contain errors and
should be checked against the recording when accuracy matters.

