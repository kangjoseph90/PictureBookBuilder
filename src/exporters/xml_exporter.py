"""
XML Exporter - Generate Premiere Pro compatible XML project files
"""
from pathlib import Path
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.dom import minidom

from config import VIDEO_FPS


@dataclass
class TimelineClip:
    """A clip in the timeline"""
    name: str
    file_path: str
    start_time: float  # Position in timeline (seconds)
    end_time: float
    track: int = 1
    clip_type: str = "audio"  # "audio" or "video"


class XMLExporter:
    """Export timeline to Premiere Pro compatible XML"""
    
    def __init__(self, fps: int = VIDEO_FPS):
        self.fps = fps
    
    def seconds_to_frames(self, seconds: float) -> int:
        """Convert seconds to frame number"""
        return int(seconds * self.fps)
    
    def create_xmeml(
        self,
        clips: list[TimelineClip],
        project_name: str = "PictureBookProject",
        sequence_name: str = "Main Sequence"
    ) -> ET.Element:
        """Create XMEML (Final Cut Pro XML) structure
        
        This format is compatible with both Premiere Pro and Final Cut Pro.
        """
        # Root element
        xmeml = ET.Element('xmeml', version="5")
        
        # Project
        project = ET.SubElement(xmeml, 'project')
        ET.SubElement(project, 'name').text = project_name
        
        # Children/Sequence
        children = ET.SubElement(project, 'children')
        sequence = ET.SubElement(children, 'sequence')
        ET.SubElement(sequence, 'name').text = sequence_name
        ET.SubElement(sequence, 'duration').text = str(self._get_total_duration(clips))
        
        # Rate
        rate = ET.SubElement(sequence, 'rate')
        ET.SubElement(rate, 'timebase').text = str(self.fps)
        ET.SubElement(rate, 'ntsc').text = 'FALSE'
        
        # Media
        media = ET.SubElement(sequence, 'media')
        
        # Video track (for images)
        video = ET.SubElement(media, 'video')
        video_clips = [c for c in clips if c.clip_type == 'video']
        if video_clips:
            self._add_video_tracks(video, video_clips)
        
        # Audio tracks (for voice)
        audio = ET.SubElement(media, 'audio')
        audio_clips = [c for c in clips if c.clip_type == 'audio']
        if audio_clips:
            self._add_audio_tracks(audio, audio_clips)
        
        return xmeml
    
    def _get_total_duration(self, clips: list[TimelineClip]) -> int:
        """Get total duration in frames"""
        if not clips:
            return 0
        max_end = max(c.end_time for c in clips)
        return self.seconds_to_frames(max_end)
    
    def _add_video_tracks(self, video_elem: ET.Element, clips: list[TimelineClip]):
        """Add video tracks with clips"""
        # Group by track
        tracks: dict[int, list[TimelineClip]] = {}
        for clip in clips:
            if clip.track not in tracks:
                tracks[clip.track] = []
            tracks[clip.track].append(clip)
        
        for track_num in sorted(tracks.keys()):
            track = ET.SubElement(video_elem, 'track')
            for clip in sorted(tracks[track_num], key=lambda c: c.start_time):
                self._add_clip_item(track, clip, 'video')
    
    def _add_audio_tracks(self, audio_elem: ET.Element, clips: list[TimelineClip]):
        """Add audio tracks with clips"""
        # Group by track
        tracks: dict[int, list[TimelineClip]] = {}
        for clip in clips:
            if clip.track not in tracks:
                tracks[clip.track] = []
            tracks[clip.track].append(clip)
        
        for track_num in sorted(tracks.keys()):
            track = ET.SubElement(audio_elem, 'track')
            for clip in sorted(tracks[track_num], key=lambda c: c.start_time):
                self._add_clip_item(track, clip, 'audio')
    
    def _add_clip_item(self, track: ET.Element, clip: TimelineClip, media_type: str):
        """Add a single clip item to track"""
        clipitem = ET.SubElement(track, 'clipitem')
        ET.SubElement(clipitem, 'name').text = clip.name
        
        # Duration and position
        duration_frames = self.seconds_to_frames(clip.end_time - clip.start_time)
        ET.SubElement(clipitem, 'duration').text = str(duration_frames)
        ET.SubElement(clipitem, 'start').text = str(self.seconds_to_frames(clip.start_time))
        ET.SubElement(clipitem, 'end').text = str(self.seconds_to_frames(clip.end_time))
        
        # In/out points (assume full clip is used)
        ET.SubElement(clipitem, 'in').text = '0'
        ET.SubElement(clipitem, 'out').text = str(duration_frames)
        
        # Rate
        rate = ET.SubElement(clipitem, 'rate')
        ET.SubElement(rate, 'timebase').text = str(self.fps)
        ET.SubElement(rate, 'ntsc').text = 'FALSE'
        
        # File reference
        file_elem = ET.SubElement(clipitem, 'file')
        ET.SubElement(file_elem, 'name').text = Path(clip.file_path).name
        ET.SubElement(file_elem, 'pathurl').text = f"file://localhost/{clip.file_path.replace(chr(92), '/')}"
        
        # Media info
        media = ET.SubElement(file_elem, 'media')
        if media_type == 'video':
            video = ET.SubElement(media, 'video')
            ET.SubElement(video, 'duration').text = str(duration_frames)
        else:
            audio = ET.SubElement(media, 'audio')
            ET.SubElement(audio, 'duration').text = str(duration_frames)
    
    def to_string(self, xmeml: ET.Element) -> str:
        """Convert XML element to pretty-printed string"""
        rough_string = ET.tostring(xmeml, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")
    
    def save(
        self,
        clips: list[TimelineClip],
        output_path: str | Path,
        project_name: str = "PictureBookProject",
        sequence_name: str = "Main Sequence"
    ) -> None:
        """Save timeline to XML file
        
        Args:
            clips: List of TimelineClip objects
            output_path: Output file path
            project_name: Name for the project
            sequence_name: Name for the sequence
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        xmeml = self.create_xmeml(clips, project_name, sequence_name)
        xml_string = self.to_string(xmeml)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_string)
