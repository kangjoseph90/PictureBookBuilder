# Image Cache

## Problem

A picture book project may contain 100+ images displayed across multiple UI components:
- Thumbnail list (48×48)
- Timeline clips (120×60)
- Preview panel (full resolution)

Loading full-resolution images everywhere causes:
- **Memory exhaustion:** 100 × 10MB = 1GB+ RAM
- **UI stutter:** Disk I/O on main thread
- **Redundant work:** Same image scaled repeatedly

---

## Solution

A **single-source LRU cache** with pre-generated multi-resolution thumbnails.

### Architecture

```
Image File
    ↓ (background thread)
Original → Preview Thumb (640×360) → Timeline Thumb (120×60) → Small Thumb (48×48)
    ↓              ↓                        ↓                        ↓
[LRU Cache]   [Always cached]          [Always cached]          [Always cached]
(capacity=20)
```

---

## Thumbnail Strategy

### Cascaded Scaling

Thumbnails are generated from larger to smaller, reusing intermediate results:

```python
preview = original.scaled(640×360)      # From original
timeline = preview.scaled(120×60)       # From preview (faster)
small = timeline.scaled(48×48)          # From timeline (fastest)
```

This is faster than scaling from original three times.

### Storage Policy

| Resolution | Storage | Eviction |
|------------|---------|----------|
| Original | LRU (20 items) | Yes |
| Preview (640×360) | Permanent | No |
| Timeline (120×60) | Permanent | No |
| Small (48×48) | Permanent | No |

Thumbnails are cheap (~50KB total per image). Only originals (~10MB each) are evicted.

---

## LRU Implementation

Using Python's `OrderedDict` for O(1) access with ordering:

```python
def get_original(self, path):
    if path in self._originals:
        self._originals.move_to_end(path)  # Mark as recently used
        return self._originals[path]
    return None

def _enforce_capacity(self):
    while len(self._originals) > self._capacity:
        self._originals.popitem(last=False)  # Evict oldest
```

---

## Background Loading

All disk I/O happens in a `ThreadPoolExecutor`:

```
Main Thread                    Background Thread
     │                              │
     ├─── load_images([paths]) ────►│
     │                              ├─── QImage(path)
     │                              ├─── scale thumbnails
     │◄── image_loaded signal ──────┤
     ├─── QPixmap.fromImage() ──────┤  (must be on main thread)
     │                              │
```

**Critical:** `QPixmap` creation must happen on the main thread; `QImage` can be created anywhere.

---

## API

| Method | Purpose |
|--------|---------|
| `load_images(paths)` | Load thumbnails only (for list view) |
| `prefetch_images(paths)` | Load originals too (for playback lookahead) |
| `get_original(path)` | Get full-res pixmap (may return None if evicted) |
| `get_thumbnail_preview(path)` | Get 640×360 for scrubbing |
| `get_thumbnail_timeline(path)` | Get 120×60 for timeline |
| `get_thumbnail_small(path)` | Get 48×48 for list |

---

## Trade-offs

| Decision | Rationale |
|----------|-----------|
| Fixed capacity (20) | Typical visible range + buffer; configurable |
| Permanent thumbnails | Memory cost (~5MB for 100 images) is acceptable |
| No disk cache | Regenerating thumbnails is fast enough; avoids stale cache issues |
| Global singleton | All UI components share one cache instance |
