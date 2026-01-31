"""
Qwen3 Forced Aligner integration (experimental)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import os
import re
import tempfile
from typing import Iterable

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None

try:
    from qwen_asr import Qwen3ForcedAligner
    QWEN3_ASR_AVAILABLE = True
except ImportError:
    QWEN3_ASR_AVAILABLE = False
    Qwen3ForcedAligner = None

try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False
    sf = None

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    AudioSegment = None

from .script_parser import DialogueLine
from .transcriber import WordSegment
from .aligner import AlignedSegment


@dataclass
class _Unit:
    text: str
    start_time: float
    end_time: float


class Qwen3ForcedAlignerWrapper:
    """Align script text to audio using Qwen3-ForcedAligner.

    This is experimental and uses heuristic token mapping to split per-line results.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        max_audio_seconds: float = 120.0,
    ):
        if not QWEN3_ASR_AVAILABLE:
            raise ImportError("qwen-asr is required but not installed. Run: pip install qwen-asr")
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for Qwen3 ForcedAligner")

        use_cuda = torch.cuda.is_available()
        device_map = "cuda:0" if use_cuda else "cpu"

        if use_cuda:
            dtype = torch.bfloat16
        else:
            # Prefer bfloat16 on CPU if supported, fallback to float32
            dtype = torch.float32
            try:
                a = torch.randn(2, 2, dtype=torch.bfloat16)
                b = torch.randn(2, 2, dtype=torch.bfloat16)
                _ = a @ b
                dtype = torch.bfloat16
            except Exception:
                dtype = torch.float32

        self.model = Qwen3ForcedAligner.from_pretrained(
            model_name,
            dtype=dtype,
            device_map=device_map,
        )
        self.max_audio_seconds = max_audio_seconds

    def _language_to_qwen(self, lang: str | None) -> str | None:
        if not lang or lang == "auto":
            return None
        mapping = {
            "ko": "Korean",
            "en": "English",
            "zh": "Chinese",
            "ja": "Japanese",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "ru": "Russian",
            "es": "Spanish",
        }
        return mapping.get(lang, None)

    def _guess_language_from_text(self, text: str) -> str:
        if re.search(r"[가-힣]", text):
            return "Korean"
        if re.search(r"[\u3040-\u30ff]", text):
            return "Japanese"
        if re.search(r"[\u4e00-\u9fff]", text):
            return "Chinese"
        return "English"

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison - lowercase and remove all non-alphanumeric characters."""
        if not text:
            return ""
        return re.sub(r'[^\w]|_', '', text.lower())

    def _find_dialogue_boundary(
        self, units: list[_Unit], dialogue_text: str, start_idx: int
    ) -> int:
        """Find where a dialogue ends in the unit list using text matching.
        
        Returns the end index (exclusive) of units belonging to this dialogue.
        """
        target = self._normalize_text(dialogue_text)
        if not target:
            return start_idx
        
        accumulated = ""
        for i in range(start_idx, len(units)):
            accumulated += self._normalize_text(units[i].text)
            if accumulated == target:
                return i + 1  # Match found
            if len(accumulated) > len(target):
                # Overshoot - fallback to closest length
                break
        
        # Fallback: find index that reaches or exceeds target length
        current_len = 0
        for i in range(start_idx, len(units)):
            current_len += len(self._normalize_text(units[i].text))
            if current_len >= len(target):
                return i + 1
        
        return len(units)

    def _units_from_results(self, results: Iterable) -> list[_Unit]:
        units: list[_Unit] = []
        for item in results:
            text = getattr(item, "text", "")
            start_time = float(getattr(item, "start_time", 0.0))
            end_time = float(getattr(item, "end_time", 0.0))
            units.append(_Unit(text=text, start_time=start_time, end_time=end_time))
        return units

    def _get_audio_duration(self, audio_path: str | Path) -> float | None:
        path = str(Path(audio_path))
        if SOUNDFILE_AVAILABLE:
            try:
                info = sf.info(path)
                if info.frames and info.samplerate:
                    return float(info.frames) / float(info.samplerate)
            except Exception:
                pass
        if PYDUB_AVAILABLE:
            try:
                audio = AudioSegment.from_file(path)
                return float(len(audio)) / 1000.0
            except Exception:
                return None
        return None

    def _map_units_to_dialogues(
        self,
        dialogues: list[DialogueLine],
        units: list[_Unit],
        time_offset: float = 0.0,
    ) -> list[AlignedSegment]:
        if not units:
            return []

        aligned_segments: list[AlignedSegment] = []
        unit_idx = 0

        for dialogue in dialogues:
            if not dialogue.text.strip():
                continue
            if unit_idx >= len(units):
                break

            # Find dialogue boundary using text matching
            end_idx = self._find_dialogue_boundary(units, dialogue.text, unit_idx)
            
            if end_idx <= unit_idx:
                # print(f"[DEBUG] Could not find boundary for: '{dialogue.text[:30]}...'")
                continue

            slice_units = units[unit_idx:end_idx]
            # print(f"[DEBUG] Dialogue matched: '{dialogue.text[:30]}...' -> units {unit_idx}-{end_idx}")
            unit_idx = end_idx

            # Use model units directly as words
            words: list[WordSegment] = []
            for u in slice_units:
                words.append(WordSegment(
                    text=u.text,
                    start=u.start_time + time_offset,
                    end=u.end_time + time_offset,
                ))

            start_time = slice_units[0].start_time + time_offset
            end_time = slice_units[-1].end_time + time_offset

            aligned_segments.append(
                AlignedSegment(
                    dialogue=dialogue,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=100.0,
                    words=words,
                )
            )

        return aligned_segments

    def _split_dialogues_by_duration(
        self,
        dialogues: list[DialogueLine],
        total_duration: float,
        max_chunk_seconds: float,
    ) -> list[tuple[list[DialogueLine], float, float]]:
        if total_duration <= max_chunk_seconds:
            return [(dialogues, 0.0, total_duration)]

        chunk_count = max(1, int(math.ceil(total_duration / max_chunk_seconds)))
        chunk_duration = total_duration / chunk_count

        # Use character count instead of token count
        char_counts = [len(d.text) for d in dialogues]
        total_chars = max(1, sum(char_counts))

        chunks: list[tuple[list[DialogueLine], float, float]] = []
        current_chunk: list[DialogueLine] = []
        current_chars = 0
        char_target_per_chunk = total_chars / chunk_count

        for idx, d in enumerate(dialogues):
            current_chunk.append(d)
            current_chars += char_counts[idx]

            if current_chars >= char_target_per_chunk and len(chunks) < chunk_count - 1:
                start_time = len(chunks) * chunk_duration
                end_time = start_time + chunk_duration
                chunks.append((current_chunk, start_time, end_time))
                current_chunk = []
                current_chars = 0

        if current_chunk:
            start_time = len(chunks) * chunk_duration
            chunks.append((current_chunk, start_time, total_duration))

        return chunks

    def align_speaker_dialogues(
        self,
        dialogues: list[DialogueLine],
        audio_path: str | Path,
        language: str | None = None,
    ) -> list[AlignedSegment]:
        if not dialogues:
            return []

        text = " ".join(d.text for d in dialogues if d.text)
        if not text.strip():
            return []

        lang = self._language_to_qwen(language)
        if lang is None:
            lang = self._guess_language_from_text(text)

        duration = self._get_audio_duration(audio_path)
        if duration is None or duration <= self.max_audio_seconds:
            results = self.model.align(
                audio=str(Path(audio_path)),
                text=text,
                language=lang,
            )

            if not results or not results[0]:
                return []

            units = self._units_from_results(results[0])
            return self._map_units_to_dialogues(dialogues, units, time_offset=0.0)

        if not PYDUB_AVAILABLE:
            # Cannot chunk without audio slicing support
            results = self.model.align(
                audio=str(Path(audio_path)),
                text=text,
                language=lang,
            )
            if not results or not results[0]:
                return []
            units = self._units_from_results(results[0])
            return self._map_units_to_dialogues(dialogues, units, time_offset=0.0)

        # Sequential processing with dialogue overlap
        # Each chunk includes the last dialogue from previous chunk for timing calibration
        audio = AudioSegment.from_file(str(Path(audio_path)))
        
        # Split dialogues into chunks by token count (estimate ~90s worth per chunk)
        text_chunk_seconds = 90.0
        base_chunks = self._split_dialogues_by_duration(dialogues, duration, text_chunk_seconds)
        
        # print(f"[DEBUG] Total duration: {duration:.2f}s, Total dialogues: {len(dialogues)}")
        # print(f"[DEBUG] Base chunks: {len(base_chunks)} (will add overlap)")
        
        # Build chunks with overlap: each chunk (except first) includes last dialogue from previous
        dialogue_chunks_with_overlap: list[tuple[list[DialogueLine], DialogueLine | None]] = []
        for i, (chunk_dialogues, _est_start, _est_end) in enumerate(base_chunks):
            if i == 0:
                # First chunk: no overlap dialogue
                dialogue_chunks_with_overlap.append((chunk_dialogues, None))
            else:
                # Get last dialogue from previous chunk
                prev_chunk_dialogues = base_chunks[i - 1][0]
                overlap_dialogue = prev_chunk_dialogues[-1] if prev_chunk_dialogues else None
                # Prepend overlap dialogue to current chunk
                if overlap_dialogue:
                    chunk_with_overlap = [overlap_dialogue] + chunk_dialogues
                else:
                    chunk_with_overlap = chunk_dialogues
                dialogue_chunks_with_overlap.append((chunk_with_overlap, overlap_dialogue))
        
        all_units: list[_Unit] = []
        temp_files: list[str] = []
        
        # Track position info from previous chunk
        # For next chunk, we start audio from overlap dialogue's START (not end)
        prev_overlap_dialogue_start: float | None = None  # Absolute start time of last dialogue in prev chunk

        try:
            for chunk_idx, (chunk_dialogues, overlap_dialogue) in enumerate(dialogue_chunks_with_overlap):
                chunk_text = " ".join(d.text for d in chunk_dialogues if d.text).strip()
                if not chunk_text:
                    continue
                
                # Determine audio start:
                # - First chunk: start from 0
                # - Other chunks: start from overlap dialogue's START time
                if chunk_idx == 0:
                    audio_start = 0.0
                else:
                    # Start from where overlap dialogue begins (from previous chunk's tracking)
                    audio_start = prev_overlap_dialogue_start if prev_overlap_dialogue_start is not None else 0.0
                
                # End with enough padding to capture all the text
                audio_end = min(duration, audio_start + self.max_audio_seconds)
                
                # print(f"\n[DEBUG] === Chunk {chunk_idx + 1}/{len(dialogue_chunks_with_overlap)} ===")
                # print(f"[DEBUG] Dialogues in chunk: {len(chunk_dialogues)}" + 
                #       (f" (first is overlap: '{overlap_dialogue.text[:30]}...')" if overlap_dialogue else ""))
                # print(f"[DEBUG] Audio range: {audio_start:.2f}s - {audio_end:.2f}s")
                # print(f"[DEBUG] Text preview: {chunk_text[:100]}...")
                
                start_ms = max(0, int(audio_start * 1000))
                end_ms = min(len(audio), int(audio_end * 1000))
                segment = audio[start_ms:end_ms]

                fd, tmp_path = tempfile.mkstemp(prefix="qwen3_fa_", suffix=".wav")
                os.close(fd)
                temp_files.append(tmp_path)
                segment.export(tmp_path, format="wav")

                results = self.model.align(
                    audio=tmp_path,
                    text=chunk_text,
                    language=lang,
                )
                if not results or not results[0]:
                    # print(f"[DEBUG] No results for chunk {chunk_idx + 1}")
                    continue

                chunk_units = self._units_from_results(results[0])
                # print(f"[DEBUG] Model returned {len(chunk_units)} units")
                
                # Offset = audio_start (where this chunk's audio begins in full file)
                effective_offset = audio_start
                overlap_end_idx = 0
                
                if overlap_dialogue and chunk_units:
                    # Find overlap dialogue boundary using text matching
                    overlap_end_idx = self._find_dialogue_boundary(
                        chunk_units, overlap_dialogue.text, 0
                    )
                    # print(f"[DEBUG] Overlap dialogue ends at unit {overlap_end_idx}")
                    
                    if overlap_end_idx > 0 and overlap_end_idx <= len(chunk_units):
                        overlap_last_unit = chunk_units[overlap_end_idx - 1]
                        # print(f"[DEBUG] Overlap dialogue ends @ {overlap_last_unit.end_time:.2f}s (chunk-relative)")
                
                if chunk_units:
                    # print(f"[DEBUG] First unit: '{chunk_units[0].text}' @ {chunk_units[0].start_time:.2f}s (chunk-relative)")
                    # print(f"[DEBUG] Last unit: '{chunk_units[-1].text}' @ {chunk_units[-1].end_time:.2f}s (chunk-relative)")
                    
                    # Track the LAST dialogue's START time for next chunk's overlap
                    # Find boundaries for all dialogues to locate last dialogue start
                    last_dialogue = chunk_dialogues[-1]
                    
                    # Find last dialogue's start by finding boundaries up to it
                    unit_idx = 0
                    for d in chunk_dialogues[:-1]:
                        unit_idx = self._find_dialogue_boundary(chunk_units, d.text, unit_idx)
                    
                    # unit_idx now points to where last dialogue starts
                    if unit_idx < len(chunk_units):
                        last_dialogue_first_unit = chunk_units[unit_idx]
                        prev_overlap_dialogue_start = audio_start + last_dialogue_first_unit.start_time
                        # print(f"[DEBUG] Last dialogue starts at unit {unit_idx}: '{last_dialogue_first_unit.text}' @ {last_dialogue_first_unit.start_time:.2f}s")
                        # print(f"[DEBUG] Next chunk will start audio at: {prev_overlap_dialogue_start:.2f}s (overlap dialogue start)")
                    else:
                        # Fallback: use last unit's end time
                        prev_overlap_dialogue_start = audio_start + chunk_units[-1].end_time
                        # print(f"[DEBUG] Fallback: Next chunk starts at: {prev_overlap_dialogue_start:.2f}s")
                
                # Add offset to convert chunk-relative to absolute time
                # Skip overlap units (they were already added in previous chunk)
                units_to_add = chunk_units[overlap_end_idx:] if overlap_dialogue else chunk_units
                
                for u in units_to_add:
                    all_units.append(_Unit(
                        text=u.text,
                        start_time=u.start_time + effective_offset,
                        end_time=u.end_time + effective_offset,
                    ))
                
                if units_to_add:
                    pass
                    # print(f"[DEBUG] Added {len(units_to_add)} units (skipped {overlap_end_idx} overlap)")
                    # print(f"[DEBUG] After offset: First @ {units_to_add[0].start_time + effective_offset:.2f}s, Last @ {units_to_add[-1].end_time + effective_offset:.2f}s (absolute)")
        finally:
            for p in temp_files:
                try:
                    os.remove(p)
                except OSError:
                    pass

        # Sort units by start time
        all_units.sort(key=lambda u: u.start_time)
        # print(f"\n[DEBUG] Total units collected: {len(all_units)}")
        
        return self._map_units_to_dialogues(dialogues, all_units, time_offset=0.0)

    def align_all(
        self,
        dialogues: list[DialogueLine],
        speaker_audio_map: dict[str, str],
        language: str | None = None,
    ) -> list[AlignedSegment]:
        # Group by speaker
        speaker_dialogues: dict[str, list[DialogueLine]] = {}
        for d in dialogues:
            speaker_dialogues.setdefault(d.speaker, []).append(d)

        speaker_aligned: dict[int, AlignedSegment] = {}
        for speaker, speaker_lines in speaker_dialogues.items():
            audio_path = speaker_audio_map.get(speaker)
            if not audio_path:
                continue
            aligned = self.align_speaker_dialogues(speaker_lines, audio_path, language=language)
            for segment in aligned:
                speaker_aligned[segment.dialogue.index] = segment

        # Return in original script order
        result: list[AlignedSegment] = []
        for i in range(len(dialogues)):
            if i in speaker_aligned:
                result.append(speaker_aligned[i])
        return result
