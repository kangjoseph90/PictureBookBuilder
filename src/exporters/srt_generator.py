"""
SRT Generator - Generate subtitles
"""
from pathlib import Path
from dataclasses import dataclass


@dataclass
class SubtitleEntry:
    """A single subtitle entry"""
    index: int
    start_time: float  # seconds
    end_time: float    # seconds
    text: str


class SRTGenerator:
    """Generate SRT subtitle files"""
    
    def format_time(self, seconds: float) -> str:
        """Convert seconds to SRT time format (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def generate_entries(
        self,
        texts: list[str],
        timestamps: list[tuple[float, float]]
    ) -> list[SubtitleEntry]:
        """Generate subtitle entries
        
        Args:
            texts: List of subtitle texts
            timestamps: List of (start, end) tuples in seconds
            
        Returns:
            List of SubtitleEntry objects
        """
        entries = []
        for i, (text, (start, end)) in enumerate(zip(texts, timestamps)):
            entries.append(SubtitleEntry(
                index=i + 1,
                start_time=start,
                end_time=end,
                text=text.strip()
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
