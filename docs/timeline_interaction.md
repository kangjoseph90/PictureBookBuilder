# Timeline Interaction Engine

## Problem

A timeline editor needs to feel “obvious” even when clips overlap and the user is moving quickly.

Key interaction conflicts:

- **Overlapping clips:** Which one should be selected/draggable?
- **Precise editing:** Resizing and trimming must be easy without pixel-perfect pointing.
- **Temporal precision:** Dragging should snap to meaningful times (playhead, clip boundaries).
- **Non-destructive editing:** Users expect undo/redo for move/resize actions.
- **Performance:** Interaction must remain smooth while continuously repainting.

Primary implementation: [src/ui/timeline_widget.py](src/ui/timeline_widget.py)

---

## Solution

The editor uses a small set of deterministic rules:

1. **Hit-testing with “topmost wins” layering**, plus selection stickiness.
2. **Edge-based resize detection** using nearest-edge distance with a slight “inside bias”.
3. **Time snapping** to clip boundaries, playhead, and timeline start with a pixel-based threshold.
4. **Modifier-driven ripple behaviors** for moving groups of clips.
5. **Linked boundary resize** (Ctrl-resize) when two clips touch.
6. **Undo snapshots** captured at press time and emitted on release.

---

## 1. Hit-Testing & Selection

### Clip selection under overlap

When multiple clips overlap at the cursor position:

- The system iterates in reverse draw order (top-to-bottom visually) so the last-drawn clip wins.
- If the currently selected clip is in the hit stack, selection is “sticky” (click-and-drag continues to operate on the selected clip).

### Selection cycling (stacked clips)

If the user clicks but does not move, and multiple clips exist under the cursor, the selection cycles to the next clip in the stack.

---

## 2. Edge Resize Detection

Resizing begins when the cursor is close to a clip boundary:

- `EDGE_THRESHOLD` is measured in pixels.
- The engine searches all clips and chooses the **closest edge** (left or right).
- Tie-break behavior:
  - Prefer clips that geometrically contain the cursor (“inside bias”).
  - If still tied, reverse iteration naturally favors the topmost clip.

This makes trimming feel consistent even in dense overlaps.

---

## 3. Snapping (Magnetic Time)

### Snap targets

During drag/resize, the candidate snap points include:

- `0.0` (timeline start)
- `playhead_time`
- Every other clip’s `start` and `end`

### Threshold logic

Snapping uses a pixel threshold converted to time:

- `threshold_time = SNAP_THRESHOLD / zoom`

So snapping “feels the same” at different zoom levels.

### Drag snapping rule

When dragging a clip, the engine attempts:

1. Snap the clip **start**
2. If start doesn’t snap, snap the clip **end** (and back-compute the start)

This helps align either boundary depending on intent.

---

## 4. Clip Dragging & Ripple Modes

Dragging computes a delta time $dt$ from mouse movement and converts it to a new start time.

Modifier keys change scope:

| Modifier | Behavior | Scope |
|----------|----------|-------|
| none | Move only the dragged clip | single clip |
| Ctrl | Ripple move: shift all subsequent clips | all tracks |
| Shift | Ripple move: shift all subsequent clips | same track |

“Subsequent” means clips whose original start time is at or after the dragged clip’s original start (with a small epsilon tolerance).

---

## 5. Resizing & Trimming Semantics

### Audio/subtitle clips (offset-aware)

For clips that represent a window into a source (audio/subtitle):

- **Left-edge resize** adjusts `start`, `offset`, and `duration` together.
- **Right-edge resize** adjusts `duration` only.

Constraints:

- `offset >= 0` (can’t trim left past the start of source)
- For audio, right-edge expansion is capped by source length: `max_duration = source_audio_length - offset`

### Non-audio clips

For clips without a source offset (e.g., images):

- Left edge changes `start` and `duration`
- Right edge changes `duration`

---

## 6. Linked Boundary Resize (Ctrl-resize)

When resizing with Ctrl held, the system tries to find an adjacent clip that touches the resized boundary (same track):

- If you resize the left edge, it looks for a clip whose **right edge** matches that boundary.
- If you resize the right edge, it looks for a clip whose **left edge** matches that boundary.

Then it adjusts the adjacent clip’s start/duration so the shared boundary stays connected.

This supports fast “gap management” without manual two-clip edits.

---

## 7. Undo/Redo Snapshots

For both move and resize:

- Snapshot the relevant clip state(s) on mouse press.
- On mouse release, compare current vs original.
- Emit a `history_command_generated` signal with a list of modifications.

This keeps the undo stack semantic (“Move clips”, “Resize X”) instead of recording per-frame motion.

---

## Implementation Notes

- Zoom uses Ctrl + mouse wheel, anchored at the mouse’s time coordinate to avoid “jumping” while zooming.
- The canvas draws a snap indicator line when a snap occurs.
- Drag/resize repaints are throttled to avoid excessive CPU; details are documented in [docs/render_cache.md](docs/render_cache.md).
