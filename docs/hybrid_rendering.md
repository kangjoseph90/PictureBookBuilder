# Hybrid Rendering Pipeline

## Problem

Rendering a picture book video involves:
- 100+ images with variable durations
- Subtitles appearing/disappearing asynchronously
- Audio clips from multiple speakers at precise timestamps

A naive approach using pydub for audio concatenation and sequential FFmpeg calls produces:

1. **Audio drift:** Sequential merging accumulates floating-point errors
2. **Slow encoding:** Re-encoding per image is O(n) expensive
3. **Memory pressure:** Loading all images simultaneously

---

## Solution

A **two-phase hybrid pipeline** that leverages FFmpeg's concat demuxer and filter_complex for precise timing.

### Architecture

```
Phase 1A: Images → Concat Demuxer → Intermediate Video (H.264)
Phase 1B: Subtitles → PNG Render → Concat Demuxer → Intermediate Video (PNG/RGBA)
Phase 2:  Overlay + Audio Mix → Final Output
```

---

## Phase 1: Intermediate Videos

### 1A. Image Video

Images are stitched using FFmpeg's **concat demuxer** (no re-encoding of source images):

```
file 'image1.jpg'
duration 3.5
file 'image2.jpg'
duration 2.1
...
```

**Timeline Quantization:**
- All segment boundaries are rounded to 6 decimal places
- Overlapping images resolved by track priority (higher track wins)
- Adjacent identical images merged into single segment

**Gap Handling:**
Black frames generated once and reused for all gaps.

### 1B. Subtitle Video

Subtitles are pre-rendered to PNG with transparency:

```
Qt QPainter → PNG files (one per unique text)
    ↓
Concat Demuxer → MOV (PNG codec, RGBA)
```

**Critical Design:** Subtitle rendering uses the exact same `Qt QPainter` logic as the preview panel. This ensures pixel-perfect consistency between what the user sees while editing and the final output. Font metrics, positioning, outline widths, and background rendering are identical.

**Optimizations:**
- Unique text deduplication (render once, reference multiple times)
- Parallel PNG generation via ThreadPoolExecutor
- Transparent frames for gaps (no subtitle visible)

---

## Phase 2: Final Composition

### Video Overlay

```
[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[vout]
```

Subtitle video (with alpha) composited over image video.

### Audio Mixing (The Key Innovation)

Instead of pre-merging audio, use FFmpeg's `adelay` filter for sample-accurate timing:

```
[2:a]atrim=start=0.5:duration=3.2,asetpts=PTS-STARTPTS,adelay=1500|1500[a0]
[2:a]atrim=start=5.1:duration=2.8,asetpts=PTS-STARTPTS,adelay=4700|4700[a1]
...
[a0][a1]...[aN]amix=inputs=N:normalize=0,apad=whole_dur=120.0[aout]
```

| Filter | Purpose |
|--------|---------|
| `atrim` | Extract segment from source audio |
| `asetpts=PTS-STARTPTS` | Reset timestamps to zero |
| `adelay=ms\|ms` | Delay to exact timeline position |
| `amix` | Combine all clips |
| `apad` | Extend to video duration |

This eliminates drift because each clip's position is calculated independently.

---

## Encoder Selection

GPU encoding detected and used when available:

| Priority | Encoder | Platform |
|----------|---------|----------|
| 1 | h264_nvenc | NVIDIA |
| 2 | h264_qsv | Intel QuickSync |
| 3 | h264_amf | AMD |
| 4 | libx264 | CPU fallback |

Detection via probe encode (0.1s black frame test).

---

## Progress Reporting

FFmpeg's `-progress pipe:1` parsed in real-time:

```
out_time_ms=5230000  → 5.23s encoded
progress=continue
```

Mapped to percentage ranges:
- 5-15%: Image video
- 15-25%: Subtitle video
- 30-100%: Final composition

---

## Trade-offs

| Decision | Rationale |
|----------|-----------|
| Intermediate MOV files | Avoids complex multi-input piping; temp files cleaned on completion |
| PNG codec for subtitles | Lossless alpha; MOV container supports it natively |
| Per-clip adelay | O(n) filter complexity but eliminates cumulative drift |
| No audio normalization | `normalize=0` preserves original levels; user controls input |
| Qt QPainter subtitle rendering | Unifies preview and export rendering; guarantees consistency |
