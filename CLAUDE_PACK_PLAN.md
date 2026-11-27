# LectureWeave+ Claude Pack Plan

This plan replaces the earlier local-LLM summarisation plan. Its reasoning and
specification come from a structured review by the Claude session that consumes
LectureWeave+ output for study, recorded in
[docs/study-claude-feedback.md](docs/study-claude-feedback.md).

## Decision

LectureWeave+ does the jobs that need the recording, so a chat model cannot do
them:

- hearing the audio;
- knowing which slide was on screen at each moment;
- reading the screen;
- placing chat on the timeline;
- telling speakers apart.

The receiving Claude does the understanding: what is new compared with the
slide, what matters for assessment, and how it links to readings and notes.

The local LLM stage (Qwen3-8B on llama.cpp) is **parked**. Its per-slide
summaries would be a lossy paraphrase made by a weaker model. The receiving
Claude would need to check them against the transcript anyway. The work also
needs the lecturer's exact words for journals and essays, and a summary cannot
provide them.

Guiding rule: **fidelity over token count.** A 2-hour lecture is roughly
40–50k tokens, which fits comfortably in Claude's context. A wrong section
number, a misattributed quote or a dropped student question costs far more than
tokens.

## Outputs

| Output | Purpose | When |
|---|---|---|
| `<unit>_<session>_<title>.md` (the Claude pack) | Primary output for Claude | Every run |
| `<…>_visuals.pdf` | Deduplicated frames Claude reads visually, each labelled `V7 · Deck A slide 4 · 00:06:21` | No deck was supplied, or off-deck or changed frames were shown |
| `CLAUDE_INSTRUCTIONS.md` | Project instructions telling Claude how to read packs | Generated once, then on request |
| Week index `.md` | Lists each recording's file, date, decks and coverage | When several recordings share a unit or week |
| Word document | Optional reading copy with deck slide numbers in the headings | User option |

There is one pack per recording. Split a pack only above about 80–100k tokens,
and only at a natural break such as a deck switch or a lecture break. Note the
split in both headers.

Claude extracts only the text from a `.docx` and cannot see embedded images. It
does read PDFs visually, up to 100 pages. That is why visuals go in a PDF and
the pack itself carries no images.

## Pack format

The pack uses light XML elements with attributes, and plain text inside them.
All timestamps use `HH:MM:SS`.

### Header (mechanical only, no interpretation)

```xml
<lecture unit="UNIT101" session="Day 2 – afternoon, part 1" date="2026-03-12"
         tz="UTC+09:30" start="14:02" duration="01:36:43"
         source="Day2_recording_2.mp4" prev="…" next="…" chat_offset="-00:02:30 (auto)">
<speakers>HOST: … · GUEST: … · STUDENTS: A–F (pseudonymised)</speakers>
<decks>A = … (no file, OCR) · B = …pdf (75 slides)</decks>
<deck_switches>A→B at 00:27:47</deck_switches>
<coverage>B1–B6 discussed · B12–B18 flicked (≤8s each) · B51–B75 not shown</coverage>
<gaps>Recording starts mid-sentence · silence 00:00:00–00:00:03 · ends at break</gaps>
<terms>New York Convention ×18 · UNCITRAL ×4 ⚠ (heard "Bunsertral" ×1) · TBARS ×3 ⚠ not in deck</terms>
<emphasis_hits>00:53:20 "very important" · …</emphasis_hits>
</lecture>
```

- **Coverage:** marks every deck slide as `discussed`, `flicked` or `not shown`.
- **Gaps:** covers the recording starting or ending mid-content, silences longer
  than 60 seconds, breaks and breakout rooms.
- **Terms:** lists capitalised names, Acts, cases and section references, each
  with a count and its first timestamp. Terms not found in the deck or glossary
  are flagged ⚠.
- **Emphasis hits:** matches keywords such as "important", "remember",
  "assessment", "exam", "journal", "essay", "will come up" and "note this".
  They are candidates only. Claude decides what they mean.

### Slide spans

```xml
<slide id="A4" deck="A" deck_title="…" slide="4" appearance="1" title="Code of Hammurabi"
       start="00:06:21" end="00:07:20" match="deck|ocr|none" match_conf="0.92" alt="A5"
       flicks="A5@00:07:05(4s)" visual="V4">
<on_slide source="deck|ocr" ocr_conf="0.88">full slide text</on_slide>
<transcript>
[00:06:20] GUEST (Surname): …paragraph…
[00:41:02] HOST (Surname) ↩chat-31: Yes, …
</transcript>
<chat>
[00:07:18] chat-12 STUDENT A: arbitration
</chat>
</slide>
```

- **Identity:** a slide is identified by its deck label plus the deck's own
  slide number. `appearance` counts revisits.
- **`<on_slide>`:** keeps the full slide text, because exact wording, citations
  and numbers matter.
- **Transcript paragraphs:**
  - one per natural pause, or every 30–90 seconds, each with one timestamp;
  - fillers ("um", "uh") removed;
  - nothing else rewritten.
