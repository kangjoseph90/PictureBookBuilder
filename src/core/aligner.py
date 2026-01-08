"""
Aligner - Match script dialogue to transcribed audio segments
"""
from dataclasses import dataclass
import re

from rapidfuzz import fuzz

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
    
    def find_segment_for_dialogue(
        self,
        dialogue: DialogueLine,
        transcription: TranscriptionResult,
        search_start: int = 0
    ) -> tuple[AlignedSegment | None, int]:
        """주어진 대사 라인에 매칭되는 오디오 세그먼트 찾기
        
        Sliding window 방식을 사용하며, 문장 경계 정확도를 높이기 위해 
        Tail Similarity(문장 끝 부분 유사도) 분석을 수행함.
        """
        if not transcription.words:
            return None, search_start
        
        target = self.normalize_text(dialogue.text)
        target_word_count = len(target.split())
        words = transcription.words
        
        # 띄어쓰기 가변성을 고려하여 검색 윈도우 크기 설정 (스크립트 단어 수의 70% ~ 130%)
        min_window = max(1, int(target_word_count * 0.7))
        max_window = min(len(words) - search_start, int(target_word_count * 1.3) + 2)
        
        best_match_info = {
            'score': 0.0,
            'tail_score': 0.0,
            'last_match': False,
            'words': None,
            'start_idx': search_start,
            'end_idx': search_start
        }
        
        for window_size in range(min_window, max_window + 1):
            for start_idx in range(search_start, len(words) - window_size + 1):
                end_idx = start_idx + window_size
                candidate_words = words[start_idx:end_idx]
                candidate_text = ' '.join(w.text for w in candidate_words)
                candidate_normalized = self.normalize_text(candidate_text)
                
                # 1. 전체 유사도 계산
                score = fuzz.ratio(target, candidate_normalized)
                
                # 2. 문장 끝 단어 일치 여부 확인 (경계 정확도 보너스)
                target_last = target.split()[-1] if target.split() else ""
                cand_last = candidate_normalized.split()[-1] if candidate_normalized.split() else ""
                last_match = False
                if target_last and cand_last and fuzz.ratio(target_last, cand_last) >= 85:
                    score += 2.0
                    last_match = True
                
                # 3. 꼬리 유사도 계산 (마지막 15자 비교)
                target_tail = target[-15:] if len(target) >= 15 else target
                cand_tail = candidate_normalized[-15:] if len(candidate_normalized) >= 15 else candidate_normalized
                tail_score = fuzz.ratio(target_tail, cand_tail)
                
                # 최적의 후보 업데이트 로직 (Tie-breaking 포함)
                is_better = False
                if score > best_match_info['score'] + 1.0:
                    is_better = True
                elif score > best_match_info['score'] - 1.0:
                    # 점수가 비슷할 경우 꼬리 유사도가 높은 쪽을 선호 (문장 침범 방지)
                    if tail_score > best_match_info['tail_score'] + 2:
                        is_better = True
                    elif tail_score > best_match_info['tail_score'] - 2:
                        if last_match and not best_match_info['last_match']:
                            is_better = True
                        elif score > best_match_info['score']:
                            is_better = True
                
                if is_better:
                    best_match_info.update({
                        'score': score,
                        'tail_score': tail_score,
                        'last_match': last_match,
                        'words': candidate_words,
                        'start_idx': start_idx,
                        'end_idx': end_idx
                    })
        
        if best_match_info['score'] >= self.similarity_threshold and best_match_info['words']:
            # 단어 단위 타임스탬프 재구성
            best_words = best_match_info['words']
            aligned_words = self.align_words_to_script(dialogue.text, best_words)
            
            aligned = AlignedSegment(
                dialogue=dialogue,
                start_time=best_words[0].start,
                end_time=best_words[-1].end,
                confidence=best_match_info['score'],
                words=aligned_words
            )
            return aligned, best_match_info['end_idx']
        
        return None, search_start
    
    def align_speaker_dialogues(
        self,
        dialogues: list[DialogueLine],
        transcription: TranscriptionResult
    ) -> list[AlignedSegment]:
        """한 화자의 모든 대사 라인을 오디오와 정렬"""
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
        """모든 대사 라인을 각 화자의 오디오와 정렬"""
        # 화자별로 대사 그룹화
        speaker_dialogues: dict[str, list[DialogueLine]] = {}
        for d in dialogues:
            if d.speaker not in speaker_dialogues:
                speaker_dialogues[d.speaker] = []
            speaker_dialogues[d.speaker].append(d)
        
        # 각 화자별 정렬 수행
        speaker_aligned: dict[int, AlignedSegment] = {}
        for speaker, speaker_lines in speaker_dialogues.items():
            if speaker not in speaker_transcriptions:
                continue
            
            transcription = speaker_transcriptions[speaker]
            aligned = self.align_speaker_dialogues(speaker_lines, transcription)
            
            for segment in aligned:
                speaker_aligned[segment.dialogue.index] = segment
        
        # 원래 스크립트 순서대로 반환
        result = []
        for i in range(len(dialogues)):
            if i in speaker_aligned:
                result.append(speaker_aligned[i])
        
        return result
