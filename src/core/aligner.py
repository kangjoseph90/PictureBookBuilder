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
            aligned = AlignedSegment(
                dialogue=dialogue,
                start_time=best_match[0].start,
                end_time=best_match[-1].end,
                confidence=best_score,
                words=best_match  # Include word-level timestamps
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
