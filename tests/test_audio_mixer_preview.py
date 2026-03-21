"""
Tests for the simplified preview audio volume strategy.

Strategy summary
----------------
Clips with volume > 1.0 are pre-rendered to a single "max-preview" file at
MAX_PREVIEW_VOLUME (2.0) amplitude.  Playback gain is then scaled at runtime:

    effectiveGain = master_volume * clip_volume / MAX_PREVIEW_VOLUME

This means:
  - A single file is shared for all volume > 1.0 requests on the same segment.
  - No per-volume boosted file is generated.
  - Clips with volume <= 1.0 continue to use the shared speaker player path.
"""

import os
import sys
import types
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Stub out PyQt6 before importing the module under test so that these tests
# can run in environments without a Qt installation.
# ---------------------------------------------------------------------------
def _make_qt_stubs():
    """Create minimal PyQt6 stubs so audio_mixer can be imported without Qt."""
    pyqt6 = types.ModuleType("PyQt6")
    pyqt6_core = types.ModuleType("PyQt6.QtCore")
    pyqt6_multimedia = types.ModuleType("PyQt6.QtMultimedia")

    # QObject stub
    class QObject:
        def __init__(self, parent=None):
            pass

    # QTimer stub
    class QTimer:
        def __init__(self, parent=None):
            self.timeout = MagicMock()
        def setInterval(self, ms): pass
        def stop(self): pass
        def start(self): pass

    # Signal stub (descriptor-like)
    class pyqtSignal:
        def __init__(self, *args, **kwargs):
            self._name = None
        def __set_name__(self, owner, name):
            self._name = name
        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            attr = f"_signal_{self._name}"
            if not hasattr(obj, attr):
                mock = MagicMock()
                mock.emit = MagicMock()
                setattr(obj, attr, mock)
            return getattr(obj, attr)

    # QMediaPlayer stub
    class MediaStatus:
        LoadedMedia = "LoadedMedia"
        NoMedia = "NoMedia"

    _MediaStatus = MediaStatus

    class QMediaPlayer:
        MediaStatus = _MediaStatus
        def __init__(self, parent=None):
            self._source = MagicMock()
            self._source.toLocalFile.return_value = ""
            self.mediaStatusChanged = MagicMock()
            self.mediaStatusChanged.connect = MagicMock()
            self.mediaStatusChanged.disconnect = MagicMock()
        def setAudioOutput(self, ao): pass
        def setSource(self, url):
            self._source = url
        def source(self):
            return self._source
        def mediaStatus(self):
            return MediaStatus.LoadedMedia
        def setPlaybackRate(self, r): pass
        def setPosition(self, ms): pass
        def play(self): pass
        def stop(self): pass
        def deleteLater(self): pass

    # QAudioOutput stub
    class QAudioOutput:
        def __init__(self):
            self._volume = 1.0
        def setVolume(self, v):
            self._volume = v
        def deleteLater(self): pass

    # QUrl stub
    class QUrl:
        def __init__(self, s=""):
            self._s = s
        @staticmethod
        def fromLocalFile(path):
            url = QUrl(path)
            url.toLocalFile = lambda: path
            return url
        def toLocalFile(self):
            return self._s

    pyqt6_core.QObject = QObject
    pyqt6_core.QTimer = QTimer
    pyqt6_core.pyqtSignal = pyqtSignal
    pyqt6_core.QUrl = QUrl
    pyqt6_multimedia.QMediaPlayer = QMediaPlayer
    pyqt6_multimedia.QAudioOutput = QAudioOutput

    pyqt6.QtCore = pyqt6_core
    pyqt6.QtMultimedia = pyqt6_multimedia
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = pyqt6_core
    sys.modules["PyQt6.QtMultimedia"] = pyqt6_multimedia


_make_qt_stubs()

