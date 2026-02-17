"""
Project - Top-level container for the entire PBB project.

Brings together the three separated concerns:
  1. Media Registry  — source files and their metadata
  2. Timeline        — tracks, items, and editing state
  3. Script          — the authored script that drives AI pipelines

Also provides a backward-compatible conversion layer so that legacy
TimelineClip-based .pbb files can be round-tripped through the new model.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from models.media import MediaRegistry, AudioMedia, ImageMedia
from models.timeline import (
    Timeline,
    Track,
    AudioItem,
    ImageItem,
    SubtitleItem,
    SubtitleWordSegment,
)
from models.script import Script, ScriptLine


PROJECT_FORMAT_VERSION = "2.0"


@dataclass
class Project:
    """Root object of a PBB project.

    Attributes:
        uuid: Unique project identifier.
        name: Human-readable project name.
        created_at: ISO-8601 timestamp.
        settings: Arbitrary per-project settings dict (render params, etc.).
        media_registry: All imported media assets.
        timeline: The editing timeline.
        script: The production script.
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Untitled"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    settings: dict = field(default_factory=dict)

    media_registry: MediaRegistry = field(default_factory=MediaRegistry)
    timeline: Timeline = field(default_factory=Timeline)
    script: Script = field(default_factory=Script)

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": PROJECT_FORMAT_VERSION,
            "uuid": self.uuid,
            "name": self.name,
            "created_at": self.created_at,
            "settings": self.settings,
            "media_registry": self.media_registry.to_dict(),
            "timeline": self.timeline.to_dict(),
            "script": self.script.to_dict(),
        }

    @classmethod
    def _parse_version(cls, version_str: str) -> tuple[int, ...]:
        """Parse a dotted version string into a comparable tuple of ints."""
        try:
            return tuple(int(p) for p in version_str.split("."))
        except (ValueError, AttributeError):
            return (0,)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        version = data.get("version", "1.1")
        if cls._parse_version(version) < (2, 0):
            return cls._from_legacy_dict(data)
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            name=data.get("name", "Untitled"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            settings=data.get("settings", {}),
            media_registry=MediaRegistry.from_dict(data.get("media_registry", [])),
            timeline=Timeline.from_dict(data.get("timeline", {})),
            script=Script.from_dict(data.get("script", {})),
        )

    # -- Legacy conversion (v1.x → v2.0) ----------------------------------

    @classmethod
    def _from_legacy_dict(cls, data: dict) -> "Project":
        """Convert a v1.x project dict to the new model.

        Legacy format stores:
          - speaker_audio_map: {speaker_name: audio_path}
          - clips: [{id, name, start, duration, track, color, clip_type, ...}]
          - script_content / script_path
          - image_folder
          - settings: RuntimeConfig dict
        """
        project = cls(
            name=data.get("script_path", "Untitled"),
            settings=data.get("settings", {}),
        )

        # 1. Build media registry from speaker_audio_map
        speaker_audio_map = data.get("speaker_audio_map", {})
        speaker_to_media_uuid: dict[str, str] = {}
        for speaker, audio_path in speaker_audio_map.items():
            media = AudioMedia(path=audio_path, speaker=speaker)
            project.media_registry.add(media)
            speaker_to_media_uuid[speaker] = media.uuid

        # 2. Build media registry entries for images found in clips
        image_path_to_uuid: dict[str, str] = {}
        for clip_data in data.get("clips", []):
            img_path = clip_data.get("image_path", "")
            if img_path and img_path not in image_path_to_uuid:
                img_media = ImageMedia(path=img_path)
                project.media_registry.add(img_media)
                image_path_to_uuid[img_path] = img_media.uuid

        # 3. Create default tracks (audio=0, subtitle=1, image=2)
        audio_track = Track(name="Audio", track_type="audio")
        subtitle_track = Track(name="Subtitle", track_type="subtitle")
        image_track = Track(name="Image", track_type="video")
        project.timeline.add_track(audio_track)
        project.timeline.add_track(subtitle_track)
        project.timeline.add_track(image_track)

        # 4. Convert legacy clips to new timeline items
        for clip_data in data.get("clips", []):
            clip_type = clip_data.get("clip_type", "audio")
            start = clip_data.get("start", 0.0)
            duration = clip_data.get("duration", 0.0)
            speaker = clip_data.get("speaker", "")

            if clip_type == "audio":
                item = AudioItem(
                    timeline_start=start,
                    timeline_duration=duration,
                    media_uuid=speaker_to_media_uuid.get(speaker, ""),
                    source_in=clip_data.get("offset", 0.0),
                    source_out=clip_data.get("offset", 0.0) + duration,
                    volume=clip_data.get("volume", 1.0),
                    speaker=speaker,
                    segment_index=clip_data.get("segment_index", -1),
                )
                audio_track.add_item(item)

            elif clip_type == "subtitle":
                words = []
                for w in clip_data.get("words", []):
                    words.append(SubtitleWordSegment(
                        text=w.get("text", ""),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                    ))
                item = SubtitleItem(
                    timeline_start=start,
                    timeline_duration=duration,
                    text=clip_data.get("name", ""),
                    source_media_uuid=speaker_to_media_uuid.get(speaker, ""),
                    alignment_segment_index=clip_data.get("segment_index", -1),
                    words=words,
                )
                subtitle_track.add_item(item)

            elif clip_type == "image":
                img_path = clip_data.get("image_path", "")
                item = ImageItem(
                    timeline_start=start,
                    timeline_duration=duration,
                    media_uuid=image_path_to_uuid.get(img_path, ""),
                )
                image_track.add_item(item)

        # 5. Build script from script_content (if present)
        script_content = data.get("script_content", "")
        if script_content:
            from core.script_parser import ScriptParser
            parser = ScriptParser()
            dialogue_lines = parser.parse_text(script_content)
            for dl in dialogue_lines:
                project.script.lines.append(ScriptLine(
                    speaker=dl.speaker,
                    text=dl.text,
                    media_uuid=speaker_to_media_uuid.get(dl.speaker, ""),
                ))

        return project
