"""
Subtitle Processor - Smart Subtitle Segmentation with Heuristic Scoring
Based on Algorithm Specification v1.0
"""
from dataclasses import dataclass
from typing import Optional
import re

try:
    from kiwipiepy import Kiwi
    KIWI_AVAILABLE = True
except ImportError:
    print("Warning: kiwipiepy module not found. Korean morpheme analysis will be disabled.")
    KIWI_AVAILABLE = False


@dataclass
class WordSegment:
    """A single word with timestamp (mirror of transcriber.WordSegment)"""
    text: str
    start: float
    end: float


class SubtitleProcessor:
    """Process subtitles using heuristic scoring system for optimal breaks"""
    
    # ============ SCORING WEIGHTS ============
    # 1. Segment Split (Timeline Clips)
    SEG_SCORE_SENTENCE_END = 60
    SEG_SCORE_CLAUSE_END = 40
    SEG_SCORE_KOREAN_PARTICLE = 20
    SEG_SCORE_KOREAN_CONNECTIVE = 30
    SEG_SCORE_KOREAN_DEPENDENT = 30
    SEG_SCORE_ENGLISH_CONJ = 20
    SEG_SCORE_ENGLISH_PREP = 20
    SEG_PENALTY_DISTANCE_WEIGHT = 50
    SEG_PENALTY_ORPHAN = -100
    SEG_PENALTY_TIGHT_BINDING = -25

    # 2. Line Break (Within Clip \n)
    LINE_SCORE_SENTENCE_END = 50      # 문장부호 뒤 (최우선)
    LINE_SCORE_CLAUSE_END = 40        # 쉼표 뒤
    LINE_SCORE_KOREAN_PARTICLE = 25   # 조사 뒤
    LINE_SCORE_KOREAN_CONNECTIVE = 35 # 연결어미/종결어미 뒤
    LINE_SCORE_KOREAN_DEPENDENT = 15  # 의존명사
    LINE_SCORE_ENGLISH_CONJ = 20      # 영어 접속사 앞
    LINE_SCORE_ENGLISH_PREP = 15      # 영어 전치사 앞
    LINE_PENALTY_DISTANCE_WEIGHT = 60 # 거리 페널티 완화
    LINE_PENALTY_ORPHAN = -100
    LINE_PENALTY_TIGHT_BINDING = -30  # 관형형 분할 억제
    
    # ============ LINGUISTIC DATA ============
    # 문장 부호 (1순위)
    SENTENCE_DELIMITERS = set('.?!。？！')
    
    # 쉼표/구두점 (2순위)
    CLAUSE_DELIMITERS = set(',;:，；：')
    
    # 영어 접속사/전치사 (3순위) - 공백 뒤 단어 체크
    ENGLISH_CONJUNCTIONS = {
        'and', 'but', 'or', 'so', 'because', 'if', 'when', 'while', 'since', 'that', 'which', 'who',
        'although', 'though', 'unless', 'until', 'once', 'as', 'where', 'whether',
        'then', 'yet', 'nor', 'also'
    }
    ENGLISH_PREPOSITIONS = {
        'to', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'about', 'before', 'after',
        'without', 'within', 'during', 'against', 'among', 'between', 'under', 'over', 'through'
    }
    
    def __init__(
        self,
        line_soft_cap: int = 18,
        line_hard_cap: int = 25,
        max_lines: int = 2,
        split_on_conjunctions: bool = True
    ):
        self.line_soft_cap = line_soft_cap
        self.line_hard_cap = line_hard_cap
        self.max_lines = max_lines
        self.split_on_conjunctions = split_on_conjunctions
        
        # 파생 파라미터 계산
        # soft_cap은 약간 여유있게 (max_lines - 0.5)로 계산하여 분할점 선택 범위 확보
        self.segment_soft_cap = int(line_soft_cap * (max_lines - 0.5))
        self.segment_hard_cap = (line_soft_cap * (max_lines - 1)) + line_hard_cap
        
        # 형태소 분석기 (한국어 - Kiwi) - lazy initialization
        self._kiwi = None
        self._kiwi_initialized = False
    
    @property
    def kiwi(self):
        """Lazy-load Kiwi only when needed for Korean text processing"""
        if not self._kiwi_initialized:
            self._kiwi_initialized = True
            if KIWI_AVAILABLE:
                try:
                    self._kiwi = Kiwi()
                except Exception as e:
                    print(f"Warning: Kiwi initialization failed: {e}")
                    self._kiwi = None
        return self._kiwi
    
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
    
    def _get_prev_word(self, text: str, space_idx: int) -> str:
        """Get word before the space"""
        before_text = text[:space_idx]
        match = re.search(r'(\S+)$', before_text)
        if match:
            return match.group(1)
        return ''
    
    def _get_next_word(self, text: str, space_idx: int) -> str:
        """Get word after the space (raw token)"""
        remaining = text[space_idx + 1:]
        match = re.match(r'(\S+)', remaining)
        if match:
            return match.group(1)
        return ''

    def _analyze_sentence_morphemes(self, text: str) -> dict[int, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
        """문장 전체를 Kiwi로 분석하고, 각 공백 위치에서의 품사 정보를 반환
        
        Args:
            text: 분석할 전체 문장
            
        Returns:
            dict[space_idx] = (prev_first_pos, prev_last_pos, next_first_pos, next_last_pos)
            각 공백 위치에서의 앞 단어 마지막 품사와 뒷 단어 첫 번째 품사
        """
        if not self.kiwi or not text:
            return {}
        
        try:
            morphs = self.kiwi.tokenize(text)
            if not morphs:
                return {}
            
            # 공백 위치 수집
            space_indices = [i for i, c in enumerate(text) if c == ' ']
            result = {}
            
            for sp_idx in space_indices:
                # 공백 직전 형태소 찾기 (end <= sp_idx)
                prev_morphs = [m for m in morphs if m.end <= sp_idx]
                # 공백 직후 형태소 찾기 (start > sp_idx)
                next_morphs = [m for m in morphs if m.start > sp_idx]
                
                prev_first_pos = prev_last_pos = None
                next_first_pos = next_last_pos = None
                
                if prev_morphs:
                    # 공백 직전 단어의 형태소들 찾기 (연속된 형태소 그룹)
                    last_morph = prev_morphs[-1]
                    prev_last_pos = last_morph.tag
                    
                    # 같은 단어의 첫 형태소 찾기 (이전 공백 또는 문장 시작부터)
                    prev_space = max([i for i in [-1] + space_indices if i < sp_idx])
                    word_morphs = [m for m in prev_morphs if m.start > prev_space]
                    if word_morphs:
                        prev_first_pos = word_morphs[0].tag
                
                if next_morphs:
                    first_morph = next_morphs[0]
                    next_first_pos = first_morph.tag
                    
                    # 다음 공백 또는 문장 끝까지의 형태소들
                    next_space_candidates = [i for i in space_indices if i > sp_idx]
                    next_space = next_space_candidates[0] if next_space_candidates else len(text)
                    word_morphs = [m for m in next_morphs if m.start < next_space]
                    if word_morphs:
                        next_last_pos = word_morphs[-1].tag
                
                result[sp_idx] = (prev_first_pos, prev_last_pos, next_first_pos, next_last_pos)
            
            return result
        except Exception:
            return {}
    
    def _calculate_linguistic_bonus(self, text: str, space_idx: int, lang: str, is_segment: bool = True, morpheme_cache: dict = None) -> int:
        """Calculate linguistic bonus for a split position
        
        Args:
            morpheme_cache: 문장 전체 분석 결과 (한국어). None이면 단어별 분석 fallback.
        """
        bonus = 0
        prev_char = self._get_prev_char(text, space_idx)
        
        score_sentence = self.SEG_SCORE_SENTENCE_END if is_segment else self.LINE_SCORE_SENTENCE_END
        score_clause = self.SEG_SCORE_CLAUSE_END if is_segment else self.LINE_SCORE_CLAUSE_END
        score_conj = self.SEG_SCORE_ENGLISH_CONJ if is_segment else self.LINE_SCORE_ENGLISH_CONJ
        score_prep = self.SEG_SCORE_ENGLISH_PREP if is_segment else self.LINE_SCORE_ENGLISH_PREP
        
        # 1순위: 문장 부호 뒤
        if prev_char in self.SENTENCE_DELIMITERS:
            bonus += score_sentence
        
        # 2순위: 쉼표/구두점 뒤
        elif prev_char in self.CLAUSE_DELIMITERS:
            bonus += score_clause
        
        # 3순위: 언어별 처리 (설정에서 켜져 있을 때만)
        elif self.split_on_conjunctions:
            if lang == 'ko':
                # 한국어: 형태소 분석 기반 판단 (문맥 기반 분석 사용)
                bonus += self._calculate_korean_morpheme_bonus(text, space_idx, is_segment, morpheme_cache)
            else:
                # 영어: 접속사/전치사 앞
                next_word = self._get_next_word(text, space_idx).lower()
                if next_word == 'of':
                    bonus += 5  # 'of'는 낮은 점수
                elif next_word in self.ENGLISH_CONJUNCTIONS:
                    bonus += score_conj
                elif next_word in self.ENGLISH_PREPOSITIONS:
                    bonus += score_prep
        
        return bonus
    
    def _calculate_korean_morpheme_bonus(self, text: str, space_idx: int, is_segment: bool = True, morpheme_cache: dict = None) -> int:
        """한국어 형태소 분석 기반 보너스 계산
        
        Args:
            morpheme_cache: _analyze_sentence_morphemes()의 결과.
                           None이면 단어별 분석으로 fallback.
        
        Kiwi 품사 태그:
        - JK*: 격조사 (JKS주격, JKC보격, JKG관형격, JKO목적격, JKB부사격, JKV호격, JKQ인용격)
        - JX: 보조사
        - JC: 접속조사  
        - EC: 연결어미 (가장 좋은 분할점 - 절 경계)
        - EF: 종결어미 (문장 끝)
        - ETM: 관형형 어미 (다음 명사와 결합되어야 함 - 분할 비권장)
        - ETN: 명사형 어미
        - NNB: 의존명사
        - VX: 보조용언
        """
        bonus = 0
        prev_word = self._get_prev_word(text, space_idx)
        next_word = self._get_next_word(text, space_idx)
        
        if not prev_word:
            return 0
        
        score_particle = self.SEG_SCORE_KOREAN_PARTICLE if is_segment else self.LINE_SCORE_KOREAN_PARTICLE
        score_connective = self.SEG_SCORE_KOREAN_CONNECTIVE if is_segment else self.LINE_SCORE_KOREAN_CONNECTIVE
        score_dependent = self.SEG_SCORE_KOREAN_DEPENDENT if is_segment else self.LINE_SCORE_KOREAN_DEPENDENT
        
        # 문장 전체 분석 캐시 사용 (문맥 기반으로 정확함)
        if not morpheme_cache or space_idx not in morpheme_cache:
            return 0
            
        prev_first_pos, prev_last_pos, next_first_pos, next_last_pos = morpheme_cache[space_idx]

        if not prev_last_pos:
            return 0

        # === 주요 분할점 보너스 ===
        
        # 1. 연결어미 (EC): 절 경계 - 가장 좋은 분할점
        #    예: "~하고", "~하며", "~해서", "~하니까"
        if prev_last_pos == 'EC':
            bonus += score_connective
        
        # 2. 종결어미 (EF): 문장 끝 (문장부호 없이 끝나는 경우)
        #    예: "살려", "했다", "간다"
        elif prev_last_pos == 'EF':
            bonus += score_connective  # 종결어미도 연결어미와 동일 점수
        
        # 3. 명사형 어미 (ETN): "~함", "~됨" 등
        elif prev_last_pos == 'ETN':
            bonus += score_particle
        
        # 4. 조사 (JK*, JX, JC): 체언 뒤 분할
        #    예: "놀부는", "마음에", "흥부의"
        elif prev_last_pos.startswith('JK') or prev_last_pos == 'JX' or prev_last_pos == 'JC':
            bonus += score_particle
        
        # 5. 의존명사 (NNB) + 조사: "것을", "데에" 등
        #    조사가 붙어 있으면 분할 OK, 단독이면 위험
        elif prev_last_pos == 'NNB':
            # NNB 단독으로 끝나면 페널티 (다음 단어와 결합해야 함)
            bonus += score_dependent if prev_word.endswith(('을', '를', '이', '가', '은', '는', '의', '에', '로')) else -score_dependent
        
        # 6. 시간/순서 명사 뒤: "V-ㄴ 뒤", "V-ㄴ 후", "V-ㄴ 다음" 등
        #    시간적 연결을 나타내는 자연스러운 분할점
        elif prev_last_pos == 'NNG' and prev_word in ('뒤', '후', '다음', '때', '순간', '직후', '이후'):
            bonus += score_connective  # 연결어미와 동일한 점수

        # === 분할 억제 패턴 (tight binding) ===
        binding_penalty = self.SEG_PENALTY_TIGHT_BINDING if is_segment else self.LINE_PENALTY_TIGHT_BINDING

        # 1. 관형형(ETM) 뒤 분할 억제: "예쁜 꽃", "먹은 밥" 등
        #    관형형 어미 뒤에서 끊으면 수식어만 고아가 됨
        if prev_last_pos == 'ETM':
            bonus += binding_penalty * 2  # 강한 억제
        
        # 2. 관형사(MM) 뒤 분할 억제: "이 사람", "그 말" 등  
        if prev_last_pos == 'MM':
            bonus += binding_penalty
        
        # 3. 부사(MAG/MAJ) 단독 뒤 분할 억제: "아주 예쁜" 등
        #    (단, 문장 부사는 분할 OK이므로 신중하게)
        if prev_last_pos in ('MAG', 'MAJ') and next_first_pos in ('VA', 'VV', 'VX'):
            bonus += binding_penalty // 2

        # 4. 의존명사(NNB) + 수사/숫자 결합 억제
        if prev_last_pos == 'NNB' and next_first_pos in ('SN', 'NR', 'MM'):
            bonus += binding_penalty
        
        # 5. 보조용언(VX) 앞 분할 억제: "고쳐 주어", "먹어 버렸다", "해 가다" 등
        #    본용언(VV/VA) + 연결어미(EC) 뒤에 보조용언(VX)이 오면 분리 금지
        if next_first_pos == 'VX':
            bonus += binding_penalty * 2  # 강한 억제

        return bonus
    def _find_best_break(self, text: str, target_pos: int, limit_pos: int, is_segment: bool = True, min_pos: int = 0, strict: bool = False) -> Optional[int]:
        """Find the best break position using scoring system
        
        Args:
            text: Text to break
            target_pos: Target position for balance (soft goal)
            limit_pos: Maximum allowed position (hard limit)
            is_segment: True for segment split, False for line break
            min_pos: Minimum position (to avoid overflow in next segment)
            strict: If True, prioritizes distance to target_pos over linguistic bonus
        
        Returns:
            Best space index to break at, or None
        """
        space_indices = self._get_space_indices(text)
        
        if not space_indices:
            return None
        
        lang = self.detect_language(text)
        best_idx = None
        best_score = float('-inf')
        
        # 가중치 선택
        score_dist_weight = self.SEG_PENALTY_DISTANCE_WEIGHT if is_segment else self.LINE_PENALTY_DISTANCE_WEIGHT
        score_orphan_penalty = self.SEG_PENALTY_ORPHAN if is_segment else self.LINE_PENALTY_ORPHAN
        
        # 한국어인 경우 문장 전체를 한 번 분석하여 캐싱 (문맥 기반 정확한 분석)
        morpheme_cache = None
        if lang == 'ko':
            morpheme_cache = self._analyze_sentence_morphemes(text)
        
        for space_idx in space_indices:
            # Hard Limit: 절대 초과 불가
            if space_idx > limit_pos:
                continue
            
            # Min Limit: 다음 줄이 너무 길어지는 것 방지
            if space_idx < min_pos:
                continue
            
            # 기본 점수: 목표 지점과의 거리에 따른 페널티
            # strict 모드일 경우 거리 페널티 가중치를 아주 높게 설정
            dist_weight = score_dist_weight * (5 if strict else 1)
            distance_ratio = abs(space_idx - target_pos) / max(1, target_pos)
            distance_score = -(distance_ratio ** 2) * dist_weight
            
            # 언어적 가산점 (한국어는 문맥 기반 캐시 사용)
            linguistic_score = self._calculate_linguistic_bonus(text, space_idx, lang, is_segment, morpheme_cache)
            
            # 고아 줄 페널티 (끝에서 2글자 이내)
            orphan_penalty = 0
            remaining = len(text) - space_idx - 1
            if remaining < 3:  # 2글자 미만
                orphan_penalty = score_orphan_penalty
            
            # 시작에서 2글자 이내도 페널티
            if space_idx < 3:
                orphan_penalty += score_orphan_penalty
            
            score = distance_score + linguistic_score + orphan_penalty
            
            if score > best_score:
                best_score = score
                best_idx = space_idx
        
        return best_idx
    
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
    
    # ============ NEW API: 텍스트와 타임스탬프 분리 ============
    
    def find_split_points(self, text: str, is_segment: bool = True) -> list[int]:
        """텍스트를 분할/줄바꿈할 포인트들을 반환 (balanced 전략)
        
        Args:
            text: 분할할 텍스트
            is_segment: True면 세그먼트 분할, False면 줄바꿈
            
        Returns:
            분할 포인트 인덱스 리스트 (공백 위치)
        """
        # Soft/Hard Cap 선택
        if is_segment:
            soft_cap = self.segment_soft_cap
            hard_cap = self.segment_hard_cap
        else:
            soft_cap = self.line_soft_cap
            hard_cap = self.line_hard_cap
        
        if len(text) <= hard_cap:
            return []
        
        split_points = []
        remaining_text = text
        offset = 0
        
        import math
        
        # Hard Cap을 벗어나는 부분이 없을 때까지 반복 분할
        while len(remaining_text) > hard_cap:
            # 현재 남은 텍스트를 기준으로 몇 조각으로 나누는 것이 가장 이상적인지 매번 다시 계산
            num_pieces_min = math.ceil(len(remaining_text) / hard_cap)
            num_pieces_target = round(len(remaining_text) / soft_cap)
            num_pieces = max(num_pieces_min, num_pieces_target, 2)
            
            # 다음 조각의 목표 위치
            target_pos = len(remaining_text) // num_pieces
            limit_pos = hard_cap
            
            # 다음 덩어리가 Hard Cap을 넘지 않도록 최소 분할 위치 설정
            # 현재 조각을 너무 짧게 자르면 남은 텍스트가 처리 불가능해질 수 있음
            min_pos = 0
            if num_pieces == 2:
                # 2조각으로 나눌 때, 뒷부분이 hard_cap 이내가 되려면
                # len - split_pos <= hard_cap  =>  split_pos >= len - hard_cap
                min_pos = max(0, len(remaining_text) - hard_cap)
            
            best_break = self._find_best_break(remaining_text, target_pos, limit_pos, is_segment=is_segment, min_pos=min_pos)
            
            if best_break is None:
                # 1차 시도 실패 시 Fallback 로직:
                # strict=True를 통해 target_pos에 최대한 가까운 공백을 찾음
                # min_pos=0, limit_pos를 해제(문장 끝까지)하여 단어가 잘리는 것보다 길어지는 것을 허용
                best_break = self._find_best_break(remaining_text, target_pos, len(remaining_text), is_segment=is_segment, min_pos=0, strict=True)
                
                # 재탐색 후에도 못 찾았다면 (텍스트 전체에 공백 자체가 아예 없는 경우) 최후의 수단으로 자름
                if best_break is None:
                    best_break = min(len(remaining_text) - 1, hard_cap)
            
            # 전체 텍스트 기준 인덱스로 변환
            absolute_pos = offset + best_break
            split_points.append(absolute_pos)
            
            # 다음 반복을 위해 텍스트 잘라내기
            # 분할 지점 이후의 첫 번째 비공백 문자 위치 찾기 (정확한 오프셋 유지)
            next_start_relative = best_break
            while next_start_relative < len(remaining_text) and remaining_text[next_start_relative].isspace():
                next_start_relative += 1
            
            if next_start_relative >= len(remaining_text):
                break
                
            offset += next_start_relative
            remaining_text = remaining_text[next_start_relative:]
        
        return split_points
    
    def calculate_split_times(
        self,
        text: str,
        split_indices: list[int],
        words: list
    ) -> list[float]:
        """텍스트, 분할 인덱스 리스트, 단어 리스트를 받아 타임스탬프 리스트 반환
        
        Fuzzy matching으로 편집된 텍스트에도 대응
        
        Args:
            text: 분할할 텍스트 (사용자가 편집했을 수 있음)
            split_indices: 분할 포인트 인덱스 리스트 (공백 위치)
            words: 원본 단어 타임스탬프 리스트 (WordSegment 객체)
            
        Returns:
            각 분할 지점의 타임스탬프 리스트
            예: [3.2, 6.8] → 첫 분할 3.2초, 두 번째 분할 6.8초
        """
        if not words or not split_indices:
            return []
        
        timestamps = []
        
        for split_idx in split_indices:
            # 분할 지점 이전 단어 추출
            before_text = text[:split_idx]
            token_match = re.search(r'(\S+)$', before_text)
            
            if not token_match:
                # 공백만 있으면 비례 계산
                ratio = split_idx / len(text) if len(text) > 0 else 0.5
                first_time = words[0].start if hasattr(words[0], 'start') else 0.0
                last_time = words[-1].end if hasattr(words[-1], 'end') else first_time
                timestamps.append(first_time + (last_time - first_time) * ratio)
                continue
            
            token = token_match.group(1)
            
            # Fuzzy matching으로 words에서 가장 유사한 단어 찾기
            def _norm(s: str) -> str:
                return ''.join(re.findall(r"[0-9A-Za-z가-힣]+", (s or '').lower()))
            
            token_key = _norm(token)
            
            try:
                from rapidfuzz import fuzz
            except Exception:
                fuzz = None
            
            # 토큰 위치 기반 추정
            all_tokens = re.findall(r'\S+', text)
            token_idx = len(re.findall(r'\S+', before_text)) - 1
            guess = int(round(token_idx * (len(words) - 1) / max(1, len(all_tokens) - 1)))
            guess = max(0, min(len(words) - 1, guess))
            
            # 윈도우 내에서 최적 매칭 찾기
            window = 15
            lo = max(0, guess - window)
            hi = min(len(words) - 1, guess + window)
            
            best_idx = guess
            best_score = -1
            
            for i in range(lo, hi + 1):
                word_text = words[i].text if hasattr(words[i], 'text') else str(words[i])
                word_key = _norm(word_text)
                
                if not word_key:
                    continue
                
                if fuzz is None:
                    score = 100 if word_key == token_key else 0
                else:
                    score = int(fuzz.ratio(token_key, word_key))
                
                if score > best_score or (score == best_score and abs(i - guess) < abs(best_idx - guess)):
                    best_score = score
                    best_idx = i
            
            # 점수가 낮으면 전체 검색
            if best_score < 55:
                for i in range(len(words)):
                    word_text = words[i].text if hasattr(words[i], 'text') else str(words[i])
                    word_key = _norm(word_text)
                    
                    if not word_key:
                        continue
                    
                    if fuzz is None:
                        score = 100 if word_key == token_key else 0
                    else:
                        score = int(fuzz.ratio(token_key, word_key))
                    
                    if score > best_score:
                        best_score = score
                        best_idx = i
            
            # 매칭된 단어의 end 타임스탬프 사용
            word = words[best_idx]
            timestamp = word.end if hasattr(word, 'end') else 0.0
            timestamps.append(timestamp)
        
        return timestamps
