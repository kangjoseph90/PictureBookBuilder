"""
Audio Mixer - Real-time audio playback from timeline clips

Plays audio clips at their scheduled timeline positions without
pre-merging into a single file. This allows instant feedback when
clips are modified.
"""
from typing import Optional, Callable
from dataclasses import dataclass
from pathlib import Path
import math
import tempfile
import os
import subprocess
import shutil

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


@dataclass
class ScheduledClip:
    """Audio clip scheduled for playback at a specific timeline position"""
    clip_id: str
    speaker: str
    timeline_start: float  # When to start playing (seconds)
    timeline_end: float    # When clip ends on timeline (seconds)
    source_offset: float   # Where to start in source audio (seconds)
    source_path: str       # Path to source audio file
    duration: float        # Duration to play (seconds)
    volume: float = 1.0    # Volume multiplier
    
    @property
    def timeline_duration(self) -> float:
        return self.timeline_end - self.timeline_start


class AudioMixer(QObject):
    """
    Real-time audio mixer that plays clips at their scheduled timeline positions.
    
    Instead of merging all clips into one audio file, this mixer:
    1. Maintains a list of scheduled clips with their timeline positions
    2. Uses a timer to track current playback position
    3. Starts/stops individual audio players as needed based on position
    
    Signals:
        position_changed: Emitted with current position in milliseconds
        playback_state_changed: Emitted with 'playing', 'paused', or 'stopped'
        duration_changed: Emitted with total duration in seconds
    """
    
    position_changed = pyqtSignal(int)  # position_ms
    playback_state_changed = pyqtSignal(str)  # 'playing', 'paused', 'stopped'
    duration_changed = pyqtSignal(float)  # duration_sec
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Scheduled clips
        self.clips: list[ScheduledClip] = []
        self.speaker_audio_paths: dict[str, str] = {}  # speaker -> audio file path
        
        # Playback state
        self._position: float = 0.0  # Current position in seconds
        self._duration: float = 0.0  # Total timeline duration
        self._playing: bool = False
        self._playback_rate: float = 1.0
        self._volume: float = 1.0  # Volume level (0.0 to 1.0)
        
        # Active players for each clip currently playing
        self._active_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        
        # Cached players per speaker (speaker -> (player, audio_output, seek_correction))
        # These are pre-loaded and ready to seek/play instantly
        self._player_cache: dict[str, tuple[QMediaPlayer, QAudioOutput, float]] = {}

        # Global boosted files cache
        # Key: speaker -> temp_file_path (2.0x volume, 48000Hz)
        self._speaker_boosted_files: dict[str, str] = {}
        
        # Minimum duration enforced (e.g. by other tracks)
        self._min_duration: float = 0.0
        
        # Timer for position updates (16ms = ~60fps)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._update_position)
        
        # Track last timer tick for accurate timing
        self._last_tick_time: Optional[float] = None
        
    def set_clips(self, clips: list[ScheduledClip]):
        """Set the clips to be played.
        
        Args:
            clips: List of ScheduledClip objects
        """
        # Stop any current playback but preserve position during update
        # to avoid redundant 'jump to 0' signals.
        self.stop(reset_position=False)
        
        self.clips = clips
        self._update_duration()
        
    def set_speaker_audio_paths(self, paths: dict[str, str]):
        """Set the audio file paths for each speaker.
        
        Args:
            paths: Dict mapping speaker name to audio file path
        """
        # Clear cache if paths changed (invalidate old players)
        if paths != self.speaker_audio_paths:
            self._clear_player_cache()
        self.speaker_audio_paths = paths
        
    def _update_duration(self):
        """Calculate total duration from clips and min_duration"""
        clips_duration = 0.0
        if self.clips:
            clips_duration = max(c.timeline_end for c in self.clips)
            
        # Enforce minimum duration (0.1s) to prevent edge cases
        self._duration = max(clips_duration, self._min_duration, 0.1)
        self.duration_changed.emit(self._duration)
        
    def set_duration(self, duration: float):
        """
        Set minimum duration (for when other track types extend beyond audio)
        
        Args:
            duration: Duration in seconds
        """
        self._min_duration = duration
        self._update_duration()
        
    @property
    def position(self) -> float:
        """Current position in seconds"""
        return self._position
    
    @property
    def position_ms(self) -> int:
        """Current position in milliseconds"""
        return int(self._position * 1000)
    
    @property
    def duration(self) -> float:
        """Total duration in seconds"""
        return self._duration
    
    @property
    def is_playing(self) -> bool:
        """Whether playback is active"""
        return self._playing
    
    def play(self):
        """Start or resume playback"""
        if self._playing:
            return
            
        self._playing = True
        self._last_tick_time = None
        self._timer.start()
        
        # Resume all active players
        for player, _ in self._active_players.values():
            if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                player.play()
        
        # Start any clips that should be playing at current position
        self._sync_active_clips()
        
        self.playback_state_changed.emit('playing')
        
    def pause(self):
        """Pause playback"""
        if not self._playing:
            return
            
        self._playing = False
        self._timer.stop()
        
        # Pause all active players
        for clip_id, (player, _) in self._active_players.items():
            player.pause()
            
        self.playback_state_changed.emit('paused')
        
    def stop(self, reset_position: bool = True):
        """Stop playback and optionally reset position"""
        self._playing = False
        self._timer.stop()
        
        # Stop and cleanup all active players
        self._stop_all_players()
        
        if reset_position:
            self._position = 0.0
            self.position_changed.emit(0)
        self.playback_state_changed.emit('stopped')
        
    def seek(self, position_sec: float):
        """Seek to a specific position.
        
        Args:
            position_sec: Position in seconds
        """
        self._position = max(0.0, min(position_sec, self._duration))
        
        # Reset timer to prevent drift accumulation
        self._last_tick_time = None
        
        # Stop all current players and resync
        self._stop_all_players()
        
        if self._playing:
            self._sync_active_clips()
            
        self.position_changed.emit(self.position_ms)
        
    def set_position(self, position_ms: int):
        """Set position in milliseconds (alias for seek)"""
        self.seek(position_ms / 1000.0)
        
    def set_playback_rate(self, rate: float):
        """Set playback rate.
        
        Args:
            rate: Playback rate (1.0 = normal)
        """
        self._playback_rate = rate
        
        # Update rate on all active players
        for clip_id, (player, _) in self._active_players.items():
            player.setPlaybackRate(rate)
            
    def set_volume(self, volume: float):
        """Set volume level.
        
        Args:
            volume: Volume level (0.0 to 1.0)
        """
        self._volume = max(0.0, min(1.0, volume))
        
        # Update active clips
        for clip in self.clips:
            if clip.clip_id in self._active_players:
                player, audio_output = self._active_players[clip.clip_id]
                effective_vol = self._volume * (clip.volume / 2.0)
                audio_output.setVolume(effective_vol)

    def _update_position(self):
        """Timer callback to update position and manage clips"""
        import time
        
        current_time = time.perf_counter()
        
        # Use timer-based tracking for smooth movement
        if self._last_tick_time is not None:
            elapsed = (current_time - self._last_tick_time) * self._playback_rate
            self._position += elapsed
            
        self._last_tick_time = current_time
        
        # Check if we've reached the end (with safety margin for edge cases)
        if self._position >= self._duration:
            self._position = self._duration
            self.stop()
            return
        
        # Safety: Stop if position significantly exceeds duration (edge case protection)
        if self._position > self._duration + 0.5:
            self._position = self._duration
            self.stop()
            return
            
        # Sync active clips
        self._sync_active_clips()
        
        # Emit position
        self.position_changed.emit(self.position_ms)
        
    def _sync_active_clips(self):
        """Start/stop clips based on current position"""
        current_pos = self._position
        
        # Find clips that should be playing now
        should_be_active: set[str] = set()
        
        for clip in self.clips:
            if clip.timeline_start <= current_pos < clip.timeline_end:
                should_be_active.add(clip.clip_id)
                
                # Start this clip if not already active
                if clip.clip_id not in self._active_players:
                    self._start_clip(clip, current_pos)
                    
        # Stop clips that should no longer be playing
        to_stop = []
        for clip_id in self._active_players:
            if clip_id not in should_be_active:
                to_stop.append(clip_id)
                
        for clip_id in to_stop:
            self._stop_clip(clip_id)
            
    def _get_or_create_boosted_speaker_file(self, speaker: str, original_path: str) -> Optional[str]:
        """Get or create a 2.0x volume boosted WAV file for a speaker."""
        if speaker in self._speaker_boosted_files:
            path = self._speaker_boosted_files[speaker]
            if os.path.exists(path):
                return path

        try:
            if not original_path or not os.path.exists(original_path):
                return None

            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            cmd = [
                'ffmpeg',
                '-i', original_path,
                '-filter:a', 'volume=2.0',
                '-ar', '48000',
                '-y',
                temp_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._speaker_boosted_files[speaker] = temp_path
            return temp_path
        except Exception as e:
            print(f"Failed to create boosted speaker file: {e}")
            return None

    def _get_or_create_cached_player(self, speaker: str) -> Optional[tuple[QMediaPlayer, QAudioOutput, float]]:
        """Get a cached player for a speaker, or create and cache a new one.
        
        Args:
            speaker: Speaker name
            
        Returns:
            Tuple of (player, audio_output, seek_correction) or None if no audio path
        """
        # Return cached player if exists
        if speaker in self._player_cache:
            return self._player_cache[speaker]
            
        # Get audio path for this speaker
        audio_path = self.speaker_audio_paths.get(speaker)
        if not audio_path or not Path(audio_path).exists():
            return None

        # Create 2.0x boosted file to handle volume amplification
        boosted_path = self._get_or_create_boosted_speaker_file(speaker, audio_path)
        if not boosted_path:
            return None
            
        # Create player and audio output
        audio_output = QAudioOutput()
        
        player = QMediaPlayer()
        player.setAudioOutput(audio_output)
        
        # Set source to boosted file (which is also forced to 48kHz, so seek correction is 1.0)
        player.setSource(QUrl.fromLocalFile(boosted_path))
        
        seek_correction = 1.0
        
        # Cache and return
        self._player_cache[speaker] = (player, audio_output, seek_correction)
        return self._player_cache[speaker]

    def _start_clip(self, clip: ScheduledClip, current_position: float):
        """Start playing a clip at the appropriate offset.
        
        Args:
            clip: The clip to start
            current_position: Current timeline position in seconds
        """
        # Fallback to standard logic for all volumes (shared players using 2.0x boosted source)
        cached = self._get_or_create_cached_player(clip.speaker)
        if cached is None:
            return
            
        player, audio_output, seek_correction = cached
        
        # Apply volume (source is 2.0x, so divide by 2)
        effective_vol = self._volume * (clip.volume / 2.0)
        audio_output.setVolume(effective_vol)

        # Calculate where in the source audio to start
        time_into_clip = current_position - clip.timeline_start
        source_position_ms = int((clip.source_offset + time_into_clip) * 1000)
        
        # Store reference to track this clip is active
        self._active_players[clip.clip_id] = (player, audio_output)
        
        # Store reference for position sync (will update after seek)
        corrected_pos = int(source_position_ms * seek_correction)
        
        # Check if player is already loaded
        if player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia:
            # Player already loaded, seek and play immediately
            player.setPlaybackRate(self._playback_rate)
            player.setPosition(corrected_pos)
            if self._playing:
                player.play()
        else:
            # Wait for media to load, then seek and play
            def on_media_status_changed(status):
                if status == QMediaPlayer.MediaStatus.LoadedMedia:
                    player.setPlaybackRate(self._playback_rate)
                    if self._playing:
                        # Recalculate position to account for loading time
                        current_time_into_clip = self._position - clip.timeline_start
                        raw_source_pos = (clip.source_offset + current_time_into_clip) * 1000
                        corrected_source_pos = int(raw_source_pos * seek_correction)
                        player.setPosition(corrected_source_pos)
                        player.play()
                    else:
                        initial_pos = int(source_position_ms * seek_correction)
                        player.setPosition(initial_pos)
                    # Disconnect after first load
                    try:
                        player.mediaStatusChanged.disconnect(on_media_status_changed)
                    except Exception:
                        pass
                        
            player.mediaStatusChanged.connect(on_media_status_changed)
        
    def _stop_clip(self, clip_id: str):
        """Stop and cleanup a specific clip player.
        
        Args:
            clip_id: ID of the clip to stop
        """
        if clip_id in self._active_players:
            player, audio_output = self._active_players.pop(clip_id)
            player.stop()
            
    def _stop_all_players(self):
        """Stop and cleanup all active players"""
        for clip_id in list(self._active_players.keys()):
            self._stop_clip(clip_id)
            
    def _clear_player_cache(self):
        """Clear and cleanup all cached players"""
        # Clear shared speaker players
        for speaker, (player, audio_output, _) in self._player_cache.items():
            player.stop()
            player.setSource(QUrl())
            player.deleteLater()
            audio_output.deleteLater()
        self._player_cache.clear()

        # Remove temp files
        for path in self._speaker_boosted_files.values():
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        self._speaker_boosted_files.clear()
        
    def cleanup(self):
        """Cleanup all resources"""
        self.stop()
        self._timer.stop()
        self._clear_player_cache()
        
    def update_clip(self, clip: ScheduledClip):
        """Update a single clip's data (for real-time editing).
        
        Args:
            clip: Updated clip data
        """
        # Find and update the clip
        for i, existing in enumerate(self.clips):
            if existing.clip_id == clip.clip_id:
                self.clips[i] = clip
                break
        else:
            # Clip not found, add it
            self.clips.append(clip)
            
        # If this clip is active (playing or paused), update it
        if clip.clip_id in self._active_players:
            # Update volume immediately
            player, audio_output = self._active_players[clip.clip_id]
            effective_vol = self._volume * (clip.volume / 2.0)
            audio_output.setVolume(effective_vol)

        self._update_duration()
        
    def remove_clip(self, clip_id: str):
        """Remove a clip.
        
        Args:
            clip_id: ID of the clip to remove
        """
        # Stop if playing
        if clip_id in self._active_players:
            self._stop_clip(clip_id)

        # Remove from list
        self.clips = [c for c in self.clips if c.clip_id != clip_id]
        self._update_duration()
