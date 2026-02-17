"""
Unit tests for the new PBB data model layer (models package).

Tests cover:
  - Media assets: creation, serialization, registry operations
  - Timeline: tracks, items, serialization
  - Script: lines, speakers, serialization
  - Project: full round-trip and legacy v1.x conversion
"""
import json
import sys
import os
import unittest

# Ensure src/ is on the path so that absolute imports within models work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.media import (
    AudioMedia,
    ImageMedia,
    VideoMedia,
    AlignmentData,
    AlignmentSegment,
    WordTiming,
    MediaAsset,
    MediaRegistry,
)
from models.timeline import (
    Timeline,
    Track,
    TimelineItem,
    AudioItem,
    VideoItem,
    ImageItem,
    SubtitleItem,
    SubtitleWordSegment,
)
from models.script import Script, ScriptLine
from models.project import Project


# ---- Media ---------------------------------------------------------------

class TestWordTiming(unittest.TestCase):
    def test_round_trip(self):
        wt = WordTiming(text="hello", start=0.1, end=0.5, confidence=0.95)
        d = wt.to_dict()
        wt2 = WordTiming.from_dict(d)
        self.assertEqual(wt2.text, "hello")
        self.assertAlmostEqual(wt2.start, 0.1)
        self.assertAlmostEqual(wt2.confidence, 0.95)

    def test_default_confidence_omitted(self):
        wt = WordTiming(text="hi", start=0.0, end=0.2)
        d = wt.to_dict()
        self.assertNotIn("confidence", d)


class TestAlignmentData(unittest.TestCase):
    def test_round_trip(self):
        ad = AlignmentData(
            script_text="Once upon a time",
            method="forced_alignment",
            segments=[
                AlignmentSegment(
                    text="Once upon a time",
                    start=0.0,
                    end=2.0,
                    words=[
                        WordTiming(text="Once", start=0.0, end=0.5),
                        WordTiming(text="upon", start=0.5, end=0.8),
                    ],
                )
            ],
        )
        d = ad.to_dict()
        ad2 = AlignmentData.from_dict(d)
        self.assertEqual(ad2.method, "forced_alignment")
        self.assertEqual(len(ad2.segments), 1)
        self.assertEqual(len(ad2.segments[0].words), 2)


class TestAudioMedia(unittest.TestCase):
    def test_creation(self):
        m = AudioMedia(path="/audio.wav", speaker="Narrator", duration=5.0)
        self.assertEqual(m.media_type, "audio")
        self.assertTrue(len(m.uuid) > 0)

    def test_round_trip(self):
        m = AudioMedia(
            path="/audio.wav",
            speaker="Narrator",
            duration=5.0,
            sample_rate=44100,
            channels=2,
            alignment=AlignmentData(script_text="test"),
        )
        d = m.to_dict()
        m2 = AudioMedia.from_dict(d)
        self.assertEqual(m2.uuid, m.uuid)
        self.assertEqual(m2.speaker, "Narrator")
        self.assertIsNotNone(m2.alignment)
        self.assertEqual(m2.alignment.script_text, "test")


class TestImageMedia(unittest.TestCase):
    def test_round_trip(self):
        m = ImageMedia(path="/img.png", width=1920, height=1080, tags=["landscape"])
        d = m.to_dict()
        m2 = ImageMedia.from_dict(d)
        self.assertEqual(m2.width, 1920)
        self.assertEqual(m2.tags, ["landscape"])
        self.assertEqual(m2.media_type, "image")


class TestVideoMedia(unittest.TestCase):
    def test_round_trip(self):
        m = VideoMedia(path="/vid.mp4", duration=120.0, fps=30.0, width=1920, height=1080, has_audio=True)
        d = m.to_dict()
        m2 = VideoMedia.from_dict(d)
        self.assertEqual(m2.fps, 30.0)
        self.assertTrue(m2.has_audio)
        self.assertEqual(m2.media_type, "video")


