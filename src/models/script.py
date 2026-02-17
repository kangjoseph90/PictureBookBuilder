"""
Script - Script data structure for script-driven editing.

The Script layer sits alongside the Timeline and Media Registry:
  Media Registry → Timeline → Script

It stores the original authored script (speaker + dialogue lines)
so that AI pipelines can re-derive timeline content from it.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ScriptLine:
    """A single line of the production script.

    Attributes:
        speaker: Who is speaking this line.
        text: The dialogue/narration text.
        media_uuid: Optional UUID of the associated audio/video media.
        image_hint: Keyword or description used for image matching.
        scene_description: Longer description of the visual scene.
    """
    speaker: str = ""
    text: str = ""
    media_uuid: str = ""
    image_hint: str = ""
    scene_description: str = ""

    def to_dict(self) -> dict:
        d: dict = {"speaker": self.speaker, "text": self.text}
        if self.media_uuid:
            d["media_uuid"] = self.media_uuid
        if self.image_hint:
            d["image_hint"] = self.image_hint
        if self.scene_description:
            d["scene_description"] = self.scene_description
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptLine":
        return cls(
            speaker=data.get("speaker", ""),
            text=data.get("text", ""),
            media_uuid=data.get("media_uuid", ""),
            image_hint=data.get("image_hint", ""),
            scene_description=data.get("scene_description", ""),
        )


@dataclass
class Script:
    """The full production script for a project.

    Maintains the original authored order of dialogue lines and
    provides helpers for speaker/media queries.
    """
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    lines: List[ScriptLine] = field(default_factory=list)

    # -- Query helpers -----------------------------------------------------

    def speakers(self) -> list[str]:
        """Return unique speakers in order of first appearance."""
        seen: set[str] = set()
        result: list[str] = []
        for line in self.lines:
            if line.speaker and line.speaker not in seen:
                seen.add(line.speaker)
                result.append(line.speaker)
        return result

    def lines_for_speaker(self, speaker: str) -> list[ScriptLine]:
        return [l for l in self.lines if l.speaker == speaker]

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "lines": [l.to_dict() for l in self.lines],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Script":
        return cls(
            uuid=data.get("uuid", str(uuid.uuid4())),
            lines=[ScriptLine.from_dict(l) for l in data.get("lines", [])],
        )
