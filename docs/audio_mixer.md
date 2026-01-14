# Audio Mixer

## Problem

Traditional timeline audio playback requires pre-merging all clips into a single audio file:

```
Clip A (0-3s) + Clip B (3-6s) + Clip C (6-9s) → merged.wav → QMediaPlayer
```

This approach has critical drawbacks:
- **Slow feedback:** Any edit requires re-merging (O(n) operation)
- **Memory pressure:** Merged file can be 100MB+ for long timelines
- **Sync complexity:** Seeking requires recalculating byte offsets

---

## Solution

A **clip-based scheduler** that plays source audio files directly at their timeline positions, without pre-merging.

### Architecture

```
Timeline                 AudioMixer                  QMediaPlayer (per speaker)
    │                        │                              │
    ├── clip @ 0.0s ────────►├── schedule ─────────────────►│ (seek to offset, play)
    ├── clip @ 3.5s ────────►├── schedule ─────────────────►│
    │                        │                              │
    ├── position update ────►├── sync check                 │
    │                        │   ├── start clip if in range │
    │                        │   └── stop clip if past end  │
```

---

## Scheduling Model

Each clip is represented as:

```python
ScheduledClip:
    clip_id: str
    speaker: str
    timeline_start: float   # When to start on timeline
    timeline_end: float     # When to end on timeline
    source_offset: float    # Where to seek in source file
    source_path: str        # Speaker's audio file
    duration: float         # How long to play
```

### Position Tracking

A 16ms timer (~60fps) drives position updates:

```python
def _update_position(self):
    elapsed = (now - last_tick) * playback_rate
    position += elapsed
    
    for clip in clips:
        if clip.timeline_start <= position < clip.timeline_end:
            if clip not in active_players:
                start_clip(clip)
        else:
            if clip in active_players:
                stop_clip(clip)
```

---

## Player Caching

Creating a `QMediaPlayer` per clip is expensive (FFmpeg initialization). Instead, cache one player per speaker:

```python
_player_cache: dict[speaker, (QMediaPlayer, QAudioOutput, seek_correction)]
```

**Seek Correction:**
Qt/FFmpeg has a bug with non-standard sample rates. For audio files with `framerate < 44100`:

```python
seek_correction = framerate / 48000.0
corrected_position = position_ms * seek_correction
```

This compensates for the backend assuming 48kHz.

---

## Clip Lifecycle

### Start

```python
def _start_clip(clip, current_position):
    player = get_cached_player(clip.speaker)
    time_into_clip = current_position - clip.timeline_start
    source_position = clip.source_offset + time_into_clip
    
    player.setPosition(source_position * 1000 * seek_correction)
    player.play()
```

### Stop

```python
def _stop_clip(clip_id):
    player = active_players.pop(clip_id)
    player.stop()  # Don't delete—it's cached
```

---

## Real-Time Editing

When a clip is modified during playback:

```python
def update_clip(clip):
    clips[clip.clip_id] = clip
    
    if clip in active_players and playing:
        stop_clip(clip.clip_id)
        if clip.timeline_start <= position < clip.timeline_end:
            start_clip(clip, position)  # Restart at new offset
```

No re-merge required. Changes take effect immediately.

---

## Trade-offs

| Decision | Rationale |
|----------|-----------|
| Timer-based tracking | More reliable than QMediaPlayer's position signal |
| Per-speaker caching | Limits memory to O(speakers) not O(clips) |
| No audio crossfade | Clips don't overlap by design; crossfade unnecessary |
| 16ms timer interval | 60fps is sufficient for UI sync; lower CPU than 1ms |
