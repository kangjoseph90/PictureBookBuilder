"""
Auteur Importer - Import scene/shot info from Auteur project files
and match with PBB timeline clips for automatic image placement.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None


@dataclass
class AuteurHint:
    """A single hint from Auteur's covered_lines"""
    speaker: str
    text: str  # Normalized text (without speaker prefix, ...)
    original_text: str  # Original text for debugging
    scene_id: int
    shot_id: int


@dataclass
class ImagePlacement:
    """Calculated image placement info"""
    scene_id: int
    shot_id: int
    start_time: float
    end_time: float  # Will be adjusted later
    image_path: Optional[str] = None


def normalize_text(text: str) -> str:
    """Normalize text for comparison (remove punctuation, whitespace, lowercase)"""
    # Remove punctuation and extra whitespace
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', '', text)  # Remove ALL whitespace for tighter matching
    return text.strip().lower()


def parse_covered_line(line: str) -> tuple[str, str]:
    """Parse 'Speaker: text' or 'Speaker: ...partial...' format
    
    Returns:
        (speaker, clean_text) - speaker name and cleaned text without ...
    """
    # Split by first colon
    if ':' in line:
        parts = line.split(':', 1)
        speaker = parts[0].strip()
        text = parts[1].strip() if len(parts) > 1 else ""
    else:
        speaker = ""
        text = line.strip()
    
    # Remove leading/trailing ... (partial markers)
    text = re.sub(r'^\.{2,}\s*', '', text)  # Leading ...
    text = re.sub(r'\s*\.{2,}$', '', text)  # Trailing ...
    
    return speaker, text


