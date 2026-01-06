"""
Aligner - Match script dialogue to transcribed audio segments
"""
from dataclasses import dataclass
import re

from rapidfuzz import fuzz, process

from .script_parser import DialogueLine
from .transcriber import TranscriptionResult, WordSegment


@dataclass
class AlignedSegment:
    """A dialogue line aligned with its audio segment"""
    dialogue: DialogueLine
    start_time: float
    end_time: float
    confidence: float  # Matching confidence 0-100
    words: list = None  # Word-level timestamps for precise editing


class Aligner:
    """Align script dialogue lines with transcribed audio segments"""
    
    def __init__(self, similarity_threshold: float = 60.0):
        """
        Args:
            similarity_threshold: Minimum similarity score (0-100) for a match
        """
        self.similarity_threshold = similarity_threshold
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        # Remove punctuation and extra whitespace
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
    
    def align_words_to_script(
        self,
        script_text: str,
        whisper_words: list[WordSegment]
    ) -> list[WordSegment]:
        """
        Whisper words를 스크립트 텍스트 단어 경계에 맞게 재구성
        Anchor 기반 2-pass 알고리즘 사용
        """
        if not whisper_words:
            return []
        
        script_words_raw = script_text.split()
        script_words = [self.normalize_text(w) for w in script_words_raw]
        paired = [(raw, norm) for raw, norm in zip(script_words_raw, script_words) if norm]
        if not paired:
            return whisper_words
        script_words_raw, script_words = zip(*paired)
        script_words_raw, script_words = list(script_words_raw), list(script_words)
        

        # === PASS 1: Find anchors (high-confidence 1:1 matches) ===
        anchors = []  # List of (script_idx, whisper_idx, score)
        
        for s_idx, script_word in enumerate(script_words):
            best_w_idx = -1
            best_score = 0
            
            for w_idx, whisper_word in enumerate(whisper_words):
                whisper_norm = self.normalize_text(whisper_word.text)
                score = fuzz.ratio(script_word, whisper_norm)
                
                if score >= 85 and score > best_score:
                    # Check ordering constraint: anchors must be in order
                    if not anchors or w_idx > anchors[-1][1]:
                        best_score = score
                        best_w_idx = w_idx
            
            if best_w_idx >= 0:
                anchors.append((s_idx, best_w_idx, best_score))
        

        
        # === PASS 2: Fill gaps between anchors AND add anchor words ===
        result: list[WordSegment] = []
        
        # Add virtual anchors at boundaries (not real words)
        anchors_with_bounds = [(-1, -1, 0)] + anchors + [(len(script_words), len(whisper_words), 0)]
        
        for i in range(len(anchors_with_bounds) - 1):
            prev_anchor = anchors_with_bounds[i]
            next_anchor = anchors_with_bounds[i + 1]
            
            # Script words in this gap (exclusive of anchors)
            s_start = prev_anchor[0] + 1
            s_end = next_anchor[0]
            
            # Whisper words in this gap (exclusive of anchors)
            w_start = prev_anchor[1] + 1
            w_end = next_anchor[1]
            
            gap_script = list(range(s_start, s_end))
            gap_whisper = list(range(w_start, w_end))
            
            # Process gap (words between anchors)
            if gap_script:
                if not gap_whisper:
                    # No whisper words for these script words - use ratio from neighbors
                    if result:
                        last_end = result[-1].end
                    else:
                        last_end = whisper_words[0].start if whisper_words else 0.0
                    
                    if w_end < len(whisper_words):
                        next_start = whisper_words[w_end].start
                    else:
                        next_start = last_end + 1.0
                    
                    total_duration = next_start - last_end
                    total_chars = sum(len(script_words_raw[si]) for si in gap_script)
                    current_time = last_end
                    
                    for si in gap_script:
                        word_dur = total_duration * len(script_words_raw[si]) / total_chars if total_chars > 0 else 0.1
                        result.append(WordSegment(
                            text=script_words_raw[si],
                            start=current_time,
                            end=current_time + word_dur
                        ))
                        current_time += word_dur
                else:
                    # Distribute script words among whisper words
                    gap_whisper_words = [whisper_words[wi] for wi in gap_whisper]
                    gap_script_raw = [script_words_raw[si] for si in gap_script]
                    gap_script_norm = [script_words[si] for si in gap_script]
                    
                    segments = self._distribute_words(gap_script_raw, gap_script_norm, gap_whisper_words)
                    result.extend(segments)
            
            # Add the anchor word itself (unless it's the virtual end boundary)
            if next_anchor[0] < len(script_words) and next_anchor[1] < len(whisper_words):
                anchor_s_idx = next_anchor[0]
                anchor_w_idx = next_anchor[1]
                result.append(WordSegment(
                    text=script_words_raw[anchor_s_idx],
                    start=whisper_words[anchor_w_idx].start,
                    end=whisper_words[anchor_w_idx].end
                ))
        

        
        return result
    
    def _distribute_words(
        self,
        script_raw: list[str],
        script_norm: list[str],
        whisper_words: list[WordSegment]
    ) -> list[WordSegment]:
        """
        Gap 내에서 script words를 whisper words에 분배
        """
        if not script_raw:
            return []
        if not whisper_words:
            return []
        
        total_start = whisper_words[0].start
        total_end = whisper_words[-1].end
        total_duration = total_end - total_start
        
        # 개수가 같으면 1:1 매핑 (Whisper 시간 그대로)
        if len(script_raw) == len(whisper_words):
            return [
                WordSegment(text=script_raw[i], start=whisper_words[i].start, end=whisper_words[i].end)
                for i in range(len(script_raw))
            ]
        
        # 개수가 다르면 문자 비율로 분배
        total_chars = sum(len(w) for w in script_raw)
        result = []
        current_time = total_start
        
        for word in script_raw:
            ratio = len(word) / total_chars if total_chars > 0 else 1 / len(script_raw)
            word_duration = total_duration * ratio
            result.append(WordSegment(
                text=word,
                start=current_time,
                end=current_time + word_duration
            ))
            current_time += word_duration
        
        return result
    
    def _find_best_match(
        self,
        script_words: list[str],
        script_words_raw: list[str],
        script_start: int,
        whisper_words: list[WordSegment],
        whisper_start: int
    ) -> tuple[int, int, list[WordSegment]] | None:
        """
        현재 위치에서 최적의 스크립트-Whisper 매칭 찾기
        Returns: (소비된 스크립트 단어 수, 소비된 Whisper 단어 수, 생성된 WordSegments)
        """
        best_score = 0
        best_result = None
        
        # 다양한 조합 시도 (script 1~3개 vs whisper 1~3개)
        for s_count in range(1, min(4, len(script_words) - script_start + 1)):
            for w_count in range(1, min(4, len(whisper_words) - whisper_start + 1)):
                script_chunk = script_words[script_start:script_start + s_count]
                script_chunk_raw = script_words_raw[script_start:script_start + s_count]
                whisper_chunk = whisper_words[whisper_start:whisper_start + w_count]
                
                script_merged = ''.join(script_chunk)
                whisper_merged = ''.join(self.normalize_text(w.text) for w in whisper_chunk)
                
                score = fuzz.ratio(script_merged, whisper_merged)
                
                if score > best_score and score >= 55:
                    best_score = score
                    # 세그먼트 생성 (raw 텍스트 사용)
                    segments = self._create_segments(
                        script_chunk_raw, whisper_chunk
                    )
                    best_result = (s_count, w_count, segments)
                    
                    if score >= 95:  # 충분히 좋으면 바로 반환
                        return best_result
        
        return best_result
    
    def _create_segments(
        self,
        script_words: list[str],
        whisper_words: list[WordSegment]
    ) -> list[WordSegment]:
        """
        매칭된 스크립트/Whisper 단어들로 WordSegment 리스트 생성
        시간은 문자 비율로 분배
        
        Args:
            script_words: 원본 스크립트 단어 리스트 (raw, 구두점 포함)
            whisper_words: 매칭된 Whisper 단어들
        """
        
        total_start = whisper_words[0].start
        total_end = whisper_words[-1].end
        total_duration = total_end - total_start
        
        if len(script_words) == 1:
            # 1:N 병합 - 간단히 하나로
            return [WordSegment(
                text=script_words[0],
                start=total_start,
                end=total_end
            )]
            
        # N:N 매칭 - 1:1로 정확한 시간 매핑 (예: "가시지요"(5자)/"제가"(2자) vs "가시죠"/"제가")
        # 묶어서 매칭되었더라도 개수가 같으면 Whisper의 시간을 그대로 쓰는 것이 정확함
        if len(script_words) == len(whisper_words):
            segments = []
            for i, word in enumerate(script_words):
                segments.append(WordSegment(
                    text=word,
                    start=whisper_words[i].start,
                    end=whisper_words[i].end
                ))
            return segments
        
        # N:M (N!=M) 분할 - 문자 비율로 시간 분배 (Fallback)
        # 예: "당겨주소" (1개) -> "당겨", "주소" (2개)
        total_chars = sum(len(w) for w in script_words)
        segments = []
        current_time = total_start
        
        for word in script_words:
            word_ratio = len(word) / total_chars if total_chars > 0 else 1 / len(script_words)
            word_duration = total_duration * word_ratio
            segments.append(WordSegment(
                text=word,
                start=current_time,
                end=current_time + word_duration
            ))
            current_time += word_duration
        
        return segments
    
    
    def find_segment_for_dialogue(
        self,
        dialogue: DialogueLine,
        transcription: TranscriptionResult,
        search_start: int = 0
    ) -> tuple[AlignedSegment | None, int]:
        """Find the audio segment that matches a dialogue line
        
        Uses sliding window approach to find best matching substring.
        
        Args:
            dialogue: The dialogue line to find
            transcription: The transcription result to search in
            search_start: Start searching from this word index
            
        Returns:
            Tuple of (AlignedSegment or None, next search position)
        """
        if not transcription.words:
            return None, search_start
        
        target = self.normalize_text(dialogue.text)
        target_word_count = len(target.split())
        
        # Search with varying window sizes
        best_match = None
        best_score = 0.0
        best_start_idx = search_start
        best_end_idx = search_start
        
        words = transcription.words
        
        # Try different window sizes around expected word count
        for window_size in range(
            max(1, target_word_count - 3),
            min(len(words) - search_start, target_word_count + 5) + 1
        ):
            for start_idx in range(search_start, len(words) - window_size + 1):
                end_idx = start_idx + window_size
                
                # Build candidate text from words
                candidate_words = words[start_idx:end_idx]
                candidate_text = ' '.join(w.text for w in candidate_words)
                candidate_normalized = self.normalize_text(candidate_text)
                
                # Calculate similarity
                score = fuzz.ratio(target, candidate_normalized)
                
                if score > best_score:
                    best_score = score
                    best_match = candidate_words
                    best_start_idx = start_idx
                    best_end_idx = end_idx
        
        if best_score >= self.similarity_threshold and best_match:
            # Whisper words를 스크립트 텍스트 기준으로 재구성
            aligned_words = self.align_words_to_script(dialogue.text, best_match)
            
            aligned = AlignedSegment(
                dialogue=dialogue,
                start_time=best_match[0].start,
                end_time=best_match[-1].end,
                confidence=best_score,
                words=aligned_words  # 재구성된 words 사용
            )
            return aligned, best_end_idx
        
        return None, search_start
    
    def align_speaker_dialogues(
        self,
        dialogues: list[DialogueLine],
        transcription: TranscriptionResult
    ) -> list[AlignedSegment]:
        """Align all dialogues for a single speaker
        
        Args:
            dialogues: List of dialogue lines for one speaker (in order)
            transcription: Transcription of that speaker's audio
            
        Returns:
            List of aligned segments
        """
        aligned_segments: list[AlignedSegment] = []
        search_start = 0
        
        for dialogue in dialogues:
            segment, next_pos = self.find_segment_for_dialogue(
                dialogue, transcription, search_start
            )
            if segment:
                aligned_segments.append(segment)
                search_start = next_pos
        
        return aligned_segments
    
    def align_all(
        self,
        dialogues: list[DialogueLine],
        speaker_transcriptions: dict[str, TranscriptionResult]
    ) -> list[AlignedSegment]:
        """Align all dialogues with their corresponding speaker audio
        
        Args:
            dialogues: All dialogue lines in script order
            speaker_transcriptions: Map of speaker name to their transcription
            
        Returns:
            List of aligned segments in script order
        """
        # Group dialogues by speaker
        speaker_dialogues: dict[str, list[DialogueLine]] = {}
        for d in dialogues:
            if d.speaker not in speaker_dialogues:
                speaker_dialogues[d.speaker] = []
            speaker_dialogues[d.speaker].append(d)
        
        # Align each speaker's dialogues
        speaker_aligned: dict[int, AlignedSegment] = {}  # index -> segment
        
        for speaker, speaker_lines in speaker_dialogues.items():
            if speaker not in speaker_transcriptions:
                continue
            
            transcription = speaker_transcriptions[speaker]
            aligned = self.align_speaker_dialogues(speaker_lines, transcription)
            
            for segment in aligned:
                speaker_aligned[segment.dialogue.index] = segment
        
        # Return in original script order
        result = []
        for i in range(len(dialogues)):
            if i in speaker_aligned:
                result.append(speaker_aligned[i])
        
        return result
