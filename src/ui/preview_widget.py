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
    QSlider, QStyle, QComboBox, QStyleOption, QStyleOptionSlider
)
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QPen, QColor, QFontMetrics, QMouseEvent, QIcon, QPolygonF, QBrush
from PyQt6.QtCore import QPointF


class ClickableSlider(QSlider):
    """QSlider that allows clicking anywhere to jump to that position"""
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Calculate the value based on click position
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            sr = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
            
            if self.orientation() == Qt.Orientation.Horizontal:
                half_handle_width = (sr.width() / 2) if not sr.isEmpty() else 0
                available_width = self.width() - sr.width()
                if available_width > 0:
                    pos = event.position().x() - half_handle_width
                    # Clamp position
                    pos = max(0, min(pos, available_width))
                    value = self.minimum() + (pos / available_width) * (self.maximum() - self.minimum())
                else:
                    value = self.minimum()
            else:
                half_handle_height = (sr.height() / 2) if not sr.isEmpty() else 0
                available_height = self.height() - sr.height()
                if available_height > 0:
                    pos = (self.height() - event.position().y()) - half_handle_height
                    # Clamp position
                    pos = max(0, min(pos, available_height))
                    value = self.minimum() + (pos / available_height) * (self.maximum() - self.minimum())
                else:
                    value = self.minimum()
            
            self.setValue(int(value))
            self.sliderMoved.emit(int(value))
            # No event.accept() here to let QSlider handle the click-and-drag normally
        super().mousePressEvent(event)

from .image_cache import get_image_cache

from .audio_mixer import AudioMixer, ScheduledClip


