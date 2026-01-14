# Smart Image Insertion

## Problem

Placing images on a timeline alongside audio clips creates two conflicting goals:

1. **User intent:** Place the image where the user requested.
2. **Timeline integrity:** Prevent overlaps and maintain audio-image synchronization.

A naive approach either ignores collisions (causing visual chaos) or rigidly snaps to fixed positions (frustrating users).

---

## Solution

The system uses two distinct insertion strategies based on interaction type:

| Interaction | Priority | Behavior |
|-------------|----------|----------|
| Drag & Drop | Safety | Avoids collisions, adjusts position within constraints |
| Context Menu | Sync | Locks to audio position, manages surrounding clips |

---

## 1. Drag & Drop (Safety-First)

When an image is dropped, the algorithm calculates boundary constraints from neighboring clips.

### Constraints

- `m` (min): Latest start allowed by preceding clips → `max(prev.start + margin)`
- `M` (max): Earliest start allowed by succeeding clips → `min(next.start - margin)`

### Positioning

| Condition | Action |
|-----------|--------|
| `m ≤ t ≤ M` | Use requested position `t` |
| `t > M` | Snap to `M` |
| `t < m` | Snap to `m` |
| `m > M` (overcrowded) | Split difference: `(m + M) / 2` |

---

## 2. Context Menu (Sync-First)

When inserting at an audio clip's position, synchronization takes priority over collision avoidance.

### The 1-Second Rule

1. **Start:** Lock to audio clip's start time `t`
2. **Truncate:** Cut any clip containing `t` at that point
3. **End time:**
   - Base: Next audio clip's start
   - If obstacle within 1.0s: Share space → `(obstacle.end + t) / 2`
   - If obstacle beyond 1.0s: Fill gap to obstacle
4. **Shift:** Push affected clips to start at new end time

---

## Implementation Notes

- **Precision:** `EPSILON = 1e-4` for all time comparisons
- **Minimum duration:** Clips guaranteed `0.1s` minimum for UI selection
