# Render Cache (Timeline)

## Problem

The timeline view is deceptively expensive to repaint:

- Grid lines + ruler labels scale with zoom and scroll.
- Many clips may exist, but only a subset are visible.
- Waveforms are geometry-heavy (paths) and can dominate CPU when dragging.
- Mouse move events can arrive far faster than the screen refresh rate.

A naive “recompute everything on every `paintEvent`” approach causes stutter during drag/resize and wastes CPU when only small overlays (like the playhead) change.

---

## Solution

The timeline uses a layered rendering strategy:

1. **Cached background pixmap**: Pre-render the expensive, mostly-static content (grid + clips + overlays + selection) into a `QPixmap` and reuse it until invalidated.
2. **Dynamic overlays**: Draw fast-changing elements (playhead line, snap indicator) directly each frame.
3. **Waveform path cache**: Cache each clip’s waveform `QPainterPath` keyed by geometry and clip parameters.
4. **Throttled invalidation**: Coalesce rapid mouse events to a ~60 FPS update rate.

Primary implementation: [src/ui/timeline_widget.py](src/ui/timeline_widget.py)

---

## 1. Cached Background Pixmap

### What gets cached

The cached background pixmap contains:

- Timeline background fill
- Grid + ruler ticks/labels
- Visible clips (culled by visible time range)
- Overlap highlight overlays
- Selection highlight outline

### What does *not* get cached

Drawn every frame on top of the cached background:

- **Playhead** (current time indicator)
- **Snap indicator** (dashed vertical line while snapping)

### High-DPI correctness

The cache is created at device pixel ratio scale:

- Allocate pixmap as `size * dpr`
- Set `pixmap.setDevicePixelRatio(dpr)`

This avoids blur on fractional scaling displays.

### Invalidation triggers

The background cache is marked dirty (`_background_dirty = True`) when:

- Clip layout changes (`set_clips`, `set_gap`, drag/resize updates)
- Scroll offset changes (including auto-scroll)
- Widget resizes (`resizeEvent`)

The paint path updates the cache when:

- `_background_dirty` is true
- Cache is missing
- Cache size mismatches `size * dpr`

---

## 2. Visible-Range Culling

Before drawing clips into the background cache, the renderer computes the visible time range:

- `visible_start = x_to_time(0)`
- `visible_end = x_to_time(width)`

Clips outside that time window are skipped. This keeps background regeneration close to $O(\text{#visible clips})$ instead of $O(\text{#all clips})$.

---

## 3. Waveform Path Cache

Waveforms are rendered as a filled polygon-like path:

- Sample amplitudes across the clip width
- Build a top polyline + bottom polyline (mirrored)
- Close the path and fill it

### Cache key

To prevent stale geometry when zoom/resize/trim changes, the cache key includes:

- clip id
- render dimensions (`width`, `height`)
- clip parameters affecting the waveform mapping (`offset`, `duration`)

This ensures that changing the clip’s width or trim forces a new path.

### Adaptive sampling

Waveforms use **adaptive sampling** to bound per-frame cost:

- `max_samples = min(int(width), num_samples)`

So zooming out (small pixel width) reduces the number of points, preventing wasted work.

---

## 4. Throttled Updates (~60 FPS)

During drag/resize, the timeline avoids calling `update()` for every mouse event.

- Mouse move sets a “pending update” flag
- A single-shot timer (`_update_throttle_timer`) fires at `_update_interval_ms = 16`
- When the timer fires, the canvas:
  - (optionally) regenerates the waveform for the clip being resized
  - marks the background dirty once
  - repaints once

On mouse release, the throttling is flushed (timer stopped and any pending waveform refresh is processed immediately), ensuring the final state is fully rendered.

---

## Trade-offs

| Choice | Benefit | Cost |
|--------|---------|------|
| Cached background pixmap | Smooth playhead + low-cost repaints | Must carefully invalidate on all “visual state” changes |
| Waveform path cache | Avoids rebuilding complex paths repeatedly | Cache growth; needs invalidation strategy |
| Adaptive sampling | Predictable CPU when zoomed out | Lower waveform fidelity at small sizes |
| Throttled updates | Stable drag performance | Adds slight latency (bounded by ~16ms) |

---

## Implementation Notes

- The cached background includes selection and overlap overlays; changing selection must invalidate the background.
- Thumbnail rendering relies on the global image cache (see [docs/image_cache.md](docs/image_cache.md)).
- The caching approach is designed for “UI truth”: what you see while editing must be stable and deterministic even under fast interactions.