class TestMediaAssetDispatch(unittest.TestCase):
    def test_dispatch_to_audio(self):
        d = {"media_type": "audio", "path": "/a.wav", "speaker": "X"}
        asset = MediaAsset.from_dict(d)
        self.assertIsInstance(asset, AudioMedia)

    def test_dispatch_to_image(self):
        d = {"media_type": "image", "path": "/i.png", "width": 100}
        asset = MediaAsset.from_dict(d)
        self.assertIsInstance(asset, ImageMedia)

    def test_dispatch_to_video(self):
        d = {"media_type": "video", "path": "/v.mp4", "fps": 24.0}
        asset = MediaAsset.from_dict(d)
        self.assertIsInstance(asset, VideoMedia)

    def test_dispatch_generic_fallback(self):
        d = {"media_type": "generic", "path": "/x"}
        asset = MediaAsset.from_dict(d)
        self.assertIsInstance(asset, MediaAsset)
        self.assertNotIsInstance(asset, AudioMedia)


class TestMediaRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = MediaRegistry()
        self.audio = AudioMedia(path="/a.wav", speaker="Narrator")
        self.image = ImageMedia(path="/i.png")
        self.reg.add(self.audio)
        self.reg.add(self.image)

    def test_len(self):
        self.assertEqual(len(self.reg), 2)

    def test_contains(self):
        self.assertIn(self.audio.uuid, self.reg)
        self.assertNotIn("nonexistent", self.reg)

    def test_get(self):
        self.assertIs(self.reg.get(self.audio.uuid), self.audio)
        self.assertIsNone(self.reg.get("nonexistent"))

    def test_find_by_path(self):
        self.assertIs(self.reg.find_by_path("/a.wav"), self.audio)
        self.assertIsNone(self.reg.find_by_path("/missing"))

    def test_find_by_speaker(self):
        self.assertIs(self.reg.find_by_speaker("Narrator"), self.audio)
        self.assertIsNone(self.reg.find_by_speaker("Unknown"))

    def test_typed_filters(self):
        self.assertEqual(len(self.reg.audio_assets()), 1)
        self.assertEqual(len(self.reg.image_assets()), 1)
        self.assertEqual(len(self.reg.video_assets()), 0)

    def test_remove(self):
        removed = self.reg.remove(self.audio.uuid)
        self.assertIs(removed, self.audio)
        self.assertEqual(len(self.reg), 1)
        self.assertIsNone(self.reg.remove("nonexistent"))

    def test_round_trip(self):
        data = self.reg.to_dict()
        reg2 = MediaRegistry.from_dict(data)
        self.assertEqual(len(reg2), 2)
        a = reg2.get(self.audio.uuid)
        self.assertIsInstance(a, AudioMedia)
        self.assertEqual(a.speaker, "Narrator")


# ---- Timeline ------------------------------------------------------------

class TestSubtitleWordSegment(unittest.TestCase):
    def test_round_trip(self):
        w = SubtitleWordSegment(text="hello", start=0.0, end=0.5)
        d = w.to_dict()
        w2 = SubtitleWordSegment.from_dict(d)
        self.assertEqual(w2.text, "hello")


class TestAudioItem(unittest.TestCase):
    def test_creation(self):
        item = AudioItem(timeline_start=1.0, timeline_duration=3.0, media_uuid="abc", volume=0.8)
        self.assertEqual(item.item_type, "audio")
        self.assertAlmostEqual(item.timeline_end, 4.0)

    def test_round_trip(self):
        item = AudioItem(
            timeline_start=1.0,
            timeline_duration=3.0,
            media_uuid="abc",
            source_in=0.5,
            source_out=3.5,
            volume=0.75,
            fade_in=0.1,
            fade_out=0.2,
            speaker="Narrator",
            segment_index=2,
        )
        d = item.to_dict()
        item2 = AudioItem.from_dict(d)
        self.assertEqual(item2.uuid, item.uuid)
        self.assertAlmostEqual(item2.volume, 0.75)
        self.assertEqual(item2.speaker, "Narrator")
        self.assertEqual(item2.segment_index, 2)


class TestVideoItem(unittest.TestCase):
    def test_round_trip(self):
        item = VideoItem(timeline_start=0.0, timeline_duration=5.0, media_uuid="v1", scale=1.5)
        d = item.to_dict()
        item2 = VideoItem.from_dict(d)
        self.assertEqual(item2.item_type, "video")
        self.assertAlmostEqual(item2.scale, 1.5)


