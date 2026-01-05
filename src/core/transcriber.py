"""
Transcriber - Whisper-based audio transcription with word-level timestamps
"""
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE


@dataclass
class WordSegment:
    """A single word with timestamp"""
    text: str
    start: float
    end: float


@dataclass 
class TranscriptionResult:
    """Full transcription result for an audio file"""
    file_path: str
    language: str
    words: list[WordSegment]
    full_text: str


class Transcriber:
    """Transcribe audio files using Whisper with word-level timestamps"""
    
    def __init__(
        self,
        model_size: str = WHISPER_MODEL,
        device: str = WHISPER_DEVICE,
        compute_type: str = WHISPER_COMPUTE_TYPE
    ):
        """Initialize the Whisper model
        
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
            device: Device to use (cuda, cpu)
            compute_type: Compute type (float16, int8)
        """
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type
        )
    
    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None
    ) -> TranscriptionResult:
        """Transcribe an audio file with word-level timestamps
        
        Args:
            audio_path: Path to the audio file
            language: Language code (ko, en, etc.) or None for auto-detect
            
        Returns:
            TranscriptionResult with words and timestamps
        """
        audio_path = Path(audio_path)
        
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True
        )
        
        words: list[WordSegment] = []
        full_text_parts: list[str] = []
        
        for segment in segments:
            full_text_parts.append(segment.text)
            
            if segment.words:
                for word in segment.words:
                    words.append(WordSegment(
                        text=word.word.strip(),
                        start=word.start,
                        end=word.end
                    ))
        
        return TranscriptionResult(
            file_path=str(audio_path),
            language=info.language,
            words=words,
            full_text=' '.join(full_text_parts).strip()
        )
    
    def transcribe_multiple(
        self,
        audio_paths: list[str | Path],
        language: str | None = None
    ) -> dict[str, TranscriptionResult]:
        """Transcribe multiple audio files
        
        Args:
            audio_paths: List of audio file paths
            language: Language code or None for auto-detect
            
        Returns:
            Dictionary mapping file paths to transcription results
        """
        results = {}
        for path in audio_paths:
            path_str = str(Path(path))
            results[path_str] = self.transcribe(path, language)
        return results
