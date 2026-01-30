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
1) Stable Whisper transcription
2) Fuzzy matching alignment (sliding window)
3) VAD boundary refinement

**New**
1) Qwen3 Forced Aligner (direct text–audio alignment)

---

## Notes

- The forced aligner aligns script text directly to audio with word-level timestamps.
- For Qwen3 usage, it skips VAD and apply lightweight padding only.
- Chunking is used to control memory usage on long inputs.