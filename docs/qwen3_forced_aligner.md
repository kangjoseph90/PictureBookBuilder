# Qwen3 Forced Aligner Adoption

## Summary

I replaced the legacy three-step alignment pipeline (Stable Whisper → fuzzy matching → VAD refinement) with a single Qwen3 Forced Aligner pass. The result is dramatically higher alignment accuracy and a much simpler workflow.

This model was released 2026-01-29, so I am adopting it early based on its strong forced-alignment quality.

---

## Why It Matters

- **Accuracy jump:** Dialogue-to-audio alignment is consistently tighter than the old fuzzy-matching approach.
- **Pipeline simplification:** One model replaces three complex stages.
- **Faster iteration:** Less custom logic, fewer edge cases to maintain.

---

## Replaced Pipeline

**Old**

1. Stable Whisper transcription
2. Fuzzy matching alignment (sliding window)
3. VAD boundary refinement

**New**

1. Qwen3 Forced Aligner (direct text–audio alignment)

---

## Notes

- The forced aligner aligns script text directly to audio with word-level timestamps.
- For Qwen3 usage, it skips VAD and apply lightweight padding only.
- Chunking is used to control memory usage on long inputs.

---

## Troubleshooting: Managing Long Audio & Memory

The primary challenge with Qwen3 Forced Aligner is its high memory usage and "greedy" alignment nature on long files. Below is the evolution of the chunking strategy used to ensure production-grade stability.

### The Problem

- **OOM (Out of Memory):** Large script + long audio crashes VRAM.
- **Greedy Alignment:** The model forces all input text into the provided audio chunk even if they don't match, causing severe drift.
- **Context Loss:** Simply cutting audio at 120s breaks the model’s ability to "anchor" the first word of the next segment.

---

### Implementation Journey

#### Phase 1: Naive Fixed Slicing

- **Strategy:** Slice audio and text into fixed 120s blocks.
- **Result:** **Failure.** Drift accumulates at every boundary because text/audio split points never perfectly align.

#### Phase 2: Full Script + Chunked Audio

- **Strategy:** Chunk audio into 90s blocks but provide the _entire_ script as the text input.
- **Result:** **Failure.** Massive memory usage (OOM) and search space explosion. The model struggles to locate the relevant text within the full script.

#### Phase 3: Time-Estimated Slicing with Margins

- **Strategy:** Estimate script timing, slice audio with 15s margins.
- **Result:** **Failure.** FA aggressively maps the start of any provided text to the `0.0s` mark of the audio chunk, ignoring the leading margin and causing timing logic to break.

#### Phase 4: Sequential Tail-to-Head Matching

- **Strategy:** Align `ABC`, then start next chunk immediately at the recognized `C_end` time.
- **Result:** **Inconsistent.** If a long silence exists between sentences, the model still maps the first word of the next chunk to `0.0s`, ignoring the silence gap.

#### Phase 5: VAD-Based Gap Detection

- **Strategy:** Use Silero VAD to find the exact start of the next sentence speech.
- **Result:** **Failure.** VAD is less robust than the FA model itself. It often misses non-speech vocalizations (sighs, breaths) that are critical for the FA's internal context.

---

### Final Solution: Dialogue Overlap Strategy

To solve the "0.0s mapping" problem without external VAD, I use the FA's own previous results as an anchor.

**The Workflow:**

1. **Overlap Prepend:** For a sequence `ABCDE`, Chunk 1 aligns `ABC`. Chunk 2 aligns `CDE` (prepending the last dialogue `C` from the previous result).
2. **Anchor Point:** Use the precise **start time** of `C` found in Chunk 1 as the absolute start time for Chunk 2's audio slicing.
3. **Punctuation-Blind Matching:** Instead of counting tokens (which vary by model), I normalize text by removing all punctuation and whitespace to find exact sentence boundaries in the output units.
4. **Seamless Stitching:** Skip the already-aligned overlap units (`C`) in Chunk 2's results to maintain a clean, continuous timeline.

**Key Insight:** By forcing the model to re-align the last few seconds of the previous chunk, I provide the "landing pad" it needs to accurately position the new dialogues that follow.
