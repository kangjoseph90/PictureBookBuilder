"""
Transcriber - Whisper-based audio transcription with word-level timestamps
Uses stable-ts for improved timestamp accuracy
"""
from dataclasses import dataclass
from pathlib import Path

import stable_whisper

from config import WHISPER_MODEL, WHISPER_DEVICE


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
    """Transcribe audio files using stable-ts (enhanced Whisper) with word-level timestamps"""
    
    def __init__(
        self,
        model_size: str = WHISPER_MODEL,
        device: str = WHISPER_DEVICE
    ):
        """Initialize the stable-ts Whisper model
        
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
            device: Device to use (cuda, cpu)
        """
        self.model = stable_whisper.load_model(model_size, device=device)
    
    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        initial_prompt: str | None = None
    ) -> TranscriptionResult:
        """Transcribe an audio file with refined word-level timestamps
        
        Args:
            audio_path: Path to the audio file
            language: Language code (ko, en, etc.) or None for auto-detect
            initial_prompt: Hint text for Whisper (e.g., speaker names, keywords)
            
        Returns:
            TranscriptionResult with words and timestamps
        """
        audio_path = Path(audio_path)
        
        # stable-ts transcribe with word timestamps
        result = self.model.transcribe(
            str(audio_path),
            language=language,
            initial_prompt=initial_prompt,
            vad=True,  # Voice Activity Detection
            regroup=True  # Regroup words for better timing
        )
        
        words: list[WordSegment] = []
        full_text_parts: list[str] = []
        
        for segment in result.segments:
            full_text_parts.append(segment.text)
            
            if segment.words:
                for word in segment.words:
                    words.append(WordSegment(
                        text=word.word.strip(),
                        start=float(word.start),
                        end=float(word.end)
                    ))
        
        detected_language = result.language if hasattr(result, 'language') else language or 'ko'
        
        return TranscriptionResult(
            file_path=str(audio_path),
            language=detected_language,
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
