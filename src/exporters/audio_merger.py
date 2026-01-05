"""
Audio Merger - Merge audio clips with configurable gaps
"""
from pathlib import Path
from dataclasses import dataclass

from pydub import AudioSegment

from config import DEFAULT_GAP_SECONDS


@dataclass
class AudioClip:
    """A single audio clip with metadata"""
    segment: AudioSegment
    speaker: str
    text: str
    original_start: float  # Original position in source file
    original_end: float
    timeline_start: float | None = None  # Position in final timeline
    timeline_end: float | None = None


class AudioMerger:
    """Merge multiple audio clips into a single timeline"""
    
    def __init__(self, default_gap_seconds: float = DEFAULT_GAP_SECONDS):
        self.default_gap_seconds = default_gap_seconds
    
    def merge_clips(
        self,
        clips: list[AudioClip],
        gaps: list[float] | None = None
    ) -> tuple[AudioSegment, list[AudioClip]]:
        """Merge clips with specified gaps
        
        Args:
            clips: List of AudioClip objects to merge
            gaps: List of gaps in seconds after each clip (except last).
                  If None, uses default_gap_seconds for all.
                  
        Returns:
            Tuple of (merged AudioSegment, updated clips with timeline positions)
        """
        if not clips:
            return AudioSegment.empty(), []
        
        if gaps is None:
            gaps = [self.default_gap_seconds] * (len(clips) - 1)
        
        # Ensure we have enough gaps
        while len(gaps) < len(clips) - 1:
            gaps.append(self.default_gap_seconds)
        
        result = AudioSegment.empty()
        current_time = 0.0
        updated_clips = []
        
        for i, clip in enumerate(clips):
            # Set timeline position
            clip.timeline_start = current_time
            clip.timeline_end = current_time + len(clip.segment) / 1000.0
            updated_clips.append(clip)
            
            # Add clip to result
            result += clip.segment
            current_time = clip.timeline_end
            
            # Add gap (except after last clip)
            if i < len(clips) - 1:
                gap_ms = int(gaps[i] * 1000)
                result += AudioSegment.silent(duration=gap_ms)
                current_time += gaps[i]
        
        return result, updated_clips
    
    def merge_with_uniform_gap(
        self,
        clips: list[AudioClip],
        gap_seconds: float
    ) -> tuple[AudioSegment, list[AudioClip]]:
        """Merge clips with a uniform gap between all clips
        
        Args:
            clips: List of AudioClip objects
            gap_seconds: Gap to insert between each clip
            
        Returns:
            Tuple of (merged AudioSegment, updated clips)
        """
        gaps = [gap_seconds] * (len(clips) - 1)
        return self.merge_clips(clips, gaps)
    
    def save(
        self,
        audio: AudioSegment,
        output_path: str | Path,
        format: str = "wav"
    ) -> None:
        """Save merged audio to file
        
        Args:
            audio: The merged AudioSegment
            output_path: Output file path
            format: Audio format (wav, mp3, etc.)
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(str(output_path), format=format)
