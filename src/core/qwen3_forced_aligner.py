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

    def _tokenize(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        # If there is no whitespace but CJK is present, split into characters
        has_cjk = bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
        if has_cjk and not re.search(r"\s", text):
            return list(text)
        return text.split()

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

        line_tokens = [self._tokenize(d.text) for d in dialogues]
        line_counts = [len(toks) for toks in line_tokens]

        aligned_segments: list[AlignedSegment] = []
        unit_idx = 0

        for dialogue, tokens, count in zip(dialogues, line_tokens, line_counts):
            if count <= 0:
                continue
            if unit_idx >= len(units):
                break

            slice_units = units[unit_idx: unit_idx + count]
            if not slice_units:
                break

            unit_idx += len(slice_units)

            words: list[WordSegment] = []
            for i, token in enumerate(tokens):
                if i < len(slice_units):
                    u = slice_units[i]
                    words.append(WordSegment(
                        text=token,
                        start=u.start_time + time_offset,
                        end=u.end_time + time_offset,
                    ))
                else:
                    break

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

        token_counts = [len(self._tokenize(d.text)) for d in dialogues]
        total_tokens = max(1, sum(token_counts))

        chunks: list[tuple[list[DialogueLine], float, float]] = []
        current_chunk: list[DialogueLine] = []
        current_tokens = 0
        token_target_per_chunk = total_tokens / chunk_count

        for idx, d in enumerate(dialogues):
            current_chunk.append(d)
            current_tokens += token_counts[idx]

            if current_tokens >= token_target_per_chunk and len(chunks) < chunk_count - 1:
                start_time = len(chunks) * chunk_duration
                end_time = start_time + chunk_duration
                chunks.append((current_chunk, start_time, end_time))
                current_chunk = []
                current_tokens = 0

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

        # Chunk long audio to reduce peak memory usage
        audio = AudioSegment.from_file(str(Path(audio_path)))
        chunks = self._split_dialogues_by_duration(dialogues, duration, self.max_audio_seconds)

        aligned_segments: list[AlignedSegment] = []
        temp_files: list[str] = []

        try:
            for chunk_dialogues, start_sec, end_sec in chunks:
                chunk_text = " ".join(d.text for d in chunk_dialogues if d.text).strip()
                if not chunk_text:
                    continue

                start_ms = max(0, int(start_sec * 1000))
                end_ms = min(len(audio), int(end_sec * 1000))
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
                    continue

                units = self._units_from_results(results[0])
                aligned_segments.extend(
                    self._map_units_to_dialogues(chunk_dialogues, units, time_offset=start_sec)
                )
        finally:
            for p in temp_files:
                try:
                    os.remove(p)
                except OSError:
                    pass

        return aligned_segments

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
