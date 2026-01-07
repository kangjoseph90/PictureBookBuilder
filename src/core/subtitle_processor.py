"""
Subtitle Processor - Smart Subtitle Segmentation with Heuristic Scoring
Based on Algorithm Specification v1.0
"""
from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class WordSegment:
    """A single word with timestamp (mirror of transcriber.WordSegment)"""
    text: str
    start: float
    end: float


class SubtitleProcessor:
    """Process subtitles using heuristic scoring system for optimal breaks"""
    
    # ============ SCORING WEIGHTS ============
    SCORE_SENTENCE_END = 50      # . ? ! 뒤
    SCORE_CLAUSE_END = 30        # , ; : 뒤
    SCORE_KOREAN_PARTICLE = 20   # 조사/어미 뒤
    SCORE_ENGLISH_CONJ = 20      # 접속사/전치사 앞
    SCORE_ENGLISH_OF = 5         # 'of'는 낮은 점수
    PENALTY_ORPHAN = -100        # 2글자 미만 남을 때
    
    # ============ LINGUISTIC DATA ============
    # 문장 부호 (1순위)
    SENTENCE_DELIMITERS = set('.?!。？！')
    
    # 쉼표/구두점 (2순위)
    CLAUSE_DELIMITERS = set(',;:，；：')
    
    # 한국어 조사/어미 (3순위) - 공백 앞 글자 체크
    KOREAN_PARTICLES = set('은는이가을를에서로와과도만요죠')
    KOREAN_ENDINGS = set(['고', '며', '니', '면', '지', '던', '든'])
    
    # 영어 접속사/전치사 (3순위) - 공백 뒤 단어 체크
    ENGLISH_CONJUNCTIONS = {'and', 'but', 'or', 'so', 'because', 'if', 'when', 'while', 'since', 'that', 'which', 'who'}
    ENGLISH_PREPOSITIONS = {'to', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'about'}
    
    def __init__(
        self,
        max_chars_per_segment: int = 40,
        max_chars_per_line: int = 20,
        max_lines: int = 2,
        split_on_conjunctions: bool = True
    ):
        self.max_chars_per_segment = max_chars_per_segment
        self.max_chars_per_line = max_chars_per_line
        self.max_lines = max_lines
        self.split_on_conjunctions = split_on_conjunctions
    
    def detect_language(self, text: str) -> str:
        """Detect if text is primarily Korean or English"""
        korean_chars = len(re.findall(r'[\uac00-\ud7af]', text))
        total_chars = len(re.findall(r'\w', text))
        
        if total_chars == 0:
            return 'en'
        
        korean_ratio = korean_chars / total_chars
        return 'ko' if korean_ratio > 0.3 else 'en'
    
    def _get_space_indices(self, text: str) -> list[int]:
        """Get all space indices in text"""
        return [i for i, char in enumerate(text) if char == ' ']
    
    def _get_prev_char(self, text: str, space_idx: int) -> str:
        """Get character before the space"""
        if space_idx > 0:
            return text[space_idx - 1]
        return ''
    
    def _get_next_word(self, text: str, space_idx: int) -> str:
        """Get word after the space (lowercase)"""
        remaining = text[space_idx + 1:]
        match = re.match(r'(\S+)', remaining)
        if match:
            return match.group(1).lower()
        return ''
    
    def _calculate_linguistic_bonus(self, text: str, space_idx: int, lang: str) -> int:
        """Calculate linguistic bonus for a split position"""
        bonus = 0
        prev_char = self._get_prev_char(text, space_idx)
        
        # 1순위: 문장 부호 뒤
        if prev_char in self.SENTENCE_DELIMITERS:
            bonus += self.SCORE_SENTENCE_END
        
        # 2순위: 쉼표/구두점 뒤
        elif prev_char in self.CLAUSE_DELIMITERS:
            bonus += self.SCORE_CLAUSE_END
        
        # 3순위: 언어별 처리
        elif lang == 'ko':
            # 한국어: 조사/어미 뒤
            if prev_char in self.KOREAN_PARTICLES:
                bonus += self.SCORE_KOREAN_PARTICLE
            # 연결 어미 체크 (2글자)
            if space_idx >= 2:
                two_char = text[space_idx-2:space_idx]
                if two_char in self.KOREAN_ENDINGS:
                    bonus += self.SCORE_KOREAN_PARTICLE
        else:
            # 영어: 접속사/전치사 앞
            next_word = self._get_next_word(text, space_idx)
            if next_word == 'of':
                bonus += self.SCORE_ENGLISH_OF  # 'of'는 낮은 점수
            elif next_word in self.ENGLISH_CONJUNCTIONS:
                bonus += self.SCORE_ENGLISH_CONJ
            elif next_word in self.ENGLISH_PREPOSITIONS:
                bonus += self.SCORE_ENGLISH_CONJ
        
        return bonus
    
    def _find_best_break(self, text: str, target_pos: int, is_segment: bool = False) -> Optional[int]:
        """Find the best break position using scoring system
        
        Args:
            text: Text to break
            target_pos: Target position (MAX_SEGMENT for segmentation, len/2 for line break)
            is_segment: True for segment split, False for line break
        
        Returns:
            Best space index to break at, or None
        """
        space_indices = self._get_space_indices(text)
        
        if not space_indices:
            return None
        
        lang = self.detect_language(text)
        best_idx = None
        best_score = float('-inf')
        
        for space_idx in space_indices:
            # 세그먼트 분할: target 이하만 허용
            if is_segment and space_idx > target_pos:
                continue
            
            # 기본 점수: 목표 지점과의 거리에 따른 페널티
            distance = abs(space_idx - target_pos)
            score = -distance  # 가까울수록 높은 점수
            
            # 언어적 가산점
            score += self._calculate_linguistic_bonus(text, space_idx, lang)
            
            # 균형 페널티 (줄바꿈 시): 한쪽이 너무 짧으면 페널티
            if not is_segment:
                line1_len = space_idx
                line2_len = len(text) - space_idx - 1
                min_len = min(line1_len, line2_len)
                max_len = max(line1_len, line2_len)
                # 짧은 쪽이 긴 쪽의 30% 미만이면 큰 페널티
                if max_len > 0 and min_len / max_len < 0.3:
                    score -= 40
                # 짧은 쪽이 긴 쪽의 50% 미만이면 작은 페널티
                elif max_len > 0 and min_len / max_len < 0.5:
                    score -= 20
            
            # 고아 줄 페널티 (끝에서 2글자 이내)
            remaining = len(text) - space_idx - 1
            if remaining < 3:  # 2글자 미만
                score += self.PENALTY_ORPHAN
            
            # 시작에서 2글자 이내도 페널티
            if space_idx < 3:
                score += self.PENALTY_ORPHAN
            
            if score > best_score:
                best_score = score
                best_idx = space_idx
        
        return best_idx
    
    def format_lines(self, text: str) -> str:
        """단계 2: 줄바꿈 처리 (Line Breaking)
        
        텍스트의 중앙에 가까우면서 언어적으로 자연스러운 공백에서 줄바꿈
        """
        # Check if already has line breaks - if all lines are within limit, don't reformat
        if '\n' in text:
            lines = text.split('\n')
            all_lines_ok = all(len(line.strip()) <= self.max_chars_per_line for line in lines)
            if all_lines_ok:
                return text  # Already properly formatted
        
        # Check if single line is within limit
        if len(text) <= self.max_chars_per_line:
            return text
        
        # 목표: 중앙
        target_pos = len(text) // 2
        
        best_break = self._find_best_break(text, target_pos, is_segment=False)
        
        if best_break is None:
            return text  # 공백 없음
        
        line1 = text[:best_break].strip()
        line2 = text[best_break + 1:].strip()
        
        return f"{line1}\n{line2}"
    
    def split_segment(
        self,
        text: str,
        start_time: float,
        end_time: float,
        words: list
    ) -> list[dict]:
        """단계 1: 세그먼트 분할 (Segmentation)
        
        전체 텍스트를 MAX_SEGMENT 길이 이하의 여러 덩어리로 분할
        
        Args:
            text: The subtitle text
            start_time: Timeline start position
            end_time: Timeline end position
            words: List of WordSegment objects
        
        Returns:
            List of dicts with keys: text, start_time, end_time, words
        """
        if len(text) <= self.max_chars_per_segment:
            return [{
                'text': text,
                'start_time': start_time,
                'end_time': end_time,
                'words': words
            }]
        
        # 목표: MAX_SEGMENT에 가깝게 (꽉 채우기)
        target_pos = self.max_chars_per_segment

        best_break = self._find_best_break(text, target_pos, is_segment=True)

        if best_break is None:
            # 공백 없음 - 강제 분할
            best_break = self.max_chars_per_segment

        # 텍스트 분할
        text1 = text[:best_break].strip()
        text2 = text[best_break:].strip()

        # 단어 분할 및 타임스탬프 계산
        word_idx = self._find_word_index_at_position(text, words, best_break)
        words1 = words[:word_idx + 1] if words else []
        words2 = words[word_idx + 1:] if words else []

        # Whisper 단어 타임스탬프와 VAD 조정된 시작 시간이 다를 수 있음
        # (예: start_time=0.332, first_word.start=0.0). 이 경우 단어 시간은 절대
        # 좌표, start_time은 VAD 기준 좌표이므로 단어 시간을 그대로 사용해야 한다.
        first_word_time = words[0].start if (words and hasattr(words[0], 'start')) else 0.0
        word_times_relative = abs(first_word_time - start_time) < 0.05  # ~50ms 이내면 동일 좌표계로 간주

        # 타임라인 위치 계산 (첫 세그먼트 끝 = 마지막 단어 끝 시간 기준)
        timeline_split_time = self._calculate_split_time(
            text, best_break, start_time, end_time, words, word_times_relative
        )

        # 두 번째 세그먼트 시작 시간 = 첫 세그먼트 종료 시간(분할 시점)
        second_segment_start = timeline_split_time

        # 분할 지점이 입력 구간을 벗어나지 않도록 클램프
        timeline_split_time = min(max(timeline_split_time, start_time), end_time)
        second_segment_start = min(max(second_segment_start, start_time), end_time)
        
        # 첫 세그먼트 끝 = 두 번째 세그먼트 시작 (gap 없이 연결)
        result1 = {
            'text': text1,
            'start_time': start_time,
            'end_time': second_segment_start,
            'words': words1
        }
        
        # 재귀적으로 나머지 처리
        result2_list = self.split_segment(text2, second_segment_start, end_time, words2)
        
        return [result1] + result2_list
    
    def _find_word_index_at_position(self, text: str, words: list, char_pos: int) -> int:
        """Find word index at character position"""
        if not words:
            return 0
        
        char_idx = 0
        for i, word in enumerate(words):
            word_text = word.text if hasattr(word, 'text') else str(word)
            word_text = word_text.strip()
            
            pos = text.find(word_text, char_idx)
            if pos != -1:
                word_end = pos + len(word_text)
                if word_end >= char_pos:
                    return i
                char_idx = word_end
        
        return len(words) - 1
    
    def _calculate_split_time(
        self,
        text: str,
        char_pos: int,
        start_time: float,
        end_time: float,
        words: list,
        word_times_relative: bool = True
    ) -> float:
        """Calculate timeline split time from character position
        
        Uses absolute time difference method (same as manual split)
        """
        if not words:
            # Fallback: ratio based
            ratio = char_pos / len(text) if len(text) > 0 else 0.5
            return start_time + (end_time - start_time) * ratio
        
        # Get first word's start time as reference
        first_word = words[0]
        first_word_time = first_word.start if hasattr(first_word, 'start') else 0.0
        
        # Find the word at split position
        word_idx = self._find_word_index_at_position(text, words, char_pos)
        
        if word_idx < len(words):
            split_word = words[word_idx]
            source_split_time = split_word.end if hasattr(split_word, 'end') else first_word_time

            if word_times_relative:
                # 단어 시간이 세그먼트 시작 기준(상대)일 때는 기존 방식 유지
                relative_time = source_split_time - first_word_time
                return start_time + relative_time

            # 단어 시간이 오디오 전체 기준(절대)일 때는 그대로 사용
            return source_split_time
        
        # Fallback: ratio based
        ratio = char_pos / len(text) if len(text) > 0 else 0.5
        return start_time + (end_time - start_time) * ratio
    
    def find_best_split_point(
        self,
        text: str,
        words: list,
        target_pos: int
    ) -> tuple[int, float, int]:
        """Find the best split point near target_pos (for manual split)
        
        Returns:
            (char_index, timestamp, word_index)
        """
        if not words:
            return target_pos, 0.0, 0

        # Robust mapping after user edits (spacing/punctuation changes):
        # We choose the *token boundary before the cursor* and then fuzzy-match that token
        # to the most likely word in `words`, preferring the expected neighborhood.
        token_matches = list(re.finditer(r"\S+", text))
        if not token_matches:
            first = words[0]
            ts = first.end if hasattr(first, 'end') else 0.0
            return target_pos, ts, 0

        # Token BEFORE cursor (boundary at-or-before target_pos)
        token_before_idx = 0
        for i, m in enumerate(token_matches):
            if m.end() <= target_pos:
                token_before_idx = i
            else:
                break

        token_before = token_matches[token_before_idx].group(0)

        def _norm(s: str) -> str:
            # Keep Korean/English/numbers; drop punctuation/spacing for fuzzy compare.
            return ''.join(re.findall(r"[0-9A-Za-z가-힣]+", (s or '').lower()))

        token_key = _norm(token_before)
        if not token_key:
            # If token is pure punctuation, just use proportional guess.
            n_tokens = len(token_matches)
            n_words = len(words)
            guess = int(round(token_before_idx * (n_words - 1) / max(1, n_tokens - 1)))
            guess = max(0, min(n_words - 1, guess))
            w = words[guess]
            ts = w.end if hasattr(w, 'end') else 0.0
            return token_matches[token_before_idx].end(), ts, guess

        try:
            from rapidfuzz import fuzz
        except Exception:
            fuzz = None

        n_tokens = len(token_matches)
        n_words = len(words)
        guess = int(round(token_before_idx * (n_words - 1) / max(1, n_tokens - 1)))
        guess = max(0, min(n_words - 1, guess))

        # First pass: local window around guess
        window = 15
        lo = max(0, guess - window)
        hi = min(n_words - 1, guess + window)

        best_idx = guess
        best_score = -1

        def score_word(i: int) -> int:
            wt = words[i].text if hasattr(words[i], 'text') else str(words[i])
            wk = _norm(wt)
            if not wk:
                return 0
            if fuzz is None:
                return 100 if wk == token_key else 0
            return int(fuzz.ratio(token_key, wk))

        for i in range(lo, hi + 1):
            s = score_word(i)
            if s > best_score or (s == best_score and abs(i - guess) < abs(best_idx - guess)):
                best_score = s
                best_idx = i

        # Second pass: if score is weak, widen search (handles token-count changes aggressively)
        if best_score < 55:
            for i in range(n_words):
                s = score_word(i)
                if s > best_score or (s == best_score and abs(i - guess) < abs(best_idx - guess)):
                    best_score = s
                    best_idx = i

        word = words[best_idx]
        timestamp = word.end if hasattr(word, 'end') else 0.0
        return token_matches[token_before_idx].end(), timestamp, best_idx
    
    def merge_segments(
        self,
        seg1: dict,
        seg2: dict
    ) -> dict:
        """Merge two adjacent segments"""
        return {
            'text': f"{seg1['text']} {seg2['text']}".strip(),
            'start_time': seg1['start_time'],
            'end_time': seg2['end_time'],
            'words': (seg1.get('words') or []) + (seg2.get('words') or [])
        }
