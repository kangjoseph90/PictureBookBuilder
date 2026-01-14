# VAD Boundary Refinement

## Problem

Whisper's word timestamps are optimized for transcription accuracy, not audio editing. The boundaries often include:

- Leading silence before speech onset
- Trailing breath sounds or room noise
- Arbitrary cuts mid-phoneme

For a picture book with many short clips, these imprecisions accumulate into noticeable sync drift.

---

## Solution

Apply **Silero VAD** (Voice Activity Detection) as a post-processing step to refine segment boundaries.

### Pipeline

```
Whisper timestamps → Extract audio segment → VAD analysis → Refined boundaries
```

---

## Algorithm

### 1. Segment Extraction

Extract audio with a buffer window around Whisper's estimate:

```
extract_start = max(prev_segment_end + 50ms, whisper_start - 500ms)
extract_end = min(audio_length, whisper_end + 500ms)
```

The `prev_segment_end` constraint prevents re-analyzing already-processed audio.

### 2. VAD Processing

Silero VAD returns speech timestamps at 16kHz sample resolution:

```python
timestamps = get_speech_timestamps(audio_tensor, model, threshold=0.5)
voice_start = timestamps[0]['start']  # First speech onset
voice_end = timestamps[-1]['end']     # Last speech offset
```

### 3. Padding Application

Raw VAD boundaries are too tight for natural listening. Apply configurable padding:

```
refined_start = max(0, voice_start - padding_ms)
refined_end = min(audio_length, voice_end + padding_ms)
```

Default: **80ms** padding on each side.

### 4. Overlap Prevention

Critical constraint: segments must not overlap.

```
refined_start = max(refined_start, prev_raw_end + 20ms)
```

The system tracks **raw** (unpadded) end times to allow padding to breathe while preventing actual voice collision.

---

## Return Value

```python
(refined_start, refined_end, raw_voice_end)
```

| Field | Purpose |
|-------|---------|
| `refined_start` | Padded start time for playback |
| `refined_end` | Padded end time for playback |
| `raw_voice_end` | Exact voice offset for next segment's constraint |

---

## Trade-offs

| Decision | Rationale |
|----------|-----------|
| 500ms analysis buffer | Whisper can be off by up to 300ms; extra margin for safety |
| 50ms minimum gap | Prevents artifacts from concatenating speech too tightly |
| Unpadded end tracking | Allows tight consecutive speech while maintaining audible padding |
