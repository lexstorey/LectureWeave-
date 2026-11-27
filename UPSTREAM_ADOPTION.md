# Upstream adoption plan

This document records exactly what LectureWeave+ should port from the two
strongest slide-detection candidates. It is an engineering and licensing plan,
not a record of code already incorporated.

The reviewed snapshots are:

- [`itsyujay/video-slide-extractor`](https://github.com/itsyujay/video-slide-extractor)
  at commit `221454d1cc296e75a2ddf83cd3ed587d5922c232` (MIT; copyright
  2026 Video2Any).
- [`aggarwal-shiv/LectureLens`](https://github.com/aggarwal-shiv/LectureLens)
  at commit `05b69841bde1555f516892403052ec57ddcaa5e4` (MIT; copyright
  2025 Shivam Aggarwal).

## Decision summary

| Order | Upstream component | Decision | Why |
|---|---|---|---|
| 1 | `video-slide-extractor/index.js`: `frameDiff` | Port to NumPy/OpenCV | Block-level mean difference is much less sensitive to compression noise than LectureWeave+'s current whole-image mean difference. |
| 2 | `video-slide-extractor/index.js`: `buildActivityMask` | Port to NumPy/OpenCV | Directly addresses webcam tiles, moving cursors and animated logos without cropping away real slide content. |
| 3 | `video-slide-extractor/index.js`: `calibrateThreshold`, `chooseThreshold`, `analyzeSamples` | Port to Python | Replaces the fixed `2.0` threshold with footage-specific calibration and a safe fallback for static or high-motion recordings. |
| 4 | `LectureLens.py`: `SlideDetector.process` stable-frame confirmation | Adapt, do not copy wholesale | Prevents a transition frame or quick navigation flick from becoming the exported slide. It fits LectureWeave+'s existing timeline better as a small state machine. |
| 5 | `LectureLens.py`: `compute_phash`, `phash_distance`, `deduplicate` | Adapt after detection work | Detects a lecturer returning to an earlier slide. Keep the new timeline occurrence but reuse the canonical image identity. |
| 6 | `LectureLens.py`: `compute_histogram`, `_is_different` majority vote | Add only as a fallback | SSIM/pHash/HSV voting is useful for borderline candidates but is heavier than block differencing and should not run on every sampled frame. |

## What to port from video-slide-extractor

### `index.js: frameDiff`

Port the algorithm, defaults and tests, not the JavaScript runtime:

- Downsample a cropped lecture frame once.
- Divide it into blocks (upstream default: 8 px at analysis resolution).
- Compute mean absolute RGB difference per block.
- Mark a block changed when its mean exceeds `blockDelta` (upstream default:
  14).
- Score the fraction of unmasked blocks that changed.

Local target: replace `signature_difference` as the primary decision signal in
`lecture_to_docx.py`, while retaining the current grayscale signature as a
cheap secondary value for adjacent-state merging during the first iteration.

### `index.js: buildActivityMask`

Port the per-block activity counter:

- Compare consecutive calibration frames and collect changed-block flags.
- Mask blocks active in more than half of sampled pairs.
- Reject the mask if it covers more than 35% of the analysis frame.
- Use the mask only when the masked footage still classifies as a static or
  bimodal deck; discard it for genuine moving-camera footage.

Local target: a new `slide_detection.py` module so the algorithm can be unit
tested without video decoding or Word generation.

### `index.js: calibrateThreshold`, `chooseThreshold`, `analyzeSamples`

Port the one-dimensional Otsu-style split and its guardrails:

- Require at least six ratios.
- Trust the split only when the high cluster is sufficiently separated.
- Treat footage with at least 60% near-static pairs as a deck.
- Fall back to median plus twice MAD for motion-heavy footage.
- Return both the chosen value and a mode (`default`, `static`, `bimodal`, or
  `motion`) for logging and diagnostics.

Do not port `createSlideDetector` or `extractSlideIndices` verbatim. The current
LectureWeave+ pipeline needs duration-aware boundaries, clean recapture and
transcript alignment, so these two small wrappers add little value.

### Tests to translate

Translate focused cases from `test/index.test.js` into Python tests:

- identical/compression-noise frames stay together;
- a meaningful block change is detected;
- moving overlay blocks are masked;
- masks that cover most of the picture are rejected;
- clean bimodal footage receives a calibrated threshold;
- static and motion-heavy footage select safe fallbacks.

Also translate the dwell-capture boundary cases in `test/cli.test.js`, notably
never seeking into the next slide and never seeking past the video end.

## What to adapt from LectureLens

### Stable-frame confirmation

Adapt the state transition in `LectureLens.py: SlideDetector.process`:

1. Detect a candidate change using the calibrated block ratio.
2. Wait for two or three consecutive samples that are mutually stable.
3. Compare the settled candidate with the last accepted slide.
4. Record the boundary at the beginning of the change, but capture the image
   from inside the settled dwell.

This should replace the current assumption that capturing approximately one
second after every threshold crossing always lands on a clean slide.

### Global perceptual deduplication

Adapt `compute_phash`, `phash_distance` and the useful part of `deduplicate`:

- Calculate a perceptual hash for every accepted settled slide.
- Compare against all earlier canonical slides, not only the previous ten;
  lecture decks are small enough for this to be inexpensive.
- If a lecturer returns to an old slide, retain the new timeline occurrence and
  transcript interval but reference the original canonical slide image.
- Do not replace an earlier timeline item with the later one, as LectureLens
  currently does; that would lose information important to lecture notes.

Implement the hash locally with NumPy/OpenCV or the small `ImageHash` package.
Do not add `scikit-image` merely for SSIM until the fallback demonstrably
improves the benchmark; it materially increases installer size.

### Majority-vote verification

For candidates close to the calibrated threshold, adapt
`compute_histogram` and `_is_different` to combine:

- structural/grayscale similarity;
- perceptual-hash distance; and
- HSV histogram correlation.

Require two of three signals when all are available. This is a verification
stage, not the first-pass scanner. The goal is to reject transitions and minor
animations without tripling the cost of scanning a two-hour lecture.

### What not to copy

Do not copy LectureLens's Tkinter GUI, YouTube downloader, PaddleOCR wrapper,
PDF exporter, packaging scripts or its entire single-file architecture. Those
parts either duplicate working LectureWeave+ features or introduce unnecessary
dependencies. RapidOCR remains the better planned OCR fit for this app.

## Proposed local design

Create `slide_detection.py` containing small pure functions and typed records:

- `block_change_ratio(previous, current, config, mask=None)`
- `build_activity_mask(frames, config)`
- `choose_change_threshold(ratios, config)`
- `find_stable_slide_events(samples, config)`
- `perceptual_hash(frame)` and `hash_distance(left, right)`
- `assign_canonical_slides(events, config)`

Keep video decoding and full-resolution image capture in `lecture_to_docx.py`.
This separation lets synthetic-array tests cover the algorithms quickly and
keeps Word export concerns out of the detector.

## Benchmark and acceptance gate

Before replacing current behaviour, save a baseline from
`Legislation video.mp4` and label the true slide changes. Compare old and new
detectors on:

- slide-change precision and recall within a two-second tolerance;
- number of transition or navigation frames exported;
- repeated-slide recognition;
- short appearances with three or fewer transcript words;
- runtime and peak memory; and
- installer-size change.

The new detector should not become the default unless it removes the known
one-or-two-word false slides without missing real content slides. Keep an
Advanced switch for the legacy detector for one release so results are
recoverable.

## Attribution when implementation begins

As soon as any of the code above is ported or substantially adapted:

1. Add both upstream copyright notices and their full MIT licence text to
   `THIRD_PARTY_NOTICES.txt`.
2. Put an attribution comment at the top of `slide_detection.py` with the
   repository URLs and pinned commits.
3. State which functions were translated or modified.
4. Preserve the commit hashes in this document even when later upstream
   revisions are reviewed.
5. Add the new local tests before enabling the detector in the GUI.