# Now we can safely import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ui.audio_mixer import AudioMixer, ScheduledClip, MAX_PREVIEW_VOLUME  # noqa: E402


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_clip(clip_id="c1", volume=1.5, source_path="/fake/audio.wav",
               source_offset=0.0, duration=5.0,
               timeline_start=0.0, timeline_end=5.0, speaker="alice"):
    return ScheduledClip(
        clip_id=clip_id,
        speaker=speaker,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        source_offset=source_offset,
        source_path=source_path,
        duration=duration,
        volume=volume,
    )


class TestMaxPreviewVolumeConstant(unittest.TestCase):
    """MAX_PREVIEW_VOLUME must be 2.0."""

    def test_constant_value(self):
        self.assertEqual(MAX_PREVIEW_VOLUME, 2.0)


class TestPrepareBoostAudioCaching(unittest.TestCase):
    """
    _prepare_boosted_audio must share a single file for the same segment
    regardless of the requested clip volume.
    """

    def setUp(self):
        self.mixer = AudioMixer()

    def _run_prepare_with_fake_ffmpeg(self, clip, fake_path):
        """
        Patch os.path.exists (for source), tempfile.mkstemp, and subprocess.run
        so that _prepare_boosted_audio 'succeeds' and returns fake_path.
        """
        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch("ui.audio_mixer.tempfile.mkstemp", return_value=(99, fake_path)), \
             patch("ui.audio_mixer.os.close"), \
             patch("ui.audio_mixer.subprocess.run"):
            return self.mixer._prepare_boosted_audio(clip)

    def test_no_file_created_for_volume_lte_1(self):
        """Volume <= 1.0 must not generate a boosted file."""
        clip = _make_clip(volume=1.0)
        result = self.mixer._prepare_boosted_audio(clip)
        self.assertIsNone(result)

        clip2 = _make_clip(volume=0.5)
        result2 = self.mixer._prepare_boosted_audio(clip2)
        self.assertIsNone(result2)

    def test_different_volumes_same_segment_reuse_same_file(self):
        """
        Two clips with the same (source, offset, duration) but different volumes
        (both > 1.0) must return the SAME cached file path — no second encode.
        """
        clip_15 = _make_clip(volume=1.5)
        clip_18 = _make_clip(clip_id="c2", volume=1.8)  # same source/offset/dur

        fake_path = "/tmp/boost_shared.wav"

        # First call runs ffmpeg
        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch("ui.audio_mixer.tempfile.mkstemp", return_value=(99, fake_path)), \
             patch("ui.audio_mixer.os.close"), \
             patch("ui.audio_mixer.subprocess.run") as mock_run:
            result1 = self.mixer._prepare_boosted_audio(clip_15)
            self.assertEqual(mock_run.call_count, 1, "FFmpeg should run exactly once")

        # Second call (different volume, same segment) must return the cached path
        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch("ui.audio_mixer.subprocess.run") as mock_run2:
            result2 = self.mixer._prepare_boosted_audio(clip_18)
            mock_run2.assert_not_called()  # no second encode

        self.assertEqual(result1, fake_path)
        self.assertEqual(result2, fake_path)

    def test_ffmpeg_uses_max_preview_volume_not_clip_volume(self):
        """
        FFmpeg filter must use MAX_PREVIEW_VOLUME (2.0), not the clip's own volume.
        """
        clip = _make_clip(volume=1.3)
        fake_path = "/tmp/boost_max.wav"

        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch("ui.audio_mixer.tempfile.mkstemp", return_value=(99, fake_path)), \
             patch("ui.audio_mixer.os.close"), \
             patch("ui.audio_mixer.subprocess.run") as mock_run:
            self.mixer._prepare_boosted_audio(clip)

        called_cmd = mock_run.call_args[0][0]  # first positional arg is the list
        filter_idx = called_cmd.index("-filter:a") + 1
        filter_str = called_cmd[filter_idx]

        self.assertIn(f"volume={MAX_PREVIEW_VOLUME}", filter_str,
                      "FFmpeg filter must reference MAX_PREVIEW_VOLUME")
        self.assertNotIn(f"volume={clip.volume}", filter_str,
                         "FFmpeg filter must NOT reference clip.volume")

    def test_different_segments_produce_different_files(self):
        """
        Clips with different offsets must produce distinct cached files.
        """
        clip_a = _make_clip(source_offset=0.0)
        clip_b = _make_clip(clip_id="c2", source_offset=10.0)

        fake_path_a = "/tmp/boost_a.wav"
        fake_path_b = "/tmp/boost_b.wav"

        paths = [fake_path_a, fake_path_b]
        call_count = [0]

        def fake_mkstemp(suffix=""):
            p = paths[call_count[0]]
            call_count[0] += 1
            return (99, p)

        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch("ui.audio_mixer.tempfile.mkstemp", side_effect=fake_mkstemp), \
             patch("ui.audio_mixer.os.close"), \
             patch("ui.audio_mixer.subprocess.run"):
            r_a = self.mixer._prepare_boosted_audio(clip_a)
            r_b = self.mixer._prepare_boosted_audio(clip_b)

        self.assertNotEqual(r_a, r_b)