class TestImageItem(unittest.TestCase):
    def test_round_trip_with_ken_burns(self):
        item = ImageItem(
            timeline_start=0.0,
            timeline_duration=4.0,
            media_uuid="img1",
            ken_burns={"start_rect": [0, 0, 1, 1], "end_rect": [0.1, 0.1, 0.8, 0.8]},
        )
        d = item.to_dict()
        item2 = ImageItem.from_dict(d)
        self.assertIsNotNone(item2.ken_burns)
        self.assertEqual(item2.ken_burns["start_rect"], [0, 0, 1, 1])


class TestSubtitleItem(unittest.TestCase):
    def test_round_trip(self):
        item = SubtitleItem(
            timeline_start=0.0,
            timeline_duration=3.0,
            text="Hello world",
            source_media_uuid="audio1",
            alignment_segment_index=0,
            words=[
                SubtitleWordSegment(text="Hello", start=0.0, end=0.3),
                SubtitleWordSegment(text="world", start=0.3, end=0.6),
            ],
        )
        d = item.to_dict()
        item2 = SubtitleItem.from_dict(d)
        self.assertEqual(item2.text, "Hello world")
        self.assertEqual(len(item2.words), 2)
        self.assertEqual(item2.words[0].text, "Hello")


class TestTrack(unittest.TestCase):
    def test_add_and_remove(self):
        track = Track(name="Audio", track_type="audio")
        item = AudioItem(timeline_start=0.0, timeline_duration=1.0)
        track.add_item(item)
        self.assertEqual(item.track_uuid, track.uuid)
        self.assertEqual(len(track.items), 1)

        removed = track.remove_item(item.uuid)
        self.assertIs(removed, item)
        self.assertEqual(len(track.items), 0)

    def test_get_item(self):
        track = Track(name="T", track_type="audio")
        item = AudioItem()
        track.add_item(item)
        self.assertIs(track.get_item(item.uuid), item)
        self.assertIsNone(track.get_item("nonexistent"))

    def test_round_trip(self):
        track = Track(name="Sub", track_type="subtitle")
        track.add_item(SubtitleItem(text="hi"))
        d = track.to_dict()
        track2 = Track.from_dict(d)
        self.assertEqual(track2.name, "Sub")
        self.assertEqual(len(track2.items), 1)
        self.assertIsInstance(track2.items[0], SubtitleItem)


class TestTimeline(unittest.TestCase):
    def test_duration(self):
        tl = Timeline()
        track = Track(name="A", track_type="audio")
        track.add_item(AudioItem(timeline_start=0.0, timeline_duration=5.0))
        track.add_item(AudioItem(timeline_start=5.0, timeline_duration=3.0))
        tl.add_track(track)
        self.assertAlmostEqual(tl.duration, 8.0)

    def test_empty_duration(self):
        tl = Timeline()
        self.assertAlmostEqual(tl.duration, 0.0)

    def test_get_track_by_type(self):
        tl = Timeline()
        audio_track = Track(name="Audio", track_type="audio")
        sub_track = Track(name="Sub", track_type="subtitle")
        tl.add_track(audio_track)
        tl.add_track(sub_track)
        self.assertIs(tl.get_track_by_type("audio"), audio_track)
        self.assertIs(tl.get_track_by_type("subtitle"), sub_track)
        self.assertIsNone(tl.get_track_by_type("video"))

    def test_all_items(self):
        tl = Timeline()
        t1 = Track(name="A", track_type="audio")
        t1.add_item(AudioItem())
        t2 = Track(name="S", track_type="subtitle")
        t2.add_item(SubtitleItem())
        t2.add_item(SubtitleItem())
        tl.add_track(t1)
        tl.add_track(t2)
        self.assertEqual(len(tl.all_items()), 3)

    def test_round_trip(self):
        tl = Timeline(name="My TL")
        track = Track(name="Audio", track_type="audio")
        track.add_item(AudioItem(timeline_start=0.0, timeline_duration=2.0, speaker="A"))
        tl.add_track(track)
        d = tl.to_dict()
        tl2 = Timeline.from_dict(d)
        self.assertEqual(tl2.name, "My TL")
        self.assertEqual(len(tl2.tracks), 1)
        self.assertIsInstance(tl2.tracks[0].items[0], AudioItem)


