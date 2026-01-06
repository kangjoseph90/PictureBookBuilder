"""
Transcriber - Whisper-based audio transcription with word-level timestamps
Supports both stable-ts (accurate) and faster-whisper (fast) backends
"""
from dataclasses import dataclass
from pathlib import Path

from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, USE_STABLE_TS


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
    """Transcribe audio files using Whisper with word-level timestamps
    
    Backend is selected via USE_STABLE_TS config:
    - True: stable-ts (more accurate timestamps, slower)
    - False: faster-whisper (faster, less accurate timestamps)
    """
    
    def __init__(
        self,
        model_size: str = WHISPER_MODEL,
        device: str = WHISPER_DEVICE,
        compute_type: str = WHISPER_COMPUTE_TYPE
    ):
        self.use_stable_ts = USE_STABLE_TS
        
        if self.use_stable_ts:
            import stable_whisper
            self.model = stable_whisper.load_model(model_size, device=device)
        else:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
    
    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        initial_prompt: str | None = None
    ) -> TranscriptionResult:
        """Transcribe an audio file with word-level timestamps"""
        audio_path = Path(audio_path)
        
        if self.use_stable_ts:
            return self._transcribe_stable_ts(audio_path, language, initial_prompt)
        else:
            return self._transcribe_faster_whisper(audio_path, language, initial_prompt)
    
    def _transcribe_stable_ts(
        self,
        audio_path: Path,
        language: str | None,
        initial_prompt: str | None
    ) -> TranscriptionResult:
        """Transcribe using stable-ts backend"""
        result = self.model.transcribe(
            str(audio_path),
            language=language,
            initial_prompt=initial_prompt,
            vad=True,
            regroup=True
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
    
    def _transcribe_faster_whisper(
        self,
        audio_path: Path,
        language: str | None,
        initial_prompt: str | None
    ) -> TranscriptionResult:
        """Transcribe using faster-whisper backend"""
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            initial_prompt=initial_prompt
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
        """Transcribe multiple audio files"""
        results = {}
        for path in audio_paths:
            path_str = str(Path(path))
            results[path_str] = self.transcribe(path, language)
        return results

