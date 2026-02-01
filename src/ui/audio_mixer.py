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

        # Cache for boosted audio temp files
        # Key: (source_path, offset, duration, volume) -> temp_file_path
        self._boosted_files_cache: dict[tuple, str] = {}

        # Map clip_id -> temp_file_path to manage ownership and cleanup
        self._clip_boosted_file: dict[str, str] = {}

        # Dedicated players for boosted clips (since they can't share the speaker player)
        # Key: clip_id -> (player, audio_output)
        self._boosted_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        
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
        
        # Update volume on all cached players (global players)
        for speaker, (player, audio_output, _) in self._player_cache.items():
            audio_output.setVolume(self._volume)
            
        # Update active clips
        for clip in self.clips:
            if clip.clip_id in self._active_players:
                player, audio_output = self._active_players[clip.clip_id]

                # Logic differs for boosted vs normal clips
                if clip.volume > 1.0:
                    # For boosted clips, the boost is baked into the file.
                    # So we just set master volume.
                    audio_output.setVolume(self._volume)
                else:
                    # For normal clips, we multiply
                    effective_vol = self._volume * clip.volume
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
            
        # Create player and audio output
        audio_output = QAudioOutput()
        audio_output.setVolume(self._volume)
        
        player = QMediaPlayer()
        player.setAudioOutput(audio_output)
        
        # Set source (this triggers the FFmpeg log - but only once per speaker!)
        player.setSource(QUrl.fromLocalFile(audio_path))
        
        # Determine seek correction for non-standard sample rates (Qt/FFmpeg bug workaround)
        seek_correction = 1.0
        try:
            import wave
            import contextlib
            with contextlib.closing(wave.open(audio_path, 'rb')) as wf:
                framerate = wf.getframerate()
                # If sample rate is significantly lower than standard (44.1/48k),
                # the backend likely calculates seek offsets using 48k logic.
                if framerate < 44100:
                    seek_correction = framerate / 48000.0
        except Exception:
            pass
        
        # Cache and return
        self._player_cache[speaker] = (player, audio_output, seek_correction)
        return self._player_cache[speaker]

    def _prepare_boosted_audio(self, clip: ScheduledClip) -> Optional[str]:
        """Prepare a temporary boosted audio file for a clip using FFmpeg.

        Args:
            clip: Clip to boost

        Returns:
            Path to temporary boosted wav file, or None on failure
        """
        if clip.volume <= 1.0:
            return None

        # Key includes offset and duration because we extract just the segment
        key = (clip.source_path, clip.source_offset, clip.duration, clip.volume)

        if key in self._boosted_files_cache:
            path = self._boosted_files_cache[key]
            if os.path.exists(path):
                # Update ownership if needed, or just return
                # If this clip already owns a file that is different, we handle that in caller
                return path

        try:
            if not clip.source_path or not os.path.exists(clip.source_path):
                return None

            # Create temp file
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            # Use FFmpeg to extract segment and apply volume
            # -ss: start time
            # -t: duration
            # -i: input file
            # -filter:a "volume=X"
            # -y: overwrite
            cmd = [
                'ffmpeg',
                '-ss', str(clip.source_offset),
                '-t', str(clip.duration),
                '-i', clip.source_path,
                '-filter:a', f'volume={clip.volume}',
                '-y',
                temp_path
            ]

            # Run ffmpeg (suppress output)
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Update caches
            self._boosted_files_cache[key] = temp_path

            # Cleanup old file for this clip if it exists and is different
            if clip.clip_id in self._clip_boosted_file:
                old_path = self._clip_boosted_file[clip.clip_id]
                if old_path != temp_path:
                    # Only delete if no other clip uses it (simple ref counting impossible with tuple keys)
                    # For simplicity in this session: we trust the cache key logic.
                    # We only delete if it's NOT in the cache anymore (replaced)?
                    # Actually, if the key changed (volume changed), the old key is still in cache.
                    # We should remove old key from cache to prevent unlimited growth?
                    pass

            self._clip_boosted_file[clip.clip_id] = temp_path

            return temp_path

        except Exception as e:
            print(f"Failed to boost audio with ffmpeg: {e}")
            return None

    def _get_or_create_boosted_player(self, clip_id: str, boosted_path: str) -> tuple[QMediaPlayer, QAudioOutput]:
        """Get or create a dedicated player for a boosted clip."""
        if clip_id in self._boosted_players:
            player, output = self._boosted_players[clip_id]
            # Verify source is correct (might have changed volume/file)
            current_source = player.source().toLocalFile()
            if current_source != boosted_path:
                player.setSource(QUrl.fromLocalFile(boosted_path))
            return player, output

        # Create new
        audio_output = QAudioOutput()
        audio_output.setVolume(self._volume) # Master volume only

        player = QMediaPlayer()
        player.setAudioOutput(audio_output)
        player.setSource(QUrl.fromLocalFile(boosted_path))

        self._boosted_players[clip_id] = (player, audio_output)
        return player, audio_output
    
    def _start_clip(self, clip: ScheduledClip, current_position: float):
        """Start playing a clip at the appropriate offset.
        
        Args:
            clip: The clip to start
            current_position: Current timeline position in seconds
        """
        # Determine if we need boosting
        if clip.volume > 1.0:
            boosted_path = self._prepare_boosted_audio(clip)
            if boosted_path:
                player, audio_output = self._get_or_create_boosted_player(clip.clip_id, boosted_path)

                # Master volume only (boost baked in)
                audio_output.setVolume(self._volume)

                # For boosted clips, source is the EXTRACTED segment (offset 0)
                # Time into clip determines position in temp file
                time_into_clip = current_position - clip.timeline_start
                source_position_ms = int(time_into_clip * 1000)

                self._active_players[clip.clip_id] = (player, audio_output)

                if player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia:
                    player.setPlaybackRate(self._playback_rate)
                    player.setPosition(source_position_ms)
                    if self._playing:
                        player.play()
                else:
                    def on_media_status_changed(status):
                        if status == QMediaPlayer.MediaStatus.LoadedMedia:
                            player.setPlaybackRate(self._playback_rate)
                            if self._playing:
                                current_tic = self._position - clip.timeline_start
                                player.setPosition(int(current_tic * 1000))
                                player.play()
                            else:
                                player.setPosition(source_position_ms)
                            try:
                                player.mediaStatusChanged.disconnect(on_media_status_changed)
                            except Exception:
                                pass
                    player.mediaStatusChanged.connect(on_media_status_changed)
                return

        # Fallback to standard logic for <= 1.0 volume (shared players)
        cached = self._get_or_create_cached_player(clip.speaker)
        if cached is None:
            return
            
        player, audio_output, seek_correction = cached
        
        # Apply volume
        effective_vol = self._volume * clip.volume
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

        # Clear boosted players
        for clip_id, (player, audio_output) in self._boosted_players.items():
            player.stop()
            player.deleteLater()
            audio_output.deleteLater()
        self._boosted_players.clear()

        # Remove temp files
        for path in self._boosted_files_cache.values():
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        self._boosted_files_cache.clear()
        self._clip_boosted_file.clear()
        
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
            # 1. Update volume immediately
            player, audio_output = self._active_players[clip.clip_id]

            # Check if we switched from normal to boosted or vice-versa
            is_currently_boosted = (clip.clip_id in self._boosted_players)
            needs_boost = (clip.volume > 1.0)

            if is_currently_boosted != needs_boost:
                # Mode switched, restart clip to switch player architecture
                if self._playing:
                    self._stop_clip(clip.clip_id)
                    if clip.timeline_start <= self._position < clip.timeline_end:
                        self._start_clip(clip, self._position)
                else:
                    self._stop_clip(clip.clip_id)

            elif needs_boost:
                # Boosted -> Boosted.
                # Check if we need to regenerate due to volume change
                # We do this by checking if the _prepare_boosted_audio returns a different path
                # (since key includes volume) or simply if we assume it changed.

                # Ideally we only restart if the path changed.
                # But calculating path requires calling _prepare_boosted_audio (which might run ffmpeg).
                # Since we already optimized UI to NOT call this during drag, we can assume
                # any call here is a committed change (or legitimate update).

                # So we restart to be safe and apply new file.
                if self._playing:
                    self._stop_clip(clip.clip_id)
                    if clip.timeline_start <= self._position < clip.timeline_end:
                        self._start_clip(clip, self._position)
                else:
                    self._stop_clip(clip.clip_id)

            else:
                # Normal -> Normal. Just update volume multiplier.
                effective_vol = self._volume * clip.volume
                audio_output.setVolume(effective_vol)

            # 2. If playing and timing changed significantly, restart might be needed
            # (Handled by restart logic above for boost cases)
            # For standard clips, we stick to existing restart logic if timing changes
            # We assume volume-only updates don't need restart for standard clips.
                
        self._update_duration()
        
    def remove_clip(self, clip_id: str):
        """Remove a clip.
        
        Args:
            clip_id: ID of the clip to remove
        """
        # Stop if playing
        if clip_id in self._active_players:
            self._stop_clip(clip_id)
            
        # Clean up boosted resources if any
        if clip_id in self._boosted_players:
             player, audio_output = self._boosted_players.pop(clip_id)
             player.stop()
             player.deleteLater()
             audio_output.deleteLater()

        # Remove from list
        self.clips = [c for c in self.clips if c.clip_id != clip_id]
        self._update_duration()

        # Cleanup file ownership for this clip
        if clip_id in self._clip_boosted_file:
            path = self._clip_boosted_file.pop(clip_id)
            # Check if used by others in cache (unlikely with our usage pattern but possible)
            # We won't delete the file immediately from disk to be safe with shared cache keys,
            # but we remove our reference.
            # Real cleanup happens in _clear_player_cache or app exit.
