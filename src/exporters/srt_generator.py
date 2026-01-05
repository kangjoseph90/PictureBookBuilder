"""
SRT Generator - Generate subtitles with smart line breaks
"""
from pathlib import Path
from dataclasses import dataclass
import re

from config import SUBTITLE_MAX_CHARS_PER_LINE, SUBTITLE_MAX_LINES


@dataclass
class SubtitleEntry:
    """A single subtitle entry"""
    index: int
    start_time: float  # seconds
    end_time: float    # seconds
    text: str          # May contain newlines


class SRTGenerator:
    """Generate SRT subtitle files with smart line breaking"""
    
    def __init__(
        self,
        max_chars_per_line: int = SUBTITLE_MAX_CHARS_PER_LINE,
        max_lines: int = SUBTITLE_MAX_LINES
    ):
        self.max_chars = max_chars_per_line
        self.max_lines = max_lines
        
        # Break points (in order of preference)
        self.break_patterns_ko = [
            r'[.!?]',           # Sentence endings
            r'[,]',             # Commas
            r'[은는이가을를](?=\s)',  # Korean particles before space
            r'\s',              # Any whitespace
        ]
        
        self.break_patterns_en = [
            r'[.!?]',           # Sentence endings
            r'[,;:]',           # Punctuation
            r'\s(?:and|or|but|so|then|thus|however)\s',  # Conjunctions
            r'\s',              # Any whitespace
        ]
    
    def format_time(self, seconds: float) -> str:
        """Convert seconds to SRT time format (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def detect_language(self, text: str) -> str:
        """Simple language detection based on character range"""
        korean_chars = len(re.findall(r'[\uac00-\ud7af]', text))
        total_chars = len(re.findall(r'\w', text))
        
        if total_chars == 0:
            return 'en'
        
        return 'ko' if korean_chars / total_chars > 0.3 else 'en'
    
    def find_best_break_point(self, text: str, max_len: int, lang: str) -> int:
        """Find the best position to break a line
        
        Args:
            text: Text to break
            max_len: Maximum length for first part
            lang: Language code ('ko' or 'en')
            
        Returns:
            Position to break at
        """
        if len(text) <= max_len:
            return len(text)
        
        patterns = self.break_patterns_ko if lang == 'ko' else self.break_patterns_en
        
        best_pos = max_len
        
        for pattern in patterns:
            # Search for pattern within acceptable range
            search_text = text[:max_len + 5]  # Allow slight overflow
            matches = list(re.finditer(pattern, search_text))
            
            for match in reversed(matches):
                pos = match.end()
                if pos <= max_len and pos > max_len // 2:  # Prefer balanced lines
                    return pos
        
        # Fallback: break at last space before max_len
        last_space = text.rfind(' ', 0, max_len)
        if last_space > max_len // 2:
            return last_space + 1
        
        return max_len
    
    def apply_smart_line_breaks(self, text: str) -> str:
        """Apply smart line breaking to text
        
        Args:
            text: Original text
            
        Returns:
            Text with newlines for display
        """
        lang = self.detect_language(text)
        lines = []
        remaining = text.strip()
        
        while remaining and len(lines) < self.max_lines:
            if len(remaining) <= self.max_chars:
                lines.append(remaining)
                break
            
            break_pos = self.find_best_break_point(remaining, self.max_chars, lang)
            lines.append(remaining[:break_pos].strip())
            remaining = remaining[break_pos:].strip()
        
        # If text is still remaining and we hit max lines, append to last line
        if remaining and len(lines) == self.max_lines:
            lines[-1] = lines[-1] + ' ' + remaining
        
        return '\n'.join(lines)
    
    def generate_entries(
        self,
        texts: list[str],
        timestamps: list[tuple[float, float]]
    ) -> list[SubtitleEntry]:
        """Generate subtitle entries with smart line breaks
        
        Args:
            texts: List of subtitle texts
            timestamps: List of (start, end) tuples in seconds
            
        Returns:
            List of SubtitleEntry objects
        """
        entries = []
        for i, (text, (start, end)) in enumerate(zip(texts, timestamps)):
            formatted_text = self.apply_smart_line_breaks(text)
            entries.append(SubtitleEntry(
                index=i + 1,
                start_time=start,
                end_time=end,
                text=formatted_text
            ))
        return entries
    
    def to_srt_string(self, entries: list[SubtitleEntry]) -> str:
        """Convert entries to SRT format string"""
        lines = []
        for entry in entries:
            lines.append(str(entry.index))
            lines.append(f"{self.format_time(entry.start_time)} --> {self.format_time(entry.end_time)}")
            lines.append(entry.text)
            lines.append('')  # Blank line separator
        return '\n'.join(lines)
    
    def save(
        self,
        entries: list[SubtitleEntry],
        output_path: str | Path
    ) -> None:
        """Save subtitles to SRT file
        
        Args:
            entries: List of SubtitleEntry objects
            output_path: Output file path
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        content = self.to_srt_string(entries)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
