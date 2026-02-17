"""
Media Registry - Media asset definitions with UUID-based identification.

Implements the Media Pool layer of the 3-layer NLE architecture:
  Media Registry → Timeline → Script

Each MediaAsset holds metadata about a source file (path, duration, codec info).
Timeline items reference media assets by UUID for non-destructive editing.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class WordTiming:
    """A single word with timing information from alignment."""
    text: str
    start: float
    end: float
    confidence: float = 1.0

    def to_dict(self) -> dict:
        d = {"text": self.text, "start": self.start, "end": self.end}
        if self.confidence != 1.0:
            d["confidence"] = self.confidence
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "WordTiming":
        return cls(
            text=data["text"],
            start=data["start"],
            end=data["end"],
            confidence=data.get("confidence", 1.0),
        )


@dataclass
class AlignmentSegment:
    """A segment of aligned text with word-level timing."""
    text: str
    start: float
    end: float
    words: List[WordTiming] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlignmentSegment":
        return cls(
            text=data["text"],
            start=data["start"],
            end=data["end"],
            words=[WordTiming.from_dict(w) for w in data.get("words", [])],
        )


@dataclass
class AlignmentData:
    """Alignment result attached to an audio/video media asset.

    Stores the output of Whisper transcription or forced alignment,
    keeping it bound to the media asset rather than scattered across clips.
    """
    script_text: str = ""
    segments: List[AlignmentSegment] = field(default_factory=list)
    method: str = "whisper"  # "whisper" | "forced_alignment"

    def to_dict(self) -> dict:
        return {
            "script_text": self.script_text,
            "segments": [s.to_dict() for s in self.segments],
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlignmentData":
        return cls(
            script_text=data.get("script_text", ""),
            segments=[AlignmentSegment.from_dict(s) for s in data.get("segments", [])],
            method=data.get("method", "whisper"),
        )


# ---------------------------------------------------------------------------
# Media Assets
# ---------------------------------------------------------------------------

@dataclass
class MediaAsset:
    """Base class for all media assets in the registry.

    Every asset gets a UUID so that timeline items can reference it
    without hard-coupling to file paths.
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    path: str = ""
    media_type: str = "generic"  # "audio" | "image" | "video"

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "path": self.path,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MediaAsset":
        media_type = data.get("media_type", "generic")
        # Dispatch to correct subclass
        if media_type == "audio":
            return AudioMedia.from_dict(data)
        elif media_type == "image":
            return ImageMedia.from_dict(data)
        elif media_type == "video":
            return VideoMedia.from_dict(data)
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            path=data.get("path", ""),
            media_type=media_type,
        )


@dataclass
class AudioMedia(MediaAsset):
    """An audio source file with optional alignment data."""
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    speaker: str = ""
    alignment: Optional[AlignmentData] = None

    def __post_init__(self):
        self.media_type = "audio"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "duration": self.duration,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "speaker": self.speaker,
        })
        if self.alignment is not None:
            d["alignment"] = self.alignment.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AudioMedia":
        alignment = None
        if "alignment" in data:
            alignment = AlignmentData.from_dict(data["alignment"])
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            path=data.get("path", ""),
            duration=data.get("duration", 0.0),
            sample_rate=data.get("sample_rate", 0),
            channels=data.get("channels", 0),
            speaker=data.get("speaker", ""),
            alignment=alignment,
        )


@dataclass
class ImageMedia(MediaAsset):
    """An image source file."""
    width: int = 0
    height: int = 0
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.media_type = "image"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "width": self.width,
            "height": self.height,
        })
        if self.tags:
            d["tags"] = self.tags
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ImageMedia":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            path=data.get("path", ""),
            width=data.get("width", 0),
            height=data.get("height", 0),
            tags=data.get("tags", []),
        )


@dataclass
class VideoMedia(MediaAsset):
    """A video source file with optional alignment data."""
    duration: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    has_audio: bool = False
    alignment: Optional[AlignmentData] = None

    def __post_init__(self):
        self.media_type = "video"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
        })
        if self.alignment is not None:
            d["alignment"] = self.alignment.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "VideoMedia":
        alignment = None
        if "alignment" in data:
            alignment = AlignmentData.from_dict(data["alignment"])
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            path=data.get("path", ""),
            duration=data.get("duration", 0.0),
            fps=data.get("fps", 0.0),
            width=data.get("width", 0),
            height=data.get("height", 0),
            has_audio=data.get("has_audio", False),
            alignment=alignment,
        )


# ---------------------------------------------------------------------------
# Media Registry
# ---------------------------------------------------------------------------

class MediaRegistry:
    """Central registry of all media assets in a project.

    Provides UUID-based lookup so that timeline items never need to
    store file paths directly.
    """

    def __init__(self):
        self._assets: dict[str, MediaAsset] = {}

    # -- Mutation ----------------------------------------------------------

    def add(self, asset: MediaAsset) -> str:
        """Register *asset* and return its UUID."""
        self._assets[asset.uuid] = asset
        return asset.uuid

    def remove(self, uuid: str) -> Optional[MediaAsset]:
        """Remove and return the asset, or ``None`` if not found."""
        return self._assets.pop(uuid, None)

    # -- Query -------------------------------------------------------------

    def get(self, uuid: str) -> Optional[MediaAsset]:
        """Look up an asset by UUID."""
        return self._assets.get(uuid)

    def find_by_path(self, path: str) -> Optional[MediaAsset]:
        """Find the first asset whose *path* matches."""
        for asset in self._assets.values():
            if asset.path == path:
                return asset
        return None

    def find_by_speaker(self, speaker: str) -> Optional["AudioMedia"]:
        """Find the first AudioMedia whose *speaker* matches."""
        for asset in self._assets.values():
            if isinstance(asset, AudioMedia) and asset.speaker == speaker:
                return asset
        return None

    def all(self) -> list[MediaAsset]:
        """Return all registered assets."""
        return list(self._assets.values())

    def audio_assets(self) -> list["AudioMedia"]:
        """Return only audio assets."""
        return [a for a in self._assets.values() if isinstance(a, AudioMedia)]

    def image_assets(self) -> list["ImageMedia"]:
        """Return only image assets."""
        return [a for a in self._assets.values() if isinstance(a, ImageMedia)]

    def video_assets(self) -> list["VideoMedia"]:
        """Return only video assets."""
        return [a for a in self._assets.values() if isinstance(a, VideoMedia)]

    def __len__(self) -> int:
        return len(self._assets)

    def __contains__(self, uuid: str) -> bool:
        return uuid in self._assets

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> list[dict]:
        """Serialize all assets to a list of dicts."""
        return [asset.to_dict() for asset in self._assets.values()]

    @classmethod
    def from_dict(cls, data: list[dict]) -> "MediaRegistry":
        """Reconstruct from a list of asset dicts."""
        registry = cls()
        for item in data:
            asset = MediaAsset.from_dict(item)
            registry._assets[asset.uuid] = asset
        return registry
