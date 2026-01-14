# Script-to-Audio Alignment

## Problem

Given a written script and recorded audio files (one per speaker), the system must determine exactly when each line of dialogue occurs in the audio.

Challenges:
- **Imprecise transcription:** Whisper ASR output doesn't match the script word-for-word
- **Word boundary drift:** Concatenating transcribed words often bleeds into adjacent sentences
- **Variable speech patterns:** Speakers may skip, repeat, or paraphrase lines

---

## Solution

A two-phase alignment pipeline combining fuzzy text matching with word-level timestamp reconstruction.

### Phase 1: Segment Matching (Sliding Window)

For each script line, find the best-matching segment in the transcribed words.

**Window Strategy:**
- Search window: 70% ~ 130% of script word count (handles spacing variations)
- Start search from the last match position (sequential constraint)

**Scoring (RapidFuzz):**

| Factor | Weight | Purpose |
|--------|--------|---------|
| Full text similarity | Base score | Overall match quality |
| Last word match (≥85%) | +2 bonus | Sentence boundary accuracy |
| Tail similarity (last 15 chars) | Tie-breaker | Prevents sentence bleed-over |

**Selection Logic:**
```
if score > best + 1.0 → update best
elif score > best - 1.0:
    prefer higher tail_score
    prefer last_word match
```

---

### Phase 2: Word-Level Alignment (LCS)

Once a segment is matched, reconstruct precise timestamps for each script word.

**Algorithm:** `difflib.SequenceMatcher` (Longest Common Subsequence)

| Operation | Script Words | Whisper Words | Action |
|-----------|--------------|---------------|--------|
| `equal` | ✓ | ✓ | 1:1 timestamp copy |
| `replace` | ✓ | ✓ (different) | Proportional distribution |
| `delete` | ✓ | ✗ (gap) | Interpolate from neighbors |
| `insert` | ✗ | ✓ (extra) | Ignore |

**Proportional Distribution:**
When word counts differ, distribute time by character length:
```
word_duration = total_duration × (len(word) / total_chars)
```

---

## Result

Each `AlignedSegment` contains:
- Original dialogue line
- Start/end timestamps
- Per-word timestamps (for subtitle editing)
- Confidence score

---

## Trade-offs

| Decision | Rationale |
|----------|-----------|
| Sequential search only | Assumes script order matches audio order (true for narration) |
| Tail similarity tie-breaker | Prioritizes clean sentence endings over higher overall score |
| Character-based interpolation | Better approximation than uniform distribution for CJK text |