- **Speakers:** staff appear as role plus name. Students get pseudonyms that stay
  consistent within the lecture.
- **Chat:**
  - Every question, answer, substantive contribution and posted link is kept.
    That includes one-word answers.
  - Greetings, thanks, "can you hear me" and reaction-only lines are dropped.
  - A transcript paragraph that answers a chat comment aloud carries `↩chat-N`.
- **Uncertain words:** shown inline as `⟦best?|alt⟧`, and only below a confidence
  threshold. Tune the threshold so they appear a few times per 10 minutes.
  Words that match the glossary are not flagged. Nothing is silently
  "corrected".
- **Slide changes mid-sentence:** a sentence that crosses a slide change stays
  under the earlier slide, which records `straddles="A5"`.

### Content that is not a slide

```xml
<segment id="S3" kind="off-slide|discussion|qa|document|demo|camera|breakout|break"
         start="…" end="…" after="B34" before="B35">
<visual ref="V7"/>
<transcript>…</transcript>
<chat>…</chat>
</segment>
```

- **Long dwells:** when a slide stays on screen for a long time (for example
  more than 5 minutes) and the transcript drifts away from its text, close the
  slide and open `kind="off-slide"`. In the reviewed lecture, 18 minutes of the
  core argument were filed under a medieval-courts slide.
- **Camera frames:** webcam and name-tile frames become `kind="camera"`, not
  slides.
- **Documents:** paper or document scrolling becomes `kind="document"`, with
  visuals and OCR text marked `source="ocr-document"`.

## Work plan

Status as of version 0.4.0:

- **Done:** items 1–10 and 13; the long-dwell half of item 12; the
  chat-to-answer links of item 14.
- **Still open:**
  - item 11: adding a deck changes the Whisper vocabulary, so the lecture is
    transcribed again;
  - camera and name-tile frame detection (item 12);
  - speaker naming (item 14);
  - the "Later" items.

### Phase 1: accuracy fixes

These help the existing Word output straight away.

1. **Vocabulary pipeline.** Today the slide-derived hotwords are dominated by
   template boilerplate and common words.
   - Drop lines that appear on more than 50% of slides.
   - Drop common English words, using a frequency list.
   - Rank the remaining terms: acronyms, capitalised multi-word names, words
     with diacritics, and terms that repeat across slides.
   - Add a per-unit glossary file.
   - Replace the hard-coded, single-unit `LEGAL_PROMPT` with a generic default plus
     per-unit profiles.
   - Consider per-window hotwords: each slide span's own terms plus the next
     slide's, to work within the 96-token hotword limit.
2. **Chat clock calibration.** Blackboard chat timestamps ran about 2–3 minutes
   late in the reviewed lecture.
   - Find spoken echoes of chat, such as a student's name read aloud or
     distinctive repeated words.
   - Fit a constant offset, with optional drift, and apply it.
   - Show the offset in the header and allow a manual override.
3. **Slide boundaries.** Split Whisper segments at slide changes using word
   timestamps, snapped to the nearest sentence end. Today a whole segment is
   placed by its midpoint.
4. **Repetition guard.** Reconsider `condition_on_previous_text=True` or add a
   hallucination-silence threshold. Flag segments with a high compression
   ratio.

### Phase 2: the Claude pack

5. **Markdown pack exporter**, following the format above. It reuses data the
   app already computes but currently discards:
   - deck match confidence and margin (`match_conf`, `alt`);
   - the folded flicks, itemised (today they are only counted);
   - the coverage table.
6. **Paragraph merging and filler removal.**
7. **Student pseudonymisation**, on by default for the pack. The name mapping is
   stored locally only.
8. **Header extraction:** terms index, emphasis hits and gaps.
9. **Generated `CLAUDE_INSTRUCTIONS.md`**, based on the draft in the study-Claude
   feedback (§16).

### Phase 3: slides, speakers and visuals

10. **Multiple decks per recording**, with deck switches. Matching tolerates
    small differences between deck versions and reports frames that differ from
    the supplied deck.
11. **Re-matching an existing project** when a deck arrives late, without
    re-transcribing.
12. **Long-dwell off-slide detection** and camera-frame detection.
13. **Visuals PDF** for decks that are missing, or for off-deck or changed frames.
14. **Speaker naming** on the review screen, mapping WhisperX speakers to
    HOST/GUEST/STUDENT, and linking chat questions to spoken answers.

### Later

- Inline ⟦uncertain⟧ marks driven by word probabilities.
- Session metadata: unit, session name, time zone, and links to the previous and
  next recordings.
- A per-segment "my notes" field or voice-memo import, for unrecorded breakout
  rooms.
- An "exclude this segment" option, plus a one-line reminder about university
  policy on uploading recordings.

## Parked: local LLM summarisation

The Qwen3-8B / llama.cpp design is kept for reference in git history (commit
`e9317fa`, `LOCAL_LLM_PLAN.md`). Revisit it only if LectureWeave+ needs to
produce readable notes without Claude, or for material that must not be
uploaded.