def load_auteur_project(file_path: str) -> list[AuteurHint]:
    """Load Auteur project JSON and extract all hints
    
    Args:
        file_path: Path to Auteur .json project file
        
    Returns:
        List of AuteurHint objects
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    hints = []
    
    for scene in data.get('scenes', []):
        scene_id = scene.get('scene_id', 0)
        
        for shot in scene.get('shots', []):
            shot_id = shot.get('shot_id', 0)
            
            for line in shot.get('covered_lines', []):
                speaker, text = parse_covered_line(line)
                
                if not text:
                    continue
                
                hints.append(AuteurHint(
                    speaker=speaker,
                    text=normalize_text(text),
                    original_text=line,
                    scene_id=scene_id,
                    shot_id=shot_id
                ))
    
    return hints


def find_image_file(folder: str, scene_id: int, shot_id: int) -> Optional[str]:
    """Find image file matching n-m pattern in folder
    
    Looks for files like: 1-1.png, 01-01.jpg, 1-1.webp, etc.
    """
    folder_path = Path(folder)
    if not folder_path.exists():
        return None
    
    # Try various patterns
    patterns = [
        f"{scene_id}-{shot_id}",           # 1-1
        f"{scene_id:02d}-{shot_id:02d}",   # 01-01
        f"{scene_id:02d}-{shot_id}",       # 01-1
        f"{scene_id}-{shot_id:02d}",       # 1-01
    ]
    
    extensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']
    
    for pattern in patterns:
        for ext in extensions:
            file_path = folder_path / f"{pattern}{ext}"
            if file_path.exists():
                return str(file_path)
    
    return None


def match_hints_to_clips(
    hints: list[AuteurHint],
    clips: list,  # TimelineClip list
    similarity_threshold: float = 70.0
) -> list[ImagePlacement]:
    """Match Auteur hints to PBB timeline clips using sliding window fuzzy matching
    
    Args:
        hints: List of AuteurHint from Auteur project
        clips: List of TimelineClip from PBB timeline (audio/subtitle clips)
        similarity_threshold: Minimum fuzzy match score (0-100)
        
    Returns:
        List of ImagePlacement with matched time ranges
    """
    if not RAPIDFUZZ_AVAILABLE:
        print("Warning: rapidfuzz not available, cannot perform matching")
        return []
    
    # Group clips by speaker
    speaker_words = {}  # {speaker: [(normalized_text, start, end, word_obj), ...]}
    
    for clip in clips:
        if clip.clip_type not in ('audio', 'subtitle'):
            continue
        
        speaker = clip.speaker or ""
        if speaker not in speaker_words:
            speaker_words[speaker] = []
        
        # Collect words from clip
        for word in (clip.words or []):
            word_text = word.text if hasattr(word, 'text') else str(word)
            word_start = word.start if hasattr(word, 'start') else clip.start
            word_end = word.end if hasattr(word, 'end') else clip.end
            
            speaker_words[speaker].append({
                'text': normalize_text(word_text),
                'raw_text': word_text,
                'start': word_start,
                'end': word_end
            })
    
    placements = []
    
    for hint in hints:
        # Find matching speaker's word pool
        words = speaker_words.get(hint.speaker, [])
        
        if not words:
            # Try empty speaker as fallback
            words = speaker_words.get("", [])
        
        if not words:
            continue
        
        # Sliding window search
        best_match = None
        best_score = 0
        
        target = hint.text
        target_len = len(target)
        
        for start_idx in range(len(words)):
            concat = ""
            
            for end_idx in range(start_idx, len(words)):
                concat += words[end_idx]['text']
                
                # Check if target is contained or similar
                if target in concat:
                    # Exact containment - high score
                    score = 100 * (target_len / len(concat)) if concat else 0
                else:
                    # Fuzzy match
                    score = fuzz.ratio(target, concat)
                
                if score > best_score and score >= similarity_threshold:
                    best_score = score
                    best_match = (start_idx, end_idx)
                
                # Early termination if concat is too long
                if len(concat) > target_len * 2:
                    break
        
        if best_match:
            start_idx, end_idx = best_match
            placements.append(ImagePlacement(
                scene_id=hint.scene_id,
                shot_id=hint.shot_id,
                start_time=words[start_idx]['start'],
                end_time=words[end_idx]['end']
            ))
    
    return placements


def deduplicate_placements(placements: list[ImagePlacement]) -> list[ImagePlacement]:
    """Remove duplicate (scene, shot) pairs, keeping earliest start_time"""
    seen = {}  # (scene_id, shot_id) -> ImagePlacement
    
    for p in placements:
        key = (p.scene_id, p.shot_id)
        if key not in seen or p.start_time < seen[key].start_time:
            seen[key] = p
    
    return list(seen.values())


def adjust_end_times(placements: list[ImagePlacement], timeline_end: float) -> list[ImagePlacement]:
    """Adjust start/end times for proper image placement
    
    - Apply lead time to start_time (image appears slightly before the word)
    - Extend end_time until the next image starts
    - Processes in reverse order (latest first) to properly chain times.
    
    Args:
        placements: List of ImagePlacement (will be modified in place)
        timeline_end: End time of the timeline
        
    Returns:
        Same list sorted by start_time ascending
    """
    from config import SUBTITLE_LEAD_TIME_MS
    
    lead_time_sec = SUBTITLE_LEAD_TIME_MS / 1000.0
    
    # Sort by start_time ascending first (to apply lead time with prev boundary check)
    sorted_placements = sorted(placements, key=lambda p: p.start_time)
    
    # Apply lead time to start_time (but not before previous end)
    prev_end = 0.0
    for p in sorted_placements:
        original_start = p.start_time
        adjusted_start = original_start - lead_time_sec
        # Clamp to not go before previous image's end time
        p.start_time = max(adjusted_start, prev_end)
        prev_end = p.start_time  # Next image should not start before this
    
    # Now adjust end times (process in reverse, latest first)
    sorted_placements.reverse()
    next_start = timeline_end
    
    for p in sorted_placements:
        p.end_time = next_start
        next_start = p.start_time
    
    # Return sorted by start_time ascending
    return sorted(sorted_placements, key=lambda p: p.start_time)


def process_auteur_import(
    auteur_file: str,
    image_folder: str,
    clips: list,
    timeline_end: float,
    similarity_threshold: float = 70.0
) -> list[ImagePlacement]:
    """Main entry point: load Auteur project and generate image placements
    
    Args:
        auteur_file: Path to Auteur .json project file
        image_folder: Path to folder containing n-m.ext images
        clips: List of TimelineClip from PBB
        timeline_end: End time of the timeline
        similarity_threshold: Minimum fuzzy match score
        
    Returns:
        List of ImagePlacement with adjusted times and image paths
    """
    # 1. Load hints from Auteur
    hints = load_auteur_project(auteur_file)
    print(f"Loaded {len(hints)} hints from Auteur project")
    
    # 2. Match hints to clips
    placements = match_hints_to_clips(hints, clips, similarity_threshold)
    print(f"Matched {len(placements)} placements")
    
    # 3. Deduplicate (same scene-shot may appear multiple times)
    placements = deduplicate_placements(placements)
    print(f"After deduplication: {len(placements)} unique placements")
    
    # 4. Find image files
    found_count = 0
    for p in placements:
        p.image_path = find_image_file(image_folder, p.scene_id, p.shot_id)
        if p.image_path:
            found_count += 1
    
    # 5. Filter out placements without images
    placements = [p for p in placements if p.image_path]
    print(f"Found {found_count} images, {len(placements)} placements with images")
    
    # 6. Adjust end times (reverse order processing)
    if placements:
        placements = adjust_end_times(placements, timeline_end)
    
    return placements
