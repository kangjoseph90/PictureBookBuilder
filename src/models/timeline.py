"""
Timeline - Tracks and timeline items for non-destructive editing.

Implements the Timeline layer of the 3-layer NLE architecture:
  Media Registry → Timeline → Script

TimelineItems are *instances* of media clips placed on tracks.
They reference media assets by UUID and store timeline coordinates
plus type-specific parameters (volume, crop, subtitle style, etc.).
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Timeline Items (abstract base + concrete types)
# ---------------------------------------------------------------------------

@dataclass
class TimelineItem:
    """Abstract base for anything placed on a timeline track.

    Attributes:
        uuid: Unique identifier for this item.
        timeline_start: When this item begins on the timeline (seconds).
        timeline_duration: How long this item occupies on the timeline (seconds).
        track_uuid: UUID of the owning Track (set when added).
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeline_start: float = 0.0
    timeline_duration: float = 0.0
    track_uuid: str = ""
    item_type: str = "generic"  # discriminator for serialization

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.timeline_duration

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "timeline_start": self.timeline_start,
            "timeline_duration": self.timeline_duration,
            "track_uuid": self.track_uuid,
            "item_type": self.item_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineItem":
        """Dispatch to the correct subclass based on *item_type*."""
        item_type = data.get("item_type", "generic")
        subclass_map = {
            "audio": AudioItem,
            "video": VideoItem,
            "image": ImageItem,
            "subtitle": SubtitleItem,
        }
        target_cls = subclass_map.get(item_type, cls)
        return target_cls.from_dict(data) if target_cls is not cls else cls._from_dict_base(data)

    @classmethod
    def _from_dict_base(cls, data: dict) -> "TimelineItem":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            timeline_start=data.get("timeline_start", 0.0),
            timeline_duration=data.get("timeline_duration", 0.0),
            track_uuid=data.get("track_uuid", ""),
            item_type=data.get("item_type", "generic"),
        )


@dataclass
class AudioItem(TimelineItem):
    """An audio clip on the timeline — references an AudioMedia asset."""
    media_uuid: str = ""
    source_in: float = 0.0   # Start position in source media (seconds)
    source_out: float = 0.0  # End position in source media (seconds)
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    speaker: str = ""        # Convenience: speaker name (also on AudioMedia)
    segment_index: int = -1  # Index in alignment segments

    def __post_init__(self):
        self.item_type = "audio"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "media_uuid": self.media_uuid,
            "source_in": self.source_in,
            "source_out": self.source_out,
            "volume": self.volume,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "speaker": self.speaker,
            "segment_index": self.segment_index,
        })
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AudioItem":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            timeline_start=data.get("timeline_start", 0.0),
            timeline_duration=data.get("timeline_duration", 0.0),
            track_uuid=data.get("track_uuid", ""),
            media_uuid=data.get("media_uuid", ""),
            source_in=data.get("source_in", 0.0),
            source_out=data.get("source_out", 0.0),
            volume=data.get("volume", 1.0),
            fade_in=data.get("fade_in", 0.0),
            fade_out=data.get("fade_out", 0.0),
            speaker=data.get("speaker", ""),
            segment_index=data.get("segment_index", -1),
        )


@dataclass
class VideoItem(TimelineItem):
    """A video clip on the timeline — references a VideoMedia asset."""
    media_uuid: str = ""
    source_in: float = 0.0
    source_out: float = 0.0
    crop: Optional[dict] = None     # {x, y, w, h} normalized
    scale: float = 1.0
    position: Optional[dict] = None  # {x, y} normalized

    def __post_init__(self):
        self.item_type = "video"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "media_uuid": self.media_uuid,
            "source_in": self.source_in,
            "source_out": self.source_out,
            "scale": self.scale,
        })
        if self.crop is not None:
            d["crop"] = self.crop
        if self.position is not None:
            d["position"] = self.position
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "VideoItem":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            timeline_start=data.get("timeline_start", 0.0),
            timeline_duration=data.get("timeline_duration", 0.0),
            track_uuid=data.get("track_uuid", ""),
            media_uuid=data.get("media_uuid", ""),
            source_in=data.get("source_in", 0.0),
            source_out=data.get("source_out", 0.0),
            crop=data.get("crop"),
            scale=data.get("scale", 1.0),
            position=data.get("position"),
        )


