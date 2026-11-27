# LectureWeave+ Local LLM Plan

## Goal

Add an optional, fully local stage that turns each slide's transcript into concise study notes. The generated Word document should emphasise information introduced by the lecturer rather than repeating text already visible on the slide.

## Proposed engine

- **Model:** Qwen3-8B, initially using the `Q4_K_M` GGUF quantisation
- **Runtime:** llama.cpp with NVIDIA GPU acceleration where available
- **Installation:** guided, one-click download and setup inside LectureWeave+
- **Privacy and limits:** offline after installation, with no transcription credits or usage cap

References: [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B), [official GGUF files](https://huggingface.co/Qwen/Qwen3-8B-GGUF), and [llama.cpp](https://github.com/ggml-org/llama.cpp).

## Per-slide workflow

For each retained slide appearance, provide the local model with:

1. The correct deck slide number, when a deck was supplied.
2. Native slide text or OCR text.
3. Transcript segments spoken while the slide was displayed.
4. Timestamped chat comments associated with the slide.

Process slides independently so a long lecture never needs to fit into one model request. Repeated appearances of the same deck slide should be combined carefully while preserving timestamps.

## Required output

The model should return structured data containing only:

- New information not already written on the slide
- Lecturer explanations, qualifications and opinions
- Examples, applications and legal consequences
- Relevant questions and answers
- Uncertain statements that need checking
- Supporting timestamps

The prompt must explicitly prohibit invented facts and unnecessary repetition of slide text. Structured JSON output should be validated before it is added to the document.

## Word document design

Each slide section should contain:

- Slide image and correct deck slide number
- A short **New information discussed** section, normally 3–8 bullets
- Relevant chat questions or comments
- Timestamps linking the notes back to the recording

The complete transcript should remain cached locally for review and regeneration, but it should not be placed in the main document by default. An optional full-transcript appendix can be considered later.

## Safeguards

- Make local summarisation optional and clearly label AI-generated notes.
- Never replace or delete the underlying Whisper transcript.
- Preserve timestamps so every summary point can be checked.
- Show progress slide by slide and allow cancellation.
- Cache completed slide summaries so interrupted jobs can be resumed.
- Let the user review and edit generated notes before the final Word export.
- Fall back to the existing transcript output if the model is unavailable or fails.

## Implementation stages

1. Add guided llama.cpp and model installation with disk/GPU checks.
2. Define and test the strict per-slide JSON schema and prompt.
3. Add slide-by-slide inference, validation, caching and cancellation.
4. Add a review screen comparing slide text, transcript and proposed notes.
5. Add the concise notes to the Word document and test them on several lecture styles.
6. Measure omissions and hallucinations before making concise notes the recommended output mode.

