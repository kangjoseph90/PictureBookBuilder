"""
Auteur Importer - Import scene/shot info from Auteur project files
and match with PBB timeline clips for automatic image placement.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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


def is_stage_direction(text: str) -> bool:
    """Check if text is a stage direction (non-spoken, like actions in parentheses)
    
    Examples of stage directions:
    - "(Silent Reaction to the news)"
    - "(The brother walks away)"
    - "(Pause)"
    """
    text = text.strip()
    # Check if entire text is wrapped in parentheses
    if text.startswith('(') and text.endswith(')'):
        return True
    # Check if text is wrapped in brackets
    if text.startswith('[') and text.endswith(']'):
        return True
    return False


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
                
                # Skip stage directions (they're not in the audio)
                if is_stage_direction(text):
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
) -> list[ImagePlacement]:
    """Match Auteur hints to PBB timeline clips using exact matching with DP optimization
    
    Algorithm:
    1. Collect all words from clips with timeline positions
    2. Filter valid hints (skip stage directions, non-existent speakers)
    3. Find exact matches for each hint
    4. Use DP to select optimal non-overlapping matches
    
    Args:
        hints: List of AuteurHint from Auteur project
        clips: List of TimelineClip from PBB timeline
        
    Returns:
        List of ImagePlacement with matched time ranges
    """
    # Step 1: Collect all words from clips
    all_words = []
    
    for clip in clips:
        if clip.clip_type not in ('audio', 'subtitle'):
            continue
        
        speaker = clip.speaker or ""
        clip_start = clip.start
        clip_offset = getattr(clip, 'offset', 0.0)
        
        for word in (clip.words or []):
            word_text = word.text if hasattr(word, 'text') else str(word)
            word_start = (word.start if hasattr(word, 'start') else 0)
            word_end = (word.end if hasattr(word, 'end') else 0)
            
            # Convert to timeline position
            timeline_start = clip_start + (word_start - clip_offset)
            timeline_end = clip_start + (word_end - clip_offset)
            
            all_words.append({
                'text': normalize_text(word_text),
                'start': timeline_start,
                'end': timeline_end,
                'speaker': speaker
            })
    
    all_words.sort(key=lambda w: w['start'])
    
    # Get available speakers
    available_speakers = set(w['speaker'] for w in all_words if w['speaker'])
    
    # Step 2: Find exact matches for each valid hint
    all_matches = []  # [(hint_idx, hint, start_word_idx, end_word_idx), ...]
    matched_hints = set()
    
    for hint_idx, hint in enumerate(hints):
        # Skip invalid hints
        if not hint.text:
            continue
        if hint.speaker and hint.speaker not in available_speakers:
            continue
        
        target = hint.text
        target_len = len(target)
        found = False
        
        # Search for exact match
        for start_idx in range(len(all_words)):
            concat = ""
            
            for end_idx in range(start_idx, min(start_idx + 50, len(all_words))):
                concat += all_words[end_idx]['text']
                
                if concat == target:
                    all_matches.append({
                        'hint_idx': hint_idx,
                        'hint': hint,
                        'start_idx': start_idx,
                        'end_idx': end_idx
                    })
                    matched_hints.add(hint_idx)
                    found = True
                    break
                
                if len(concat) > target_len:
                    break
            
            if found:
                break
        
        # Debug: Show unmatched hints
        if not found:
            print(f"[No Match] Shot {hint.scene_id}-{hint.shot_id}: \"{hint.original_text[:50]}...\"" 
                  if len(hint.original_text) > 50 else f"[No Match] Shot {hint.scene_id}-{hint.shot_id}: \"{hint.original_text}\"")
    
    if not all_matches:
        print("No exact matches found!")
        return []
    
    # Step 3: DP for optimal non-overlapping selection
    # Sort by end_idx for DP, but also by hint_idx for stability
    all_matches.sort(key=lambda m: (m['end_idx'], m['hint_idx']))
    n = len(all_matches)
    
    dp_count = [1] * n  # Number of matches in optimal path ending at i
    dp_prev = [-1] * n
    
    for i in range(n):
        # Find the BEST previous compatible match (not just any)
        best_prev = -1
        best_prev_count = 0
        
        for j in range(i):
            if (all_matches[j]['end_idx'] < all_matches[i]['start_idx'] and 
                all_matches[j]['hint_idx'] < all_matches[i]['hint_idx']):
                if dp_count[j] > best_prev_count:
                    best_prev_count = dp_count[j]
                    best_prev = j
        
        if best_prev >= 0:
            dp_count[i] = best_prev_count + 1
            dp_prev[i] = best_prev
    
    # Backtrack
    best_end = max(range(n), key=lambda i: dp_count[i])
    selected = []
    idx = best_end
    while idx >= 0:
        selected.append(idx)
        idx = dp_prev[idx]
    selected.reverse()
    
    # Step 4: Build placements
    placements = []
    for sel_idx in selected:
        m = all_matches[sel_idx]
        placements.append(ImagePlacement(
            scene_id=m['hint'].scene_id,
            shot_id=m['hint'].shot_id,
            start_time=all_words[m['start_idx']]['start'],
            end_time=all_words[m['end_idx']]['end']
        ))
    
    print(f"Matched {len(placements)} shots out of {len(hints)} hints")
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
    timeline_end: float
) -> list[ImagePlacement]:
    """Main entry point: load Auteur project and generate image placements
    
    Args:
        auteur_file: Path to Auteur .json project file
        image_folder: Path to folder containing n-m.ext images
        clips: List of TimelineClip from PBB
        timeline_end: End time of the timeline
        
    Returns:
        List of ImagePlacement with adjusted times and image paths
    """
    # 1. Load hints from Auteur
    hints = load_auteur_project(auteur_file)
    print(f"Loaded {len(hints)} hints from Auteur project")
    
    # 2. Match hints to clips
    placements = match_hints_to_clips(hints, clips)
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
    
    # 6. Adjust end times and apply lead time
    if placements:
        placements = adjust_end_times(placements, timeline_end)
    
    # 7. Snap to clip boundaries (AFTER lead time is applied)
    if placements and clips:
        placements = snap_to_clip_boundaries(placements, clips)
    
    return placements


def snap_to_clip_boundaries(
    placements: list[ImagePlacement],
    clips: list,
    threshold: float = 0.5  # seconds
) -> list[ImagePlacement]:
    """Snap placement start/end times to nearby clip boundaries
    
    Args:
        placements: List of ImagePlacement
        clips: List of TimelineClip
        threshold: Maximum distance to snap (seconds)
        
    Returns:
        List of ImagePlacement with snapped times
    """
    # Collect all clip boundaries
    boundaries = set()
    for clip in clips:
        if clip.clip_type in ('audio', 'subtitle'):
            boundaries.add(clip.start)
            boundaries.add(clip.start + clip.duration)
    
    boundaries = sorted(boundaries)
    
    def find_nearest(time: float) -> float:
        """Find nearest boundary within threshold"""
        best_dist = threshold
        best_boundary = time
        
        for b in boundaries:
            dist = abs(b - time)
            if dist < best_dist:
                best_dist = dist
                best_boundary = b
        
        return best_boundary
    
    # Snap each placement
    for p in placements:
        p.start_time = find_nearest(p.start_time)
        p.end_time = find_nearest(p.end_time)
    
    return placements
