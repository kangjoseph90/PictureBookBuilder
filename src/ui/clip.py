from dataclasses import dataclass, field
from typing import Optional, List
from PyQt6.QtGui import QColor

@dataclass
class TimelineClip:
    """A clip on the timeline

    Time coordinate system:
    - start: Timeline position (when this clip plays in the final output)
    - duration: Length of the clip on timeline (seconds)
    - offset: Original audio offset (where to start reading from source audio)

    Audio extraction formula:
        audio_segment = source_audio[offset : offset + duration]

    The offset represents the exact position in the original audio file,
    without any padding applied. Padding is only used during initial
    alignment and audio extraction, not stored in the clip.
    """
    id: str
    name: str
    start: float  # Timeline position (when it plays)
    duration: float  # seconds
    track: int
    color: QColor
    clip_type: str = "audio"  # "audio", "image", or "subtitle"
    waveform: list = field(default_factory=list)  # Normalized amplitude samples (0-1)
    image_path: Optional[str] = None  # Path to image file for thumbnails

    # Source audio info (for trimming/editing)
    offset: float = 0.0        # Offset in original audio (seconds)
    segment_index: int = -1    # Index in result_data['aligned']
    speaker: str = ""          # Speaker name for audio lookup
    words: list = field(default_factory=list)  # Word timestamps for subtitle editing

    @property
    def end(self) -> float:
        """Timeline end position"""
        return self.start + self.duration

    @property
    def source_end(self) -> float:
        """End position in original audio (offset + duration)"""
        return self.offset + self.duration
