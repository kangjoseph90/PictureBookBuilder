"""
Preview Widget - Audio playback and image preview
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


class PreviewWidget(QWidget):
    """Widget for previewing images and audio playback"""
    
    # Signal emitted when playback position changes (position_ms)
    position_changed = pyqtSignal(int)
    
    def __init__(self):
        super().__init__()
        self.current_image: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.total_duration: float = 0.0
        self.image_clips: list[dict] = []  # [{'path': str, 'start': float, 'end': float}]
        self.subtitle_clips: list[dict] = []  # [{'text': str, 'start': float, 'end': float}]
        self.images: list[str] = []  # Added for backward compatibility/internal use
        self.current_subtitle: Optional[str] = None
        
        self._setup_ui()
        self._setup_audio()
    
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
        # Word wrap enabled to prevent off-screen text on small windows
        self.subtitle_label.setWordWrap(True)
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
    
    def _setup_audio(self):
        """Setup audio player"""
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        
        self.media_player = QMediaPlayer()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_state_changed)
        self.media_player.errorOccurred.connect(self._on_error)
        
        self.is_seeking = False
    
    def set_audio(self, audio_path: str, initial_pos_ms: int = 0):
        """Set the audio file to play"""
        self.audio_path = audio_path
        
        if not audio_path or not Path(audio_path).exists():
            self.status_label.setText("오디오 없음")
            return
        
        # If we want to restore position, save it for later when duration is known
        if initial_pos_ms > 0:
            self._target_pos_ms = initial_pos_ms
        else:
            self._target_pos_ms = None
        
        # Stop playback and clear source first to force reload
        # This prevents QMediaPlayer from caching the old file
        self.media_player.stop()
        self.media_player.setSource(QUrl())  # Clear source
        
        url = QUrl.fromLocalFile(audio_path)
        self.media_player.setSource(url)
        self.status_label.setText("준비됨")
        
        # Only show prompt if we are not restoring a position
        if initial_pos_ms <= 0:
            self.image_label.setText("재생 버튼을 누르세요")
            
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
    
    def _on_duration_changed(self, duration: int):
        """Handle duration change"""
        self.total_duration = duration / 1000.0
        self.time_label.setText(f"0:00 / {self._format_time(duration)}")
        
        # Restore position if we have a target
        if hasattr(self, '_target_pos_ms') and self._target_pos_ms is not None:
            if self._target_pos_ms < duration:
                self.media_player.setPosition(self._target_pos_ms)
            self._target_pos_ms = None
    
    def set_total_duration(self, duration_sec: float):
        """Set total duration from timeline (overrides audio-based duration)
        
        This allows the preview to show the full timeline duration even if
        the audio file is shorter than the timeline.
        
        Args:
            duration_sec: Total duration in seconds
        """
        if duration_sec > self.total_duration:
            self.total_duration = duration_sec
            duration_ms = int(duration_sec * 1000)
            self.time_label.setText(
                f"{self._format_time(self.media_player.position())} / {self._format_time(duration_ms)}"
            )
    
    def _on_state_changed(self, state):
        """Handle playback state change"""
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.status_label.setText("재생 중")
            self.status_label.setStyleSheet("color: #4CAF50;")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.status_label.setText("일시정지")
            self.status_label.setStyleSheet("color: orange;")
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.status_label.setText("정지")
            self.status_label.setStyleSheet("color: gray;")
    
    def _on_error(self, error):
        """Handle playback error"""
        self.status_label.setText(f"오류: {self.media_player.errorString()}")
        self.status_label.setStyleSheet("color: red;")
    
    def _toggle_play(self):
        """Toggle playback"""
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()
    
    def _stop(self):
        """Stop playback"""
        self.media_player.stop()
        self.seek_slider.setValue(0)
        # We don't necessarily want to reset image on stop, 
        # but let's sync with start of audio if available
        self.media_player.setPosition(0)
    
    def _go_to_start(self):
        """Skip to the beginning"""
        self.media_player.setPosition(0)
    
    def _go_to_end(self):
        """Skip to the end"""
        if self.total_duration > 0:
            self.media_player.setPosition(int(self.total_duration * 1000))
            
    def _on_speed_changed(self, index: int):
        """Handle playback speed change"""
        speed_text = self.speed_combo.currentText().replace("x", "")
        try:
            speed = float(speed_text)
            self.media_player.setPlaybackRate(speed)
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
        self.media_player.setPosition(position)
    
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
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        
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
        self.media_player.stop()
