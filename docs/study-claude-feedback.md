# Feedback from the receiving Claude

This is a summary of a structured review. The Claude session that turns
LectureWeave+ output into study material answered a set of design questions,
read the code at commit `e9317fa`, and reviewed a real 98-minute LectureWeave+
output. Names, units, workflow details and assessment details have been removed
or made generic. The resulting format specification is in
[../CLAUDE_PACK_PLAN.md](../CLAUDE_PACK_PLAN.md).

## Main conclusions

- **Keep the verbatim transcript and drop the local-LLM summary.**
  - Study notes, reflective journals and essays need the lecturer's exact words.
  - Claude finds "what's new versus the slide" faster and more reliably from the
    raw text than from an 8B model's paraphrase.
  - A summary would have to be checked against the transcript anyway.
- **Length isn't the problem; orientation is.** A 1.5–2-hour transcript is fine
  for Claude. The risk is putting content in the wrong place, not leaving it out.
  - **Wrong slide:** a point lands under the wrong slide.
  - **Lost link:** an answer is cut off from its question.
- **Custom vocabulary is the biggest single accuracy win.** Most corrections are
  mis-heard proper nouns, and almost all of them appear in the slide pack or the
  unit material.
- **Slide anchoring must use the deck's real slide numbers.** Notes are keyed by
  deck and slide range. Coverage matters too: which slides were discussed,
  flicked past or not shown.
- **Speaker labels and chat linking.** Misattribution (lecturer versus guest
  versus student) is a repeated source of errors. Spoken answers to chat
  questions are orphaned without the question beside them.
- **The header should be mechanical only:** metadata, coverage, gaps, a
  proper-noun index and emphasis-keyword hits. Claude does the interpretation.
- **Gaps must be explicit.** A recording that starts mid-lecture, or a segment
  held in another file, was invisible until Claude read the content.
- **Pseudonymise students by default** and keep the mapping local. Students'
  names must not reach anything that might be submitted.
- **Don't optimise for tokens at the expense of fidelity.** About 40–50k tokens
  per lecture is fine. Split a pack only above about 80–100k tokens, at a
  natural break.

## Pain points with existing transcripts

- **Mis-heard proper nouns.** Each needed a correction or a `[check]` flag.
  Examples:

  | Heard | Actually |
  |---|---|
  | Bunsertral | UNCITRAL |
  | Newell Convention | New York Convention |
  | International Borough Association | International Bar Association |
  | AKICA / HKIC / LIAC / SISC | ACICA / HKIAC / LCIA / SIAC |
  | T-bars | TBARS |
  | the other Asians | the other options (probably) |

  Also affected: people's surnames, chambers names, Aboriginal language and
  place names, and the acronyms of local legal services.
- **Section numbers.** "Section 48 2B" for *s 48(2)(b)*, and "rule 3206 sub rule
  two" for *r 32.06(2)*.
- **No slide anchoring or deck-switch information.**
- **No speaker labels.**
- **Fragmentation.** One line every 2–5 seconds roughly doubles the token count.
  LectureWeave+ already merges lines into paragraphs and uses a consistent
  `HH:MM:SS` format; keep both.
- **Missing or partial recordings are not reported.**

## Handling disagreements

- The lecturer's version goes in the notes, and the clash is flagged.
- Slides are usually right on spelling, numbers and citations. The transcript is
  right on what was said and emphasised.
- Lecturers sometimes update or contradict their own slides. Slides contain
  errors too, and guests sometimes overstate. Flag these; never silently
  correct them.