class TestAssetSelection(unittest.TestCase):
    """
    _start_clip must route to the correct player based on volume:
      - volume <= 1.0  → shared speaker player (no boosted file)
      - volume > 1.0   → boosted (max-preview) player
    """

    def setUp(self):
        self.mixer = AudioMixer()

    def test_normal_clip_does_not_call_prepare_boosted(self):
        """volume <= 1.0: _prepare_boosted_audio must never be called."""
        clip = _make_clip(volume=0.8)
        self.mixer.clips = [clip]

        with patch.object(self.mixer, "_prepare_boosted_audio",
                          wraps=self.mixer._prepare_boosted_audio) as spy, \
             patch.object(self.mixer, "_get_or_create_cached_player", return_value=None):
            self.mixer._start_clip(clip, current_position=0.0)

        spy.assert_not_called()

    def test_boosted_clip_calls_prepare_boosted(self):
        """volume > 1.0: _prepare_boosted_audio must be invoked."""
        clip = _make_clip(volume=1.7)
        self.mixer.clips = [clip]

        fake_path = "/tmp/boost.wav"

        with patch.object(self.mixer, "_prepare_boosted_audio",
                          return_value=fake_path) as spy, \
             patch.object(self.mixer, "_get_or_create_boosted_player",
                          return_value=(MagicMock(), MagicMock())):
            self.mixer._start_clip(clip, current_position=0.0)

        spy.assert_called_once_with(clip)


class TestRuntimeGainFormula(unittest.TestCase):
    """
    For volume > 1.0 the effective gain applied to the audio output must be:
        effectiveGain = master_volume * clip_volume / MAX_PREVIEW_VOLUME
    """

    def setUp(self):
        self.mixer = AudioMixer()
        self.mixer._volume = 1.0  # master volume

    def _start_boosted_clip(self, clip):
        """Helper: run _start_clip with mocked boosted player and capture gain."""
        fake_path = "/tmp/boost.wav"
        mock_player = MagicMock()
        mock_output = MagicMock()
        mock_player.mediaStatus.return_value = "LoadedMedia"

        from PyQt6.QtMultimedia import QMediaPlayer as _QMP
        mock_player.mediaStatus.return_value = _QMP.MediaStatus.LoadedMedia

        with patch.object(self.mixer, "_prepare_boosted_audio", return_value=fake_path), \
             patch.object(self.mixer, "_get_or_create_boosted_player",
                          return_value=(mock_player, mock_output)):
            self.mixer._start_clip(clip, current_position=0.0)

        return mock_output

    def test_gain_at_volume_2_master_1(self):
        """clip.volume=2.0, master=1.0 → gain=1.0."""
        clip = _make_clip(volume=2.0)
        output = self._start_boosted_clip(clip)
        expected = 1.0 * 2.0 / MAX_PREVIEW_VOLUME
        output.setVolume.assert_called_with(expected)

    def test_gain_at_volume_1_5_master_1(self):
        """clip.volume=1.5, master=1.0 → gain=0.75."""
        clip = _make_clip(volume=1.5)
        output = self._start_boosted_clip(clip)
        actual = output.setVolume.call_args[0][0]
        self.assertAlmostEqual(actual, 0.75, places=6)

    def test_gain_at_volume_1_8_master_0_5(self):
        """clip.volume=1.8, master=0.5 → gain=0.45."""
        self.mixer._volume = 0.5
        clip = _make_clip(volume=1.8)
        output = self._start_boosted_clip(clip)
        actual = output.setVolume.call_args[0][0]
        expected = 0.5 * 1.8 / MAX_PREVIEW_VOLUME
        self.assertAlmostEqual(actual, expected, places=6)

    def test_set_volume_updates_boosted_gain(self):
        """
        set_volume must update already-active boosted clips with the correct
        scaled gain.
        """
        clip = _make_clip(volume=1.6)
        self.mixer.clips = [clip]

        mock_output = MagicMock()
        mock_player = MagicMock()
        self.mixer._active_players[clip.clip_id] = (mock_player, mock_output)
        # Register as boosted so the branch is taken
        self.mixer._boosted_players[clip.clip_id] = (mock_player, mock_output)

        self.mixer.set_volume(0.8)

        expected = 0.8 * 1.6 / MAX_PREVIEW_VOLUME
        mock_output.setVolume.assert_called_with(expected)


