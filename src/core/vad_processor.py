"""
VAD Processor - Voice Activity Detection for precise audio trimming
"""
from pathlib import Path
import numpy as np
import torch

from pydub import AudioSegment

from runtime_config import get_config


class VADProcessor:
    """Use Silero VAD for precise voice activity detection"""
    
    def __init__(self, padding_ms: int | None = None):
        """
        Args:
            padding_ms: Padding to add before/after detected voice (ms).
                       If None, uses runtime config value.
        """
        if padding_ms is None:
            padding_ms = get_config().vad_padding_ms
        self.padding_ms = padding_ms
        self.model = None
        self.utils = None
        self._load_model()
    
    def _load_model(self):
        """Load Silero VAD model"""
        self.model, self.utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            trust_repo=True
        )
        (
            self.get_speech_timestamps,
            self.save_audio,
            self.read_audio,
            self.VADIterator,
            self.collect_chunks
        ) = self.utils
    
    def audio_segment_to_tensor(self, audio: AudioSegment) -> torch.Tensor:
        """Convert pydub AudioSegment to torch tensor for VAD"""
        # Convert to mono 16kHz
        audio = audio.set_frame_rate(16000).set_channels(1)
        
        # Get raw samples
        samples = np.array(audio.get_array_of_samples())
        
        # Normalize to [-1, 1]
        samples = samples.astype(np.float32) / 32768.0
        
        return torch.from_numpy(samples)
    
    def get_voice_boundaries(
        self,
        audio: AudioSegment
    ) -> tuple[int, int]:
        """Detect voice start and end positions
        
        Args:
            audio: AudioSegment to analyze
            
        Returns:
            Tuple of (start_ms, end_ms) for voice activity
        """
        tensor = self.audio_segment_to_tensor(audio)
        
        # Get speech timestamps (in samples at 16kHz)
        timestamps = self.get_speech_timestamps(
            tensor,
            self.model,
            sampling_rate=16000,
            threshold=0.5  # Sensitivity threshold
        )
        
        if not timestamps:
            # No speech detected, return full audio
            return 0, len(audio)
        
        # Convert sample positions to milliseconds
        start_sample = timestamps[0]['start']
        end_sample = timestamps[-1]['end']
        
        start_ms = int(start_sample / 16000 * 1000)
        end_ms = int(end_sample / 16000 * 1000)
        
        return start_ms, end_ms
    
    def trim_silence(
        self,
        audio: AudioSegment,
        padding_ms: int | None = None
    ) -> AudioSegment:
        """Trim silence from audio, keeping only voice with padding
        
        Args:
            audio: AudioSegment to trim
            padding_ms: Override default padding (optional)
            
        Returns:
            Trimmed AudioSegment
        """
        if padding_ms is None:
            padding_ms = self.padding_ms
        
        start_ms, end_ms = self.get_voice_boundaries(audio)
        
        # Apply padding
        start_ms = max(0, start_ms - padding_ms)
        end_ms = min(len(audio), end_ms + padding_ms)
        
        return audio[start_ms:end_ms]
    
    def trim_segment_boundaries(
        self,
        audio: AudioSegment,
        original_start: float,
        original_end: float,
        padding_ms: int | None = None,
        prev_end_time: float | None = None
    ) -> tuple[float, float, float]:
        """Get refined boundaries for an audio segment
        
        Useful when you have approximate boundaries from Whisper
        and want to refine them with VAD.
        
        Args:
            audio: Full audio file
            original_start: Original start time in seconds
            original_end: Original end time in seconds
            padding_ms: Override default padding (optional)
            prev_end_time: Raw VAD end time of previous segment (without padding)
                          to avoid overlap in analysis
            
        Returns:
            Tuple of (refined_start, refined_end, raw_voice_end):
            - refined_start: Start time with padding applied (seconds)
            - refined_end: End time with padding applied (seconds)
            - raw_voice_end: Exact VAD voice end time without padding (seconds)
        """
        if padding_ms is None:
            padding_ms = self.padding_ms
        
        # Calculate safe start point - don't go before previous segment's end
        buffer_ms = 300  # Reduced from 500ms
        
        if prev_end_time is not None:
            # Start analysis from previous segment's raw voice end (with small gap)
            safe_start_ms = int(prev_end_time * 1000) + 50  # 50ms gap
            extract_start = max(safe_start_ms, int(original_start * 1000) - buffer_ms)
        else:
            # First segment - use buffer
            extract_start = max(0, int(original_start * 1000) - buffer_ms)
        
        extract_end = min(len(audio), int(original_end * 1000) + buffer_ms)
        
        # Make sure we have valid range
        if extract_start >= extract_end:
            return original_start, original_end, original_end
        
        segment = audio[extract_start:extract_end]
        voice_start_ms, voice_end_ms = self.get_voice_boundaries(segment)
        
        # Store raw voice end before applying padding
        raw_voice_end = (extract_start + voice_end_ms) / 1000.0
        
        # Apply padding
        padded_start_ms = max(0, voice_start_ms - padding_ms)
        padded_end_ms = min(len(segment), voice_end_ms + padding_ms)
        
        # Convert back to absolute times
        refined_start = (extract_start + padded_start_ms) / 1000.0
        refined_end = (extract_start + padded_end_ms) / 1000.0
        
        # Additional safety: don't start before previous segment end
        if prev_end_time is not None:
            refined_start = max(refined_start, prev_end_time + 0.02)  # 20ms gap minimum
        
        return refined_start, refined_end, raw_voice_end