- What helps:
  - slide text attached to the same span;
  - a timestamp, so the passage can be re-listened to;
  - a warning when the deck supplied differs from the one shown (for example,
    last year's deck with edits).

## Chat

- **Keep:**
  - every question;
  - every answer, including one-word answers;
  - every substantive student contribution;
  - anything the lecturer reads out;
  - posted links.
- **Drop:** greetings, thanks, "can you hear me" and reaction-only lines.
- **Link spoken answers to chat questions**, for example "Answering [Student]…",
  with a marker such as `↩chat-N`.

## Content that isn't a slide

Give it its own `<segment>` element, so it is never mistaken for a slide. Kinds:
off-slide, discussion, qa, document, demo, camera, breakout and break.

- **Deck switches:** close the current slide and record the switch in the
  header.
- **Revisits:** mark them with an `appearance` count.

## Uncertainty

- **Words:** inline `⟦best?|alt⟧`, only below a confidence threshold, and never
  for words that match the glossary.
- **OCR:** an `ocr_conf` attribute.
- **Slide matches:** `match_conf` and `alt` attributes.
- **Slide changes mid-sentence:** `straddles`.
- **Never silently correct anything.**

## Visuals

A visuals PDF is worth its token cost only when there is no deck, or when
off-deck or changed frames were shown. It should be deduplicated, with one
image per page and a label printed on each image. Skip it when the deck PDF is
supplied.

## Other requirements

- **Late decks.** Decks often arrive late or not at all. Allow slide matching to
  be re-run on an existing project without re-transcribing.
- **Several recordings per day.** Days are often split into several recordings.
  Add session names and links to the previous and next files.
- **Time zone.** Record the time zone of the recording.
- **Unrecorded sessions.** Breakout rooms and role-plays aren't recorded. A
  "my notes" field, or a voice-memo import placed on the timeline, would help.
- **Excluded content.** Provide an "exclude this segment" option and a reminder
  about institutional policy on uploading recordings of others.
- **Word copy.** Put the deck's slide numbers in Word headings too.

## Code findings at `e9317fa`

1. **Hotwords are mostly noise.** `transcription_vocabulary()` collects words in
   slide order, with a minimal stoplist, and stops at 1,200 characters. Then
   `bounded_whisper_hotwords()` cuts the result to 96 tokens. Template text
   that repeats on every slide (for example "Click on Insert > new slide…") and
   common words use up the budget.
   - **Fixes:**
     - drop lines that appear on most slides;
     - drop common English words;
     - rank acronyms, multi-word names and words with diacritics first;
     - add a per-unit glossary;
     - consider per-window hotwords.
2. **The Whisper prompt is hard-coded.** `LEGAL_PROMPT` names one unit's terms
   and is applied to every lecture. Move it into per-unit profiles and keep the
   default generic.
3. **Repetition risk.** `condition_on_previous_text=True` with an initial prompt
   tends to cause repetition loops. Consider a hallucination-silence threshold,
   and flag segments with a high compression ratio.
4. **Sentences land under the wrong slide.** `align_segments()` places each whole
   segment by its midpoint. Split it at the slide boundary using word
   timestamps instead.
5. **Flicks are only counted.** `omit_incidental_slide_appearances()` reports a
   count. List the omitted slides and their times instead.
6. **Match confidence is discarded.** `match_deck_pages()` computes scores and a
   margin. Export them.
7. **Real chat names are kept.** `parse_chat_comments()` keeps them. Add
   pseudonymisation.
8. **WhisperX speakers are anonymous.** Add a step for naming the speakers.

## Review of a real output (98 minutes, no deck)

**Better than a plain exported transcript:**
- Proper nouns are much more accurate, thanks to the OCR-derived hotwords.
- Paragraphs and timestamps are consistent.
- The placement of chat is the standout feature.

**Still wrong:**
1. **18 minutes under the wrong slide.** The slide never changed while the
   lecturer moved on to the core argument. Detect long dwells whose transcript
   drifts away from the slide text.
2. **The second deck was ignored.** A second deck existed but only one can be
   supplied. As a result, 39 sections were unmatched.
3. **OCR noise a deck would have fixed:** words run together, *ludex* for
   *Iudex*, *ClArb* for *CIArb*, and stray numbers.
4. **Revisits aren't marked.**
5. **Camera and name-tile frames are treated as slides.**
6. **Some mis-hearings remain** that a glossary would fix.
7. **Full stops at segment joins** break sentences in the middle. This is low
   priority.
8. **The chat clock is 2–3 minutes late.** Every spoken answer appears before the
   chat question it answers:

   | Chat question time | Spoken answer time | Gap |
   |---|---|---|
   | 00:42:00 | 00:39:53 | 2:07 |
   | 00:48:39 | 00:46:12 | 2:27 |
   | 00:58:30 | 00:55:44 | 2:46 |
   | 01:15:13 | 01:12:14 | 2:59 |

   Fix this by calibrating an offset from spoken echoes of the chat, with
   optional drift, and allowing a manual override.
9. **Real student names appear in the chat.**

**Biggest wins for this lecture:**
1. multi-deck support;
2. off-slide detection for long dwells;
3. chat offset calibration, together with pseudonymised names.