class StrokedLabel(QLabel):
    """QLabel subclass that supports text outline/stroke rendering"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._outline_width = 0
        self._outline_color = QColor(0, 0, 0)
        self._text_color = QColor(255, 255, 255)
        self._line_spacing = 1.4
        
    def set_line_spacing(self, spacing):
        """Set line spacing multiplier (e.g., 1.4)"""
        self._line_spacing = spacing
        self.update()
        
    def set_outline(self, width, color):
        """Set outline (stroke) properties"""
        self._outline_width = width
        self._outline_color = QColor(color) if color else QColor(0,0,0)
        self.update()
        
    def set_text_color(self, color):
        """Set text fill color"""
        self._text_color = QColor(color) if color else QColor(255,255,255)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Draw Background (using stylesheet style if available via style options)
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        
        if not self.text():
            return
            
        # 2. Setup Font & Path
        painter.setFont(self.font())
        path = QPainterPath()
        metrics = self.fontMetrics()
        
        # Handle multiline text
        lines = self.text().split('\n')
        line_height = metrics.height()
        
        # Calculate positioning
        # We assume styling provides padding, but we need to calculate centering within contentsRect
        rect = self.contentsRect()
        
        # Calculate total text block height (using strict spacing like subtitle renderer often does)
        # Using custom line spacing
        leading = line_height * (self._line_spacing - 1.0)
        total_text_height = len(lines) * line_height + (len(lines) - 1) * leading
        
        # Center vertically
        start_y = rect.center().y() - total_text_height / 2 + metrics.ascent()
        
        current_y = start_y
        for line in lines:
            if not line:
                current_y += line_height + leading
                continue
                
            line_width = metrics.horizontalAdvance(line)
            
            # Horizontal alignment
            x = rect.left()
            if self.alignment() & Qt.AlignmentFlag.AlignHCenter:
                x = rect.center().x() - line_width / 2
            elif self.alignment() & Qt.AlignmentFlag.AlignRight:
                x = rect.right() - line_width
            
            path.addText(x, current_y, self.font(), line)
            current_y += line_height + leading

        # 3. Draw Outline (Stroke)
        if self._outline_width > 0:
            pen = QPen(self._outline_color)
            # Width * 2 because stroke is centered on the path boundary,
            # so half is inside (covered by fill) and half is outside.
            # We want 'width' pixels visible outside.
            pen.setWidthF(self._outline_width * 2) 
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            
        # 4. Draw Text Fill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._text_color)
        painter.drawPath(path)
        
    def sizeHint(self):
        """Calculate size hint including custom letter/line spacing"""
        if not self.text():
            return super().sizeHint()
            
        metrics = self.fontMetrics()
        lines = self.text().split('\n')
        
        # Calculate text dimensions
        line_height = metrics.height()
        leading = line_height * (self._line_spacing - 1.0)
        
        # Height
        num_lines = len(lines)
        total_text_height = num_lines * line_height + (num_lines - 1) * leading
        
        # Width
        max_line_width = 0
        for line in lines:
            w = metrics.horizontalAdvance(line)
            if w > max_line_width:
                max_line_width = w
                
        # Add outline width allowance (approximate)
        w = max_line_width + self._outline_width * 2
        h = total_text_height + self._outline_width * 2
        
        # Add contents margins (which includes stylesheet padding)
        # Note: ensurePolished() might be needed if style just changed, 
        # but typically adjustSize() calls it.
        self.ensurePolished() 
        marg = self.contentsMargins()
        
        w += marg.left() + marg.right()
        h += marg.top() + marg.bottom()
        
        # Add a small buffer for antialiasing/rounding
        w += 4
        h += 4
        
        return QSize(int(w), int(h))
        
    def minimumSizeHint(self):
        return self.sizeHint()


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
        self.showing_placeholder = True
        self.subtitles_enabled = True
        self._last_prefetch_idx = -1  # Track last prefetched item index
        
        # Debounce timer for high-res loading after scrubbing
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.timeout.connect(self._do_deferred_prefetch)
        
        # Image cache for shared originals
        self._image_cache = get_image_cache()
        # Connect to image loaded signal for async updates
        self._image_cache.image_loaded.connect(self._on_image_loaded)
        
        self._setup_ui()
        self._setup_audio_mixer()

    def _on_image_loaded(self, path: str):
        """Handle async image loading completion"""
        # If the loaded image is the one we are currently supposed to show, update it
        if hasattr(self, 'current_image') and self.current_image == path:
            # Re-trigger set_image which will now find it in cache
            self.set_image(path)
    
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
        # Subtitle overlay (on top of image)
        self.subtitle_label = StrokedLabel(self.image_label)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Word wrap disabled - subtitles should only break at explicit \n
        self.subtitle_label.setWordWrap(False)
        self.subtitle_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 160);
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        self.subtitle_label.hide()
        
        layout.addWidget(self.image_label, 1)
        
        # Time and Status display row
        time_status_layout = QHBoxLayout()
        
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("font-family: monospace; font-size: 12px; color: #aaaaaa;")
        time_status_layout.addWidget(self.time_label)
        
        time_status_layout.addStretch()
        
        self.status_label = QLabel("대기 중")
        self.status_label.setStyleSheet("color: gray; font-size: 12px;")
        time_status_layout.addWidget(self.status_label)
        
        layout.addLayout(time_status_layout)
        
        # Seek slider (clickable)
        self.seek_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        self.seek_slider.sliderPressed.connect(self._on_seek_start)
        self.seek_slider.sliderReleased.connect(self._on_seek_end)
        layout.addWidget(self.seek_slider)
        
        # Playback controls bar (Volume - Controls - Speed)
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 5, 0, 0)
        
        # --- LEFT: Volume Control ---
        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(2)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_mute = QPushButton()
        self.btn_mute.setIcon(self._create_volume_icon(muted=False))
        self.btn_mute.setToolTip("음소거")
        self.btn_mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_mute.setFixedSize(20, 20)
        self.btn_mute.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
            }
        """)
        volume_layout.addWidget(self.btn_mute, 0, Qt.AlignmentFlag.AlignVCenter)
        
        self.volume_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume_slider.setToolTip("음량 조절")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderMoved.connect(self._on_volume_changed)
        # Styled closer to modern media players
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #333333;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #888888;
                width: 10px;
                height: 10px;
                margin: -3px 0;
                border-radius: 5px;
            }
            QSlider::handle:horizontal:hover {
                background: #bbbbbb;
            }
            QSlider::sub-page:horizontal {
                background: #666666;
                border-radius: 2px;
            }
        """)
        volume_layout.addWidget(self.volume_slider)
        
        # Mute state tracking
        self._pre_mute_volume = 100
        self._is_muted = False
        
        controls_layout.addLayout(volume_layout)
        
        # Spacer
        controls_layout.addStretch()
        
        # --- CENTER: Playback Buttons ---
        self.btn_start = QPushButton()
        self.btn_start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.btn_start.setToolTip("맨 앞으로")
        self.btn_start.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_start.clicked.connect(self._go_to_start)
        self.btn_start.setFixedWidth(40)
        controls_layout.addWidget(self.btn_start)
        
        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setToolTip("재생/일시정지")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_play.setFixedWidth(40)
        controls_layout.addWidget(self.btn_play)
        
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.setToolTip("정지")
        self.btn_stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setFixedWidth(40)
        controls_layout.addWidget(self.btn_stop)

        self.btn_end = QPushButton()
        self.btn_end.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.btn_end.setToolTip("맨 뒤로")
        self.btn_end.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_end.clicked.connect(self._go_to_end)
        self.btn_end.setFixedWidth(40)
        controls_layout.addWidget(self.btn_end)
        
        # Spacer
        controls_layout.addStretch()
        
        # --- RIGHT: Speed Control ---
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentIndex(2) # 1.0x
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._last_prefetch_idx = -1  # Reset prefetch state when clips change
        
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
        
        # Pre-cache all images for smooth playback
        self._preload_all_images()
        
        # Update display for current position
        if playhead_ms is not None:
            self._on_position_changed(playhead_ms)
        elif self.media_player.position() >= 0:
            self._on_position_changed(self.media_player.position())
    
    def _preload_all_images(self):
        """Images are now loaded by main_window when folder is opened.
        
        This method exists for compatibility but no longer needs to do anything
        since all images are loaded upfront into the shared cache.
        """
        pass

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
        """Display an image from the shared cache"""
        self.current_image = image_path
        
        if not image_path:
            return
        
        target_size = self.image_label.size()
        
        # 1. Get original from shared cache (Best quality)
        original = self._image_cache.get_original(image_path)
        if original and not original.isNull():
            # Scale to fit label
            scaled = original.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled)
            self.showing_placeholder = False
            return
        
        # 2. Fallback: Get medium-res preview thumbnail (Fast feedback)
        preview_thumb = self._image_cache.get_thumbnail_preview(image_path)
        if preview_thumb and not preview_thumb.isNull():
            # Scale up to fit label (might be slightly blurry, but fast)
            scaled = preview_thumb.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled)
            self.showing_placeholder = False
            return
        
        # 3. Last fallback: Keep previous frame or show placeholder if needed
        # (The image_loaded signal will eventually trigger 1 or 2)
    
    def _format_time(self, ms: int) -> str:
        """Format milliseconds as M:SS"""
        seconds = ms // 1000
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"
    
    def _update_preview_content(self, position_ms: int):
        """Update image and subtitle for the given position"""
        # Lookahead prefetching (load next 3 images)
        if self.image_clips:
            current_idx = -1
            pos_sec = position_ms / 1000.0

            # Find current index
            for i, clip in enumerate(self.image_clips):
                if clip['start'] <= pos_sec <= clip['end']:
                    current_idx = i
                    break

            if current_idx != -1 and current_idx != self._last_prefetch_idx:
                # If seeking (scrubbing), don't prefetch immediately to save I/O
                if self.is_seeking:
                    # Restart timer to prefetch only when scrubbing slows down or stops
                    self._prefetch_timer.start(200) # 200ms debounce
                else:
                    self._request_prefetch(current_idx)

        # Update image and subtitle if we have clips

        # Update image and subtitle if we have clips
        image = self._get_current_image(position_ms)
        
        # Check if we need to force update to clear placeholder text
        force_update = self.showing_placeholder
        
        if image != self.current_image or force_update:
            if image:
                self.set_image(image)
            else:
                # Clear image (and placeholder text if any)
                self.image_label.clear()
                self.current_image = None
            
            self.showing_placeholder = False
                
        subtitle = self._get_current_subtitle(position_ms)
        if subtitle != self.current_subtitle:
            self.current_subtitle = subtitle
            if subtitle and self.subtitles_enabled:
                self.subtitle_label.setText(subtitle)
                self.subtitle_label.show()
                # Center the subtitle label
                self._reposition_subtitle()
            else:
                self.subtitle_label.hide()

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
        
        # Update content (image & subtitle)
        self._update_preview_content(position)
    
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
    
    def toggle_playback(self):
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
    
    def _create_volume_icon(self, muted: bool = False) -> QIcon:
        """Create a custom white volume icon for dark theme visibility"""
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Subdued color for better integration
        color = QColor("#888888")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        
        # Draw speaker body (small trapezoid/rectangle)
        speaker_body = QPolygonF([
            QPointF(2, 6),
            QPointF(5, 6),
            QPointF(5, 10),
            QPointF(2, 10),
        ])
        painter.drawPolygon(speaker_body)
        
        # Draw speaker cone (triangle)
        speaker_cone = QPolygonF([
            QPointF(5, 5),
            QPointF(9, 2),
            QPointF(9, 14),
            QPointF(5, 11),
        ])
        painter.drawPolygon(speaker_cone)
        
        if muted:
            # Draw X mark for muted - minimal grey design
            painter.setPen(QPen(color, 2))
            painter.drawLine(10, 4, 15, 12)
            painter.drawLine(15, 4, 10, 12)
        else:
            # Draw sound waves
            painter.setPen(QPen(color, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Small wave
            path1 = QPainterPath()
            path1.moveTo(11, 6)
            path1.quadTo(13, 8, 11, 10)
            painter.drawPath(path1)
            # Medium wave
            path2 = QPainterPath()
            path2.moveTo(13, 4)
            path2.quadTo(16, 8, 13, 12)
            painter.drawPath(path2)
        
        painter.end()
        return QIcon(pixmap)
    
    def _on_volume_changed(self, value: int):
        """Handle volume slider change"""
        volume = value / 100.0
        self.audio_mixer.set_volume(volume)
        
        # Update mute state and icon
        if value == 0:
            self._is_muted = True
            self.btn_mute.setIcon(self._create_volume_icon(muted=True))
        else:
            self._is_muted = False
            self._pre_mute_volume = value
            self.btn_mute.setIcon(self._create_volume_icon(muted=False))
    
    def _toggle_mute(self):
        """Toggle mute state"""
        if self._is_muted:
            # Unmute: restore previous volume
            self.volume_slider.setValue(self._pre_mute_volume)
        else:
            # Mute: save current volume and set to 0
            self._pre_mute_volume = self.volume_slider.value() if self.volume_slider.value() > 0 else 100
            self.volume_slider.setValue(0)
    
    def _on_seek_start(self):
        """Called when user starts dragging the seek slider"""
        self.is_seeking = True
    
    def _on_seek_end(self):
        """Called when user releases the seek slider"""
        self.is_seeking = False
        value = self.seek_slider.value()
        position = int(value / 1000 * self.total_duration * 1000)
        self.audio_mixer.seek(position / 1000.0)
        
        # Force immediate prefetch when user stops scrubbing
        self._prefetch_timer.stop()
        
        pos_sec = self.audio_mixer.position_ms / 1000.0
        current_idx = -1
        for i, clip in enumerate(self.image_clips):
            if clip['start'] <= pos_sec <= clip['end']:
                current_idx = i
                break
        
        if current_idx != -1:
            self._request_prefetch(current_idx)
    
    def _on_seek(self, value: int):
        """Handle seek slider drag"""
        if self.total_duration > 0:
            position_ms = int(value / 1000 * self.total_duration * 1000)
            self.time_label.setText(
                f"{self._format_time(position_ms)} / {self._format_time(int(self.total_duration * 1000))}"
            )
            # Update image and subtitle preview while seeking
            self._update_preview_content(position_ms)
            
    def _request_prefetch(self, current_idx: int):
        """Request prefetch for a range of images around the current one"""
        if not self.image_clips:
            return
            
        self._last_prefetch_idx = current_idx
        
        # Bidirectional prefetch: 2 before, current, 2 after (total 5)
        start_idx = max(0, current_idx - 2)
        end_idx = min(len(self.image_clips), current_idx + 3)
        
        target_clips = self.image_clips[start_idx : end_idx]
        paths = [c['path'] for c in target_clips]
        
        if paths:
            self._image_cache.prefetch_images(paths)
            
    def _do_deferred_prefetch(self):
        """Perform prefetch for the current position (used when scrubbing stops)"""
        pos_sec = self.audio_mixer.position_ms / 1000.0
        current_idx = -1
        for i, clip in enumerate(self.image_clips):
            if clip['start'] <= pos_sec <= clip['end']:
                current_idx = i
                break
        
        if current_idx != -1:
            self._request_prefetch(current_idx)
    
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
        self.showing_placeholder = True
        self.subtitle_label.hide()
        self.subtitle_label.setText("")
        self.time_label.setText("0:00 / 0:00")
        self.seek_slider.setValue(0)
        self.status_label.setText("대기 중")
        self.status_label.setStyleSheet("color: gray;")
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
    
    def closeEvent(self, event):
        """Handle widget close"""
        self.cleanup()
        super().closeEvent(event)

    def cleanup(self):
        """Cleanup resources"""
        # Disconnect image cache signal to prevent signals to deleted widget
        try:
            self._image_cache.image_loaded.disconnect(self._on_image_loaded)
        except (TypeError, RuntimeError):
            pass  # Already disconnected or object deleted
        
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