@dataclass
class ImageItem(TimelineItem):
    """An image placed on the timeline — references an ImageMedia asset."""
    media_uuid: str = ""
    crop: Optional[dict] = None
    scale: float = 1.0
    position: Optional[dict] = None
    ken_burns: Optional[dict] = None  # Future: {start_rect, end_rect}

    def __post_init__(self):
        self.item_type = "image"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "media_uuid": self.media_uuid,
            "scale": self.scale,
        })
        if self.crop is not None:
            d["crop"] = self.crop
        if self.position is not None:
            d["position"] = self.position
        if self.ken_burns is not None:
            d["ken_burns"] = self.ken_burns
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ImageItem":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            timeline_start=data.get("timeline_start", 0.0),
            timeline_duration=data.get("timeline_duration", 0.0),
            track_uuid=data.get("track_uuid", ""),
            media_uuid=data.get("media_uuid", ""),
            crop=data.get("crop"),
            scale=data.get("scale", 1.0),
            position=data.get("position"),
            ken_burns=data.get("ken_burns"),
        )


@dataclass
class SubtitleWordSegment:
    """Word-level timing within a subtitle item."""
    text: str = ""
    start: float = 0.0
    end: float = 0.0

    def to_dict(self) -> dict:
        return {"text": self.text, "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleWordSegment":
        return cls(
            text=data.get("text", ""),
            start=data.get("start", 0.0),
            end=data.get("end", 0.0),
        )


@dataclass
class SubtitleItem(TimelineItem):
    """A subtitle/text element on the timeline.

    Optionally linked to an audio/video media asset via
    *source_media_uuid* so that the subtitle can be traced back
    to the audio it was generated from.
    """
    text: str = ""
    style: Optional[dict] = None  # {font, size, color, ...}
    source_media_uuid: str = ""   # Optional link to the audio source
    alignment_segment_index: int = -1
    words: List[SubtitleWordSegment] = field(default_factory=list)

    def __post_init__(self):
        self.item_type = "subtitle"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "text": self.text,
            "source_media_uuid": self.source_media_uuid,
            "alignment_segment_index": self.alignment_segment_index,
        })
        if self.style is not None:
            d["style"] = self.style
        if self.words:
            d["words"] = [w.to_dict() for w in self.words]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleItem":
        words = [SubtitleWordSegment.from_dict(w) for w in data.get("words", [])]
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            timeline_start=data.get("timeline_start", 0.0),
            timeline_duration=data.get("timeline_duration", 0.0),
            track_uuid=data.get("track_uuid", ""),
            text=data.get("text", ""),
            style=data.get("style"),
            source_media_uuid=data.get("source_media_uuid", ""),
            alignment_segment_index=data.get("alignment_segment_index", -1),
            words=words,
        )


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """A single track that holds a list of timeline items.

    Attributes:
        track_type: "audio" | "video" | "subtitle" — determines what
            item types are expected (though not strictly enforced).
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    track_type: str = "audio"  # "audio" | "video" | "subtitle"
    items: List[TimelineItem] = field(default_factory=list)

    def add_item(self, item: TimelineItem) -> None:
        item.track_uuid = self.uuid
        self.items.append(item)

    def remove_item(self, item_uuid: str) -> Optional[TimelineItem]:
        for i, item in enumerate(self.items):
            if item.uuid == item_uuid:
                return self.items.pop(i)
        return None

    def get_item(self, item_uuid: str) -> Optional[TimelineItem]:
        for item in self.items:
            if item.uuid == item_uuid:
                return item
        return None

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "track_type": self.track_type,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        items = []
        for item_data in data.get("items", []):
            item_type = item_data.get("item_type", "generic")
            subclass_map = {
                "audio": AudioItem,
                "video": VideoItem,
                "image": ImageItem,
                "subtitle": SubtitleItem,
            }
            target_cls = subclass_map.get(item_type, TimelineItem)
            items.append(target_cls.from_dict(item_data))
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            name=data.get("name", ""),
            track_type=data.get("track_type", "audio"),
            items=items,
        )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@dataclass
class Timeline:
    """Top-level timeline container holding multiple tracks.

    Provides convenience helpers for finding items across all tracks.
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Main Timeline"
    tracks: List[Track] = field(default_factory=list)

    # -- Convenience -------------------------------------------------------

    def add_track(self, track: Track) -> None:
        self.tracks.append(track)

    def get_track(self, track_uuid: str) -> Optional[Track]:
        for t in self.tracks:
            if t.uuid == track_uuid:
                return t
        return None

    def get_track_by_type(self, track_type: str) -> Optional[Track]:
        """Return the first track with the given type."""
        for t in self.tracks:
            if t.track_type == track_type:
                return t
        return None

    def all_items(self) -> list[TimelineItem]:
        """Flatten all items across all tracks."""
        result: list[TimelineItem] = []
        for track in self.tracks:
            result.extend(track.items)
        return result

    @property
    def duration(self) -> float:
        """Total timeline duration (end of the last item)."""
        ends = [item.timeline_end for item in self.all_items()]
        return max(ends) if ends else 0.0

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Timeline":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            name=data.get("name", "Main Timeline"),
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
        )
