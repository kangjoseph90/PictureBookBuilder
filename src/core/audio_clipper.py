"""
Audio Clipper - Extract audio segments from longer files
"""
from pathlib import Path

from pydub import AudioSegment


class AudioClipper:
    """Extract and manipulate audio segments"""
    
    def load_audio(self, audio_path: str | Path) -> AudioSegment:
        """Load an audio file
        
        Args:
            audio_path: Path to the audio file
            
        Returns:
            AudioSegment object
        """
        audio_path = Path(audio_path)
        return AudioSegment.from_file(str(audio_path))
    
    def extract_segment(
        self,
        audio: AudioSegment,
        start_time: float,
        end_time: float
    ) -> AudioSegment:
        """Extract a segment from an audio file
        
        Args:
            audio: Source AudioSegment
            start_time: Start time in seconds
            end_time: End time in seconds
            
        Returns:
            Extracted AudioSegment
        """
        start_ms = int(start_time * 1000)
        end_ms = int(end_time * 1000)
        return audio[start_ms:end_ms]
    
    def extract_segment_from_file(
        self,
        audio_path: str | Path,
        start_time: float,
        end_time: float
    ) -> AudioSegment:
        """Load audio file and extract a segment
        
        Args:
            audio_path: Path to the audio file
            start_time: Start time in seconds
            end_time: End time in seconds
            
        Returns:
            Extracted AudioSegment
        """
        audio = self.load_audio(audio_path)
        return self.extract_segment(audio, start_time, end_time)
    
    def save_segment(
        self,
        segment: AudioSegment,
        output_path: str | Path,
        format: str = "wav"
    ) -> None:
        """Save an audio segment to file
        
        Args:
            segment: AudioSegment to save
            output_path: Output file path
            format: Audio format (wav, mp3, etc.)
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        segment.export(str(output_path), format=format)
    
    def concatenate(
        self,
        segments: list[AudioSegment],
        gap_ms: int = 500
    ) -> AudioSegment:
        """Concatenate multiple audio segments with gaps
        
        Args:
            segments: List of AudioSegments to concatenate
            gap_ms: Gap between segments in milliseconds
            
        Returns:
            Concatenated AudioSegment
        """
        if not segments:
            return AudioSegment.empty()
        
        silence = AudioSegment.silent(duration=gap_ms)
        result = segments[0]
        
        for segment in segments[1:]:
            result = result + silence + segment
        
        return result
    
    def get_duration(self, audio: AudioSegment) -> float:
        """Get duration of audio in seconds"""
        return len(audio) / 1000.0