class TestTimelineItemDispatch(unittest.TestCase):
    def test_dispatch(self):
        for item_type, expected_cls in [
            ("audio", AudioItem),
            ("video", VideoItem),
            ("image", ImageItem),
            ("subtitle", SubtitleItem),
        ]:
            d = {"item_type": item_type, "timeline_start": 0.0, "timeline_duration": 1.0}
            item = TimelineItem.from_dict(d)
            self.assertIsInstance(item, expected_cls, f"Failed for {item_type}")


# ---- Script --------------------------------------------------------------

class TestScriptLine(unittest.TestCase):
    def test_round_trip(self):
        line = ScriptLine(speaker="Narrator", text="Hello", media_uuid="abc", image_hint="forest")
        d = line.to_dict()
        self.assertIn("image_hint", d)
        line2 = ScriptLine.from_dict(d)
        self.assertEqual(line2.speaker, "Narrator")
        self.assertEqual(line2.image_hint, "forest")

    def test_optional_fields_omitted(self):
        line = ScriptLine(speaker="A", text="B")
        d = line.to_dict()
        self.assertNotIn("media_uuid", d)
        self.assertNotIn("image_hint", d)


class TestScript(unittest.TestCase):
    def test_speakers(self):
        s = Script(lines=[
            ScriptLine(speaker="A", text="1"),
            ScriptLine(speaker="B", text="2"),
            ScriptLine(speaker="A", text="3"),
        ])
        self.assertEqual(s.speakers(), ["A", "B"])

    def test_lines_for_speaker(self):
        s = Script(lines=[
            ScriptLine(speaker="A", text="1"),
            ScriptLine(speaker="B", text="2"),
            ScriptLine(speaker="A", text="3"),
        ])
        self.assertEqual(len(s.lines_for_speaker("A")), 2)

    def test_round_trip(self):
        s = Script(lines=[ScriptLine(speaker="X", text="Y")])
        d = s.to_dict()
        s2 = Script.from_dict(d)
        self.assertEqual(len(s2.lines), 1)
        self.assertEqual(s2.lines[0].speaker, "X")


# ---- Project -------------------------------------------------------------

class TestProject(unittest.TestCase):
    def _make_project(self):
        p = Project(name="Test")
        audio = AudioMedia(path="/a.wav", speaker="Narrator", duration=5.0)
        image = ImageMedia(path="/i.png")
        p.media_registry.add(audio)
        p.media_registry.add(image)

        track = Track(name="Audio", track_type="audio")
        track.add_item(AudioItem(timeline_start=0.0, timeline_duration=5.0, media_uuid=audio.uuid))
        p.timeline.add_track(track)

        p.script.lines.append(ScriptLine(speaker="Narrator", text="Hello"))
        p.settings = {"fps": 30, "render_width": 1920}
        return p

    def test_round_trip(self):
        p = self._make_project()
        d = p.to_dict()
        self.assertEqual(d["version"], "2.0")

        p2 = Project.from_dict(d)
        self.assertEqual(p2.name, "Test")
        self.assertEqual(len(p2.media_registry), 2)
        self.assertEqual(len(p2.timeline.tracks), 1)
        self.assertEqual(len(p2.script.lines), 1)
        self.assertEqual(p2.settings["fps"], 30)

    def test_json_round_trip(self):
        p = self._make_project()
        json_str = json.dumps(p.to_dict(), ensure_ascii=False, indent=2)
        p2 = Project.from_dict(json.loads(json_str))
        self.assertEqual(p2.name, "Test")
        self.assertEqual(len(p2.media_registry), 2)


