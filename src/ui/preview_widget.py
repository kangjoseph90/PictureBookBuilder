"""
Preview Widget - Audio playback and image preview using AudioMixer

Uses AudioMixer for real-time playback of timeline clips without
pre-merging audio files.
"""
from pathlib import Path
from typing import Optional
import tempfile
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QStyle, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .audio_mixer import AudioMixer, ScheduledClip


class PreviewWidget(QWidget):
    """Widget for previewing images and audio playback using AudioMixer"""
    
    # Signal emitted when playback position changes (position_ms)
    position_changed = pyqtSignal(int)
    
    def __init__(self):
        super().__init__()
        self.current_image: Optional[str] = None
        self.audio_path: Optional[str] = None  # Kept for compatibility
        self.total_duration: float = 0.0
        self.image_clips: list[dict] = []  # [{'path': str, 'start': float, 'end': float}]
        self.subtitle_clips: list[dict] = []  # [{'text': str, 'start': float, 'end': float}]
        self.images: list[str] = []  # Added for backward compatibility/internal use
        self.current_subtitle: Optional[str] = None
        
        self._setup_ui()
        self._setup_audio_mixer()
    
    def _setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Image display
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(320, 180)
        self.image_label.setStyleSheet("""
            QLabel {
                background-color: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
            }
        """)
        self.image_label.setText("미리보기\n\n처리 완료 후 재생 버튼을 누르세요")
        
        # Subtitle overlay (on top of image)
        self.subtitle_label = QLabel(self.image_label)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Word wrap disabled - subtitles should only break at explicit \n
        self.subtitle_label.setWordWrap(False)
        self.subtitle_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 160);
                padding: 8px 15px;
                border-radius: 4px;
                font-size: 16px;
                font-weight: bold;
                line-height: 1.4;
            }
        """)
        self.subtitle_label.hide()
        
        layout.addWidget(self.image_label, 1)
        
        # Time display
        time_layout = QHBoxLayout()
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("font-family: monospace;")
        time_layout.addWidget(self.time_label)
        layout.addLayout(time_layout)
        
        # Seek slider
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        self.seek_slider.sliderPressed.connect(self._on_seek_start)
        self.seek_slider.sliderReleased.connect(self._on_seek_end)
        layout.addWidget(self.seek_slider)
        
        # Playback controls
        controls_layout = QHBoxLayout()
        
        self.status_label = QLabel("대기 중")
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setFixedWidth(60) # Fixed width to prevent shifting
        controls_layout.addWidget(self.status_label)
        
        controls_layout.addStretch()
        
        self.btn_start = QPushButton()
        self.btn_start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.btn_start.setToolTip("맨 앞으로")
        self.btn_start.clicked.connect(self._go_to_start)
        self.btn_start.setFixedWidth(40)
        controls_layout.addWidget(self.btn_start)
        
        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setToolTip("재생/일시정지")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_play.setFixedWidth(40) # Smaller as it's icon only
        controls_layout.addWidget(self.btn_play)
        
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.setToolTip("정지")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setFixedWidth(40)
        controls_layout.addWidget(self.btn_stop)

        self.btn_end = QPushButton()
        self.btn_end.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.btn_end.setToolTip("맨 뒤로")
        self.btn_end.clicked.connect(self._go_to_end)
        self.btn_end.setFixedWidth(40)
        controls_layout.addWidget(self.btn_end)
        
        controls_layout.addStretch()
        
        # Speed control on the right
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentIndex(2) # 1.0x
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self.speed_combo.setFixedWidth(70)
        controls_layout.addWidget(self.speed_combo)
        
        layout.addLayout(controls_layout)
    
    def _setup_audio_mixer(self):
        """Setup AudioMixer for real-time clip playback"""
        self.audio_mixer = AudioMixer(self)
        self.audio_mixer.position_changed.connect(self._on_position_changed)
        self.audio_mixer.duration_changed.connect(self._on_duration_changed_from_mixer)
        self.audio_mixer.playback_state_changed.connect(self._on_state_changed_from_mixer)
        
        self.is_seeking = False
        
        # For compatibility with external code that accesses media_player
        # We provide a minimal interface
        self._dummy_media_player = _DummyMediaPlayer(self.audio_mixer)
    
    @property
    def media_player(self):
        """Compatibility property - returns wrapper around AudioMixer"""
        return self._dummy_media_player
    
    def set_audio_clips(self, clips: list[ScheduledClip], speaker_audio_paths: dict[str, str]):
        """Set audio clips for playback using AudioMixer.
        
        Args:
            clips: List of ScheduledClip objects
            speaker_audio_paths: Dict mapping speaker names to audio file paths
        """
        self.audio_mixer.set_speaker_audio_paths(speaker_audio_paths)
        self.audio_mixer.set_clips(clips)
        self.status_label.setText("준비됨")
        self.audio_path = "mixer"  # Mark as ready for playback
    
    def update_audio_clip(self, clip: ScheduledClip):
        """Update a single audio clip (for real-time editing).
        
        Args:
            clip: Updated clip data
        """
        self.audio_mixer.update_clip(clip)
        
    def remove_audio_clip(self, clip_id: str):
        """Remove an audio clip.
        
        Args:
            clip_id: ID of the clip to remove
        """
        self.audio_mixer.remove_clip(clip_id)
    
    def set_audio(self, audio_path: str, initial_pos_ms: int = 0):
        """Legacy method - now handled by set_audio_clips.
        
        This method is kept for backwards compatibility but does nothing
        when using AudioMixer. Use set_audio_clips() instead.
        """
        # When using AudioMixer, audio is set via set_audio_clips
        # This is kept for compatibility with code that checks audio_path
        if audio_path == "mixer":
            self.audio_path = audio_path
            if initial_pos_ms > 0:
                self.audio_mixer.seek(initial_pos_ms / 1000.0)
            return
            
        # Legacy path - mark as having audio but don't actually load
        self.audio_path = audio_path
        if initial_pos_ms > 0:
            self.audio_mixer.seek(initial_pos_ms / 1000.0)
            
    def set_images(self, image_paths: list[str], timestamps: list[float]):
        """Set images with specific start timestamps
        
        Args:
            image_paths: List of local paths to images
            timestamps: List of start times (seconds) for each image
        """
        self.images = image_paths
        self.image_clips = []
        
        for i in range(len(image_paths)):
            path = image_paths[i]
            start = timestamps[i]
            # End is the next timestamp, or a very large value if last
            end = timestamps[i+1] if i + 1 < len(timestamps) else 999999.0
            
            self.image_clips.append({
                'path': path,
                'start': start,
                'end': end
            })
            
        # Update current display
        if self.media_player.position() >= 0:
            self._on_position_changed(self.media_player.position())
    
    def set_timeline_clips(self, clips: list, playhead_ms: int = None):
        """Update preview data from timeline clips
        
        Args:
            clips: List of timeline clips
            playhead_ms: Current playhead position in ms (if None, uses media player position)
        """
        self.image_clips = []
        self.subtitle_clips = []
        
        for clip in clips:
            if clip.clip_type == "image":
                self.image_clips.append({
                    'path': clip.image_path,
                    'start': clip.start,
                    'end': clip.start + clip.duration
                })
            elif clip.clip_type == "subtitle":
                self.subtitle_clips.append({
                    'text': clip.name,
                    'start': clip.start,
                    'end': clip.start + clip.duration
                })
        
        # Update display for current position
        if playhead_ms is not None:
            self._on_position_changed(playhead_ms)
        elif self.media_player.position() >= 0:
            self._on_position_changed(self.media_player.position())

    def _get_current_image(self, position_ms: int) -> Optional[str]:
        """Get the image that should be displayed at current position"""
        if not self.image_clips:
            return None
        
        pos = position_ms / 1000.0
        for clip in self.image_clips:
            if clip['start'] <= pos <= clip['end']:
                return clip['path']
        return None

    def _get_current_subtitle(self, position_ms: int) -> Optional[str]:
        """Get the subtitle that should be displayed at current position"""
        if not self.subtitle_clips:
            return None
        
        pos = position_ms / 1000.0
        for clip in self.subtitle_clips:
            if clip['start'] <= pos <= clip['end']:
                return clip['text']
        return None
    
    def set_image(self, image_path: str):
        """Display an image"""
        self.current_image = image_path
        
        if not image_path or not Path(image_path).exists():
            return
        
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            return
        
        # Scale to fit while maintaining aspect ratio
        scaled = pixmap.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
    
    def _format_time(self, ms: int) -> str:
        """Format milliseconds as M:SS"""
        seconds = ms // 1000
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"
    
    def _on_position_changed(self, position: int):
        """Handle position updates during playback"""
        if self.is_seeking:
            return
        
        # Emit signal to sync with timeline
        self.position_changed.emit(position)
        
        # Update time display
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(int(self.total_duration * 1000))}"
        )
        
        # Update slider
        if self.total_duration > 0:
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(int(position / (self.total_duration * 1000) * 1000))
            self.seek_slider.blockSignals(False)
        
        # Update image and subtitle if we have clips
        image = self._get_current_image(position)
        if image != self.current_image:
            if image:
                self.set_image(image)
            else:
                # Clear image
                self.image_label.clear()
                self.current_image = None
                
        subtitle = self._get_current_subtitle(position)
        if subtitle != self.current_subtitle:
            self.current_subtitle = subtitle
            if subtitle:
                self.subtitle_label.setText(subtitle)
                self.subtitle_label.show()
                # Center the subtitle label at the bottom
                self._reposition_subtitle()
            else:
                self.subtitle_label.hide()
    
    def _reposition_subtitle(self):
        """Resposition subtitle label to be at the bottom center of image area"""
        if not self.subtitle_label.isVisible():
            return
            
        img_w = self.image_label.width()
        img_h = self.image_label.height()
        
        # Limit width and update layout
        max_w = int(img_w * 0.8)
        self.subtitle_label.setMaximumWidth(max_w)
        self.subtitle_label.adjustSize()
        
        sub_w = self.subtitle_label.width()
        sub_h = self.subtitle_label.height()
        
        # Position at bottom center (with some margin)
        self.subtitle_label.move(
            (img_w - sub_w) // 2,
            img_h - sub_h - 30
        )
    
    def _on_duration_changed_from_mixer(self, duration_sec: float):
        """Handle duration change from AudioMixer"""
        self.total_duration = duration_sec
        duration_ms = int(duration_sec * 1000)
        self.time_label.setText(f"0:00 / {self._format_time(duration_ms)}")
    
    def set_total_duration(self, duration_sec: float):
        """Set total duration from timeline (overrides audio-based duration)
        
        This allows the preview to show the full timeline duration even if
        the audio file is shorter than the timeline.
        
        Args:
            duration_sec: Total duration in seconds
        """
        self.total_duration = duration_sec
        self.audio_mixer.set_duration(duration_sec)
        duration_ms = int(duration_sec * 1000)
        self.time_label.setText(
            f"{self._format_time(self.audio_mixer.position_ms)} / {self._format_time(duration_ms)}"
        )
    
    def _on_state_changed_from_mixer(self, state: str):
        """Handle playback state change from AudioMixer"""
        if state == 'playing':
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.status_label.setText("재생 중")
            self.status_label.setStyleSheet("color: #4CAF50;")
        elif state == 'paused':
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.status_label.setText("일시정지")
            self.status_label.setStyleSheet("color: orange;")
        else:  # stopped
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.status_label.setText("정지")
            self.status_label.setStyleSheet("color: gray;")
    
    def _on_error(self, error):
        """Handle playback error"""
        self.status_label.setText(f"오류: {self.media_player.errorString()}")
        self.status_label.setStyleSheet("color: red;")
    
    def _toggle_play(self):
        """Toggle playback"""
        if self.audio_mixer.is_playing:
            self.audio_mixer.pause()
        else:
            self.audio_mixer.play()
    
    def _stop(self):
        """Stop playback"""
        self.audio_mixer.stop()
        self.seek_slider.setValue(0)
    
    def _go_to_start(self):
        """Skip to the beginning"""
        self.audio_mixer.seek(0)
    
    def _go_to_end(self):
        """Skip to the end"""
        if self.total_duration > 0:
            self.audio_mixer.seek(self.total_duration)
            
    def _on_speed_changed(self, index: int):
        """Handle playback speed change"""
        speed_text = self.speed_combo.currentText().replace("x", "")
        try:
            speed = float(speed_text)
            self.audio_mixer.set_playback_rate(speed)
        except ValueError:
            pass
    
    def _on_seek_start(self):
        """Called when user starts dragging the seek slider"""
        self.is_seeking = True
    
    def _on_seek_end(self):
        """Called when user releases the seek slider"""
        self.is_seeking = False
        value = self.seek_slider.value()
        position = int(value / 1000 * self.total_duration * 1000)
        self.audio_mixer.seek(position / 1000.0)
    
    def _on_seek(self, value: int):
        """Handle seek slider drag"""
        if self.total_duration > 0:
            position_ms = int(value / 1000 * self.total_duration * 1000)
            self.time_label.setText(
                f"{self._format_time(position_ms)} / {self._format_time(int(self.total_duration * 1000))}"
            )
            # Update image preview while seeking
            if self.image_clips:
                image = self._get_current_image(position_ms)
                if image:
                    self.set_image(image)
    
    def resizeEvent(self, event):
        """Handle resize to refresh image scaling and subtitle position"""
        super().resizeEvent(event)
        if self.current_image:
            self.set_image(self.current_image)
        self._reposition_subtitle()
    
    def clear_preview(self):
        """Clear all preview data and reset to initial state"""
        # Stop playback
        self.audio_mixer.stop()
        self.audio_mixer.set_clips([])
        
        # Clear data
        self.audio_path = None
        self.current_image = None
        self.image_clips = []
        self.subtitle_clips = []
        self.images = []
        self.current_subtitle = None
        self.total_duration = 0.0
        
        # Reset UI
        self.image_label.clear()
        self.image_label.setText("미리보기\n\n처리 완료 후 재생 버튼을 누르세요")
        self.subtitle_label.hide()
        self.subtitle_label.setText("")
        self.time_label.setText("0:00 / 0:00")
        self.seek_slider.setValue(0)
        self.status_label.setText("대기 중")
        self.status_label.setStyleSheet("color: gray;")
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
    
    def cleanup(self):
        """Cleanup resources"""
        self.audio_mixer.cleanup()


class _DummyMediaPlayer:
    """
    Compatibility wrapper to provide a media_player-like interface
    that delegates to AudioMixer. This allows external code that
    accesses preview_widget.media_player to continue working.
    """
    
    def __init__(self, mixer: AudioMixer):
        self._mixer = mixer
        # Connect signals for compatibility
        self.positionChanged = mixer.position_changed
        
    def position(self) -> int:
        """Get current position in milliseconds"""
        return self._mixer.position_ms
    
    def setPosition(self, position_ms: int):
        """Set position in milliseconds"""
        self._mixer.seek(position_ms / 1000.0)
        
    def play(self):
        """Start playback"""
        self._mixer.play()
        
    def pause(self):
        """Pause playback"""
        self._mixer.pause()
        
    def stop(self):
        """Stop playback"""
        self._mixer.stop()
        
    def setPlaybackRate(self, rate: float):
        """Set playback rate"""
        self._mixer.set_playback_rate(rate)
        
    def playbackState(self):
        """Get playback state - returns compatible state object"""
        if self._mixer.is_playing:
            return QMediaPlayer.PlaybackState.PlayingState
        else:
            return QMediaPlayer.PlaybackState.StoppedState
            
    def setSource(self, url):
        """Compatibility method - does nothing with AudioMixer"""
        pass
        
    def errorString(self) -> str:
        """Return empty error string"""
        return ""