class TestUpdateClipBoostedBoosted(unittest.TestCase):
    """
    update_clip for a boosted→boosted transition must update gain in-place
    when only volume changes (segment unchanged), and only restart when the
    segment (source/offset/duration) changes.
    """

    def setUp(self):
        self.mixer = AudioMixer()
        self.mixer._volume = 1.0
        self.mixer._playing = False

    def _register_active_boosted(self, clip, cached_path):
        mock_player = MagicMock()
        mock_player.source.return_value.toLocalFile.return_value = cached_path
        mock_output = MagicMock()
        self.mixer._active_players[clip.clip_id] = (mock_player, mock_output)
        self.mixer._boosted_players[clip.clip_id] = (mock_player, mock_output)
        self.mixer._boosted_files_cache[
            (clip.source_path, clip.source_offset, clip.duration)
        ] = cached_path
        return mock_player, mock_output

    def test_volume_only_change_updates_gain_no_restart(self):
        """
        Changing only volume must update setVolume in-place without restarting
        the player.
        """
        clip = _make_clip(volume=1.5)
        self.mixer.clips = [clip]

        fake_path = "/tmp/boost_shared.wav"
        mock_player, mock_output = self._register_active_boosted(clip, fake_path)

        # Simulate volume change to 1.8 (same segment)
        updated_clip = _make_clip(volume=1.8)
        self.mixer.clips = [updated_clip]

        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch.object(self.mixer, "_stop_clip") as mock_stop, \
             patch.object(self.mixer, "_start_clip") as mock_start:
            self.mixer.update_clip(updated_clip)

        mock_stop.assert_not_called()
        mock_start.assert_not_called()

        expected_gain = 1.0 * 1.8 / MAX_PREVIEW_VOLUME
        mock_output.setVolume.assert_called_with(expected_gain)

    def test_segment_change_triggers_restart(self):
        """
        Changing source_offset must invalidate the cached file and restart.
        """
        clip = _make_clip(volume=1.5, source_offset=0.0)
        self.mixer.clips = [clip]

        fake_path = "/tmp/boost_old.wav"
        mock_player, mock_output = self._register_active_boosted(clip, fake_path)

        # New clip has different offset → different cache key → no entry
        new_clip = _make_clip(volume=1.5, source_offset=5.0)
        self.mixer.clips = [new_clip]
        # Ensure new key is NOT in cache
        new_key = (new_clip.source_path, new_clip.source_offset, new_clip.duration)
        self.assertNotIn(new_key, self.mixer._boosted_files_cache)

        with patch("ui.audio_mixer.os.path.exists", return_value=True), \
             patch.object(self.mixer, "_stop_clip") as mock_stop, \
             patch.object(self.mixer, "_start_clip") as mock_start:
            self.mixer.update_clip(new_clip)

        mock_stop.assert_called_once_with(new_clip.clip_id)


if __name__ == "__main__":
    unittest.main()
