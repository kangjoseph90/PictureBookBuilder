"""
Script Parser - Parse "- 화자: 대사" format scripts
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DialogueLine:
    """A single line of dialogue"""
    index: int
    speaker: str
    text: str
    audio_file: str | None = None  # Will be matched later
    start_time: float | None = None
    end_time: float | None = None


class ScriptParser:
    """Parse script files into structured dialogue data"""
    
    # Pattern: "- 화자: 대사" or "* Speaker: Dialogue" or just "Speaker: Dialogue"
    # More flexible: allows -, *, •, or no prefix
    PATTERN = re.compile(r'^[\-\*\•]?\s*(.+?):\s*(.+)$')
    
    def parse_file(self, file_path: str | Path) -> list[DialogueLine]:
        """Parse a script file and return list of dialogue lines"""
        file_path = Path(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return self.parse_text(content)
    
    def parse_text(self, text: str) -> list[DialogueLine]:
        """Parse script text and return list of dialogue lines"""
        lines = []
        index = 0
        
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
                
            match = self.PATTERN.match(line)
            if match:
                speaker = match.group(1).strip()
                dialogue = match.group(2).strip()
                lines.append(DialogueLine(
                    index=index,
                    speaker=speaker,
                    text=dialogue
                ))
                index += 1
        
        return lines
    
    def get_unique_speakers(self, lines: list[DialogueLine]) -> list[str]:
        """Get list of unique speakers in order of first appearance"""
        seen = set()
        speakers = []
        for line in lines:
            if line.speaker not in seen:
                seen.add(line.speaker)
                speakers.append(line.speaker)
        return speakers
    
    def group_by_speaker(self, lines: list[DialogueLine]) -> dict[str, list[DialogueLine]]:
        """Group dialogue lines by speaker"""
        groups: dict[str, list[DialogueLine]] = {}
        for line in lines:
            if line.speaker not in groups:
                groups[line.speaker] = []
            groups[line.speaker].append(line)
        return groups