class TestLegacyConversion(unittest.TestCase):
    def _legacy_data(self):
        return {
            "version": "1.1",
            "saved_at": "2024-01-01T00:00:00",
            "script_path": "/test/script.txt",
            "script_content": "- Narrator: Once upon a time\n- Rabbit: I am a rabbit",
            "image_folder": "/test/images",
            "speaker_audio_map": {
                "Narrator": "/test/narrator.mp3",
                "Rabbit": "/test/rabbit.mp3",
            },
            "clips": [
                {
                    "id": "audio_0",
                    "name": "",
                    "start": 0.0,
                    "duration": 3.5,
                    "track": 0,
                    "color": "#FF5733",
                    "clip_type": "audio",
                    "offset": 0.5,
                    "segment_index": 0,
                    "speaker": "Narrator",
                    "volume": 0.9,
                },
                {
                    "id": "sub_0",
                    "name": "Once upon a time",
                    "start": 0.0,
                    "duration": 3.5,
                    "track": 1,
                    "color": "#FFFF00",
                    "clip_type": "subtitle",
                    "speaker": "Narrator",
                    "segment_index": 0,
                    "words": [
                        {"text": "Once", "start": 0.0, "end": 0.3},
                        {"text": "upon", "start": 0.3, "end": 0.6},
                    ],
                },
                {
                    "id": "img_0",
                    "name": "",
                    "start": 0.0,
                    "duration": 3.5,
                    "track": 2,
                    "color": "#00FF00",
                    "clip_type": "image",
                    "image_path": "/test/images/scene1.png",
                },
            ],
            "settings": {"whisper_model": "medium", "render_width": 1920},
        }

    def test_media_registry_built(self):
        p = Project.from_dict(self._legacy_data())
        # 2 audio (Narrator, Rabbit) + 1 image
        self.assertEqual(len(p.media_registry), 3)
        self.assertEqual(len(p.media_registry.audio_assets()), 2)
        self.assertEqual(len(p.media_registry.image_assets()), 1)

    def test_audio_media_speaker(self):
        p = Project.from_dict(self._legacy_data())
        narrator = p.media_registry.find_by_speaker("Narrator")
        self.assertIsNotNone(narrator)
        self.assertEqual(narrator.path, "/test/narrator.mp3")

    def test_timeline_tracks_created(self):
        p = Project.from_dict(self._legacy_data())
        self.assertEqual(len(p.timeline.tracks), 3)
        self.assertEqual(p.timeline.tracks[0].track_type, "audio")
        self.assertEqual(p.timeline.tracks[1].track_type, "subtitle")

    def test_audio_item_converted(self):
        p = Project.from_dict(self._legacy_data())
        audio_track = p.timeline.tracks[0]
        self.assertEqual(len(audio_track.items), 1)
        item = audio_track.items[0]
        self.assertIsInstance(item, AudioItem)
        self.assertAlmostEqual(item.source_in, 0.5)
        self.assertAlmostEqual(item.source_out, 4.0)  # 0.5 + 3.5
        self.assertAlmostEqual(item.volume, 0.9)

    def test_subtitle_item_converted(self):
        p = Project.from_dict(self._legacy_data())
        sub_track = p.timeline.tracks[1]
        self.assertEqual(len(sub_track.items), 1)
        item = sub_track.items[0]
        self.assertIsInstance(item, SubtitleItem)
        self.assertEqual(item.text, "Once upon a time")
        self.assertEqual(len(item.words), 2)

    def test_image_item_converted(self):
        p = Project.from_dict(self._legacy_data())
        img_track = p.timeline.tracks[2]
        self.assertEqual(len(img_track.items), 1)
        item = img_track.items[0]
        self.assertIsInstance(item, ImageItem)
        self.assertTrue(len(item.media_uuid) > 0)

    def test_script_parsed(self):
        p = Project.from_dict(self._legacy_data())
        self.assertEqual(len(p.script.lines), 2)
        self.assertEqual(p.script.speakers(), ["Narrator", "Rabbit"])

    def test_settings_preserved(self):
        p = Project.from_dict(self._legacy_data())
        self.assertEqual(p.settings["whisper_model"], "medium")

    def test_v2_re_serialization(self):
        """Legacy → v2 → dict → Project → verify."""
        p = Project.from_dict(self._legacy_data())
        d = p.to_dict()
        self.assertEqual(d["version"], "2.0")
        p2 = Project.from_dict(d)
        self.assertEqual(len(p2.media_registry), 3)
        self.assertEqual(len(p2.timeline.tracks), 3)


# ---- UUID uniqueness -----------------------------------------------------

class TestUUIDUniqueness(unittest.TestCase):
    def test_media_uuid_unique(self):
        a1 = AudioMedia()
        a2 = AudioMedia()
        self.assertNotEqual(a1.uuid, a2.uuid)

    def test_item_uuid_unique(self):
        i1 = AudioItem()
        i2 = AudioItem()
        self.assertNotEqual(i1.uuid, i2.uuid)

    def test_track_uuid_unique(self):
        t1 = Track()
        t2 = Track()
        self.assertNotEqual(t1.uuid, t2.uuid)


if __name__ == "__main__":
    unittest.main()
