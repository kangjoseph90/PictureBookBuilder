"""
Timeline Widget - Visual timeline editor with waveform display and playhead
"""
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, 
    QLabel, QMenu
)
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, 
    QMouseEvent, QWheelEvent, QPaintEvent, QPainterPath, QCursor, QPixmap
)


@dataclass
class TimelineClip:
    """A clip on the timeline"""
    id: str
    name: str
    start: float  # Timeline position (when it plays)
    duration: float  # seconds
    track: int
    color: QColor
    clip_type: str = "audio"  # "audio", "image", or "subtitle"
    waveform: list = field(default_factory=list)  # Normalized amplitude samples (0-1)
    image_path: Optional[str] = None  # Path to image file for thumbnails
    
    # Source audio info (for trimming/editing)
    source_start: float = 0.0  # Start time in original audio
    source_end: float = 0.0    # End time in original audio
    segment_index: int = -1    # Index in result_data['aligned']
    speaker: str = ""          # Speaker name for audio lookup
    words: list = field(default_factory=list)  # Word timestamps for subtitle editing
    
    @property
    def end(self) -> float:
        return self.start + self.duration


class TimelineCanvas(QWidget):
    """Canvas widget for drawing the timeline with playhead"""
    
    clip_selected = pyqtSignal(str)  # Emits clip id
    clip_moved = pyqtSignal(str, float)  # Emits clip id and new start time
    clip_editing = pyqtSignal(str)  # Emits during dragging/editing
    clip_edited = pyqtSignal(str)  # Emits clip id when source boundaries changed
    clip_double_clicked = pyqtSignal(str)  # Emits clip id
    clip_context_menu = pyqtSignal(str, object)  # Emits clip id and QPoint for context menu
    playhead_moved = pyqtSignal(float)  # Emits time in seconds
    
    EDGE_THRESHOLD = 8  # Pixels from edge to trigger resize
    SNAP_THRESHOLD = 10  # Pixels for snapping effect
    
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        
        # Timeline state
        self.clips: list[TimelineClip] = []
        self.zoom = 100.0  # pixels per second
        self.scroll_offset = 0.0
        self.gap_seconds = 0.5
        self.total_duration = 0.0  # Total timeline duration
        
        # Playhead
        self.playhead_time = 0.0  # Current playhead position in seconds
        self.dragging_playhead = False
        self.active_snap_time: Optional[float] = None  # Time where snapping is occurring
        
        # Track heights
        self.track_height = 50  # Height for waveform
        self.track_padding = 5
        self.header_height = 25
        
        # Interaction state
        self.selected_clip: Optional[str] = None
        self.dragging_clip: Optional[str] = None
        self.drag_start_x: float = 0
        self.drag_clip_start: float = 0
        self.drag_is_ripple: bool = False
        self.drag_initial_positions: dict[str, float] = {}
        
        # Edge resize state
        self.resizing_clip: Optional[str] = None
        self.resize_edge: str = ""  # "left" or "right"
        self.resize_start_x: float = 0
        self.resize_original_source_start: float = 0
        self.resize_original_source_end: float = 0
        self.resize_original_duration: float = 0
        self.resize_original_start: float = 0
        
        # Colors
        self.speaker_colors = [
            QColor("#E91E63"),  # Pink
            QColor("#2196F3"),  # Blue
            QColor("#4CAF50"),  # Green
            QColor("#FF9800"),  # Orange
            QColor("#9C27B0"),  # Purple
            QColor("#00BCD4"),  # Cyan
        ]
        self.bg_color = QColor("#1E1E1E")
        self.grid_color = QColor("#333333")
        self.text_color = QColor("#CCCCCC")
        self.playhead_color = QColor("#FF4444")
        self.pixmap_cache: dict[str, 'QPixmap'] = {}  # Cache for thumbnails
        
        # Audio cache for real-time waveform updates
        self.speaker_audio_cache: dict[str, 'AudioSegment'] = {}
        self.waveform_extractor = None  # Will be set by main_window
    
    def set_clips(self, clips: list[TimelineClip]):
        """Set the clips to display"""
        self.clips = clips
        self._update_total_duration()
        self.update()
    
    def get_snap_time(self, time: float, exclude_clip_id: str = None) -> float:
        """Find the nearest snap point for a given time"""
        snap_points = [0.0, self.playhead_time]
        
        for clip in self.clips:
            if exclude_clip_id and clip.id == exclude_clip_id:
                continue
            snap_points.append(clip.start)
            snap_points.append(clip.end)
            
        threshold_time = self.SNAP_THRESHOLD / self.zoom
        
        best_snap = time
        min_diff = threshold_time
        
        for snap_pt in snap_points:
            diff = abs(time - snap_pt)
            if diff < min_diff:
                min_diff = diff
                best_snap = snap_pt
                
        return best_snap

    def _update_total_duration(self):
        """Calculate total timeline duration"""
        if self.clips:
            self.total_duration = max(clip.end for clip in self.clips)
        else:
            self.total_duration = 60.0  # Default 1 minute if no clips
    
    def update_clip_waveform(self, clip_id: str):
        """Update waveform for a clip based on current source boundaries
        
        Args:
            clip_id: ID of the clip to update
        """
        for clip in self.clips:
            if clip.id == clip_id:
                if clip.clip_type == "audio" and self.waveform_extractor:
                    speaker = clip.speaker if clip.speaker else None
                    if not speaker and ":" in clip.name:
                        speaker = clip.name.split(":")[0].strip()
                    
                    if speaker and speaker in self.speaker_audio_cache:
                        try:
                            audio = self.speaker_audio_cache[speaker]
                            # Extract the segment based on current source boundaries
                            start_ms = int(clip.source_start * 1000)
                            end_ms = int(clip.source_end * 1000)
                            segment = audio[start_ms:end_ms]
                            
                            # Generate and update waveform
                            clip.waveform = self.waveform_extractor(segment)
                        except Exception as e:
                            print(f"Error updating waveform for clip {clip_id}: {e}")
                break
    
    def set_playhead(self, time: float, auto_scroll: bool = False):
        """Set playhead position
        
        Args:
            time: Playhead time in seconds
            auto_scroll: If True, scroll to keep playhead visible
        """
        self.playhead_time = max(0, time)  # Allow any positive time
        
        # Auto-scroll to keep playhead visible (only when explicitly requested)
        if auto_scroll:
            playhead_x = self.time_to_x(self.playhead_time)
            left_margin = 20  # Small margin from left edge
            
            if playhead_x > self.width() or playhead_x < 0:
                # Playhead is off screen, scroll so playhead is at left edge
                self.scroll_offset = self.playhead_time * self.zoom - left_margin
                self.scroll_offset = max(0, self.scroll_offset)
        
        self.update()
    
    def set_gap(self, gap_seconds: float):
        """Set the gap between clips"""
        self.gap_seconds = gap_seconds
        self._recalculate_positions()
        self.update()
    
    def _recalculate_positions(self):
        """Recalculate clip positions with uniform gaps"""
        if not self.clips:
            return
        
        # Sort clips by original start time within each track
        track_clips: dict[int, list[TimelineClip]] = {}
        for clip in self.clips:
            if clip.track not in track_clips:
                track_clips[clip.track] = []
            track_clips[clip.track].append(clip)
        
        for track_num, clips in track_clips.items():
            clips.sort(key=lambda c: c.start)
            current_time = 0.0
            for clip in clips:
                clip.start = current_time
                current_time = clip.end + self.gap_seconds
        
        self._update_total_duration()
    
    def get_color_for_speaker(self, speaker: str) -> QColor:
        """Get a consistent color for a speaker"""
        index = hash(speaker) % len(self.speaker_colors)
        return self.speaker_colors[index]
    
    def time_to_x(self, time: float) -> float:
        """Convert time (seconds) to x position"""
        return time * self.zoom - self.scroll_offset
    
    def x_to_time(self, x: float) -> float:
        """Convert x position to time (seconds)"""
        return (x + self.scroll_offset) / self.zoom
    
    def get_track_y(self, track: int) -> int:
        """Get y position for a track"""
        return self.header_height + track * (self.track_height + self.track_padding)
    
    def get_clip_at(self, x: float, y: float) -> Optional[TimelineClip]:
        """Get the clip at a given position"""
        for clip in self.clips:
            clip_x = self.time_to_x(clip.start)
            clip_width = clip.duration * self.zoom
            clip_y = self.get_track_y(clip.track)
            
            if (clip_x <= x <= clip_x + clip_width and 
                clip_y <= y <= clip_y + self.track_height):
                return clip
        return None
    
    def get_clip_edge_at(self, x: float, y: float) -> tuple[Optional[TimelineClip], str]:
        """Check if mouse is near a clip edge
        
        Returns:
            Tuple of (clip, edge) where edge is "left", "right", or ""
        """
        # Try to find an edge of the SELECTED clip first (prioritize current selection)
        if self.selected_clip:
            for clip in self.clips:
                if clip.id == self.selected_clip:
                    clip_x = self.time_to_x(clip.start)
                    clip_width = clip.duration * self.zoom
                    clip_y = self.get_track_y(clip.track)
                    
                    if clip_y <= y <= clip_y + self.track_height:
                        if abs(x - clip_x) <= self.EDGE_THRESHOLD:
                            return clip, "left"
                        if abs(x - (clip_x + clip_width)) <= self.EDGE_THRESHOLD:
                            return clip, "right"
                    break

        # Then check everything else
        for clip in self.clips:
            # Skip if already checked selected
            if clip.id == self.selected_clip:
                continue
                
            clip_x = self.time_to_x(clip.start)
            clip_width = clip.duration * self.zoom
            clip_y = self.get_track_y(clip.track)
            
            # Check if in vertical range
            if not (clip_y <= y <= clip_y + self.track_height):
                continue
            
            # Check left edge
            if abs(x - clip_x) <= self.EDGE_THRESHOLD:
                return clip, "left"
            
            # Check right edge
            if abs(x - (clip_x + clip_width)) <= self.EDGE_THRESHOLD:
                return clip, "right"
        
        return None, ""
    
    def paintEvent(self, event: QPaintEvent):
        """Paint the timeline"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background
        painter.fillRect(self.rect(), self.bg_color)
        
        # Draw time grid
        self._draw_grid(painter)
        
        # Draw clips
        for clip in self.clips:
            self._draw_clip(painter, clip)
        
        # Draw playhead (on top of everything)
        self._draw_playhead(painter)
        
        # Draw snap indicator
        if self.active_snap_time is not None:
            snap_x = int(self.time_to_x(self.active_snap_time))
            if 0 <= snap_x <= self.width():
                painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.PenStyle.DashLine))
                painter.drawLine(snap_x, 0, snap_x, self.height())
        
        painter.end()
    
    def _draw_grid(self, painter: QPainter):
        """Draw time grid lines and labels"""
        painter.setPen(QPen(self.grid_color))
        painter.setFont(QFont("Arial", 8))
        
        # Calculate visible time range
        start_time = max(0, self.x_to_time(0))
        end_time = self.x_to_time(self.width())
        
        # Draw second markers
        for t in range(int(start_time), int(end_time) + 1):
            x = self.time_to_x(t)
            painter.setPen(QPen(self.grid_color))
            painter.drawLine(int(x), self.header_height, int(x), self.height())
            
            # Time label
            minutes = t // 60
            seconds = t % 60
            label = f"{minutes}:{seconds:02d}"
            painter.setPen(QPen(self.text_color))
            painter.drawText(int(x) + 3, 15, label)
    
    def _draw_playhead(self, painter: QPainter):
        """Draw the playhead (current time indicator)"""
        x = self.time_to_x(self.playhead_time)
        
        # Draw playhead line
        painter.setPen(QPen(self.playhead_color, 2))
        painter.drawLine(int(x), 0, int(x), self.height())
        
        # Draw playhead handle (triangle at top)
        handle_path = QPainterPath()
        handle_path.moveTo(x - 6, 0)
        handle_path.lineTo(x + 6, 0)
        handle_path.lineTo(x, 10)
        handle_path.closeSubpath()
        
        painter.setBrush(QBrush(self.playhead_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(handle_path)
    
    def _draw_clip(self, painter: QPainter, clip: TimelineClip):
        """Draw a single clip with waveform"""
        x = self.time_to_x(clip.start)
        y = self.get_track_y(clip.track)
        width = clip.duration * self.zoom
        height = self.track_height
        
        if width < 1:
            return
        
        # Clip rectangle
        color = clip.color
        if clip.id == self.selected_clip:
            color = color.lighter(130)
        
        # Draw background
        painter.setBrush(QBrush(color.darker(180)))
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
        
        # Draw waveform if available
        if clip.waveform and len(clip.waveform) > 0:
            self._draw_waveform(painter, clip, x, y, width, height)
        
        # Draw thumbnail for image clips
        if clip.clip_type == "image" and clip.image_path:
            self._draw_thumbnail(painter, clip, x, y, width, height)
        
        # Clip label (on top of waveform)
        if width > 30:
            painter.setPen(QPen(Qt.GlobalColor.white))
            painter.setFont(QFont("Arial", 8))
            
            label = clip.name
            max_chars = int(width / 6)
            if max_chars > 3 and len(label) > max_chars:
                label = label[:max_chars-2] + ".."
            
            # For subtitle clips, center the text vertically
            if clip.clip_type == "subtitle":
                text_rect = QRectF(x + 2, y, width - 4, height)
                painter.setPen(QPen(QColor(0, 0, 0, 150)))
                painter.drawText(text_rect.adjusted(1, 1, 1, 1), 
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
                painter.setPen(QPen(Qt.GlobalColor.white))
                painter.drawText(text_rect, 
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            else:
                # Draw label with shadow for readability (top)
                text_rect = QRectF(x + 4, y + 2, width - 8, 14)
                painter.setPen(QPen(QColor(0, 0, 0, 150)))
                painter.drawText(text_rect.adjusted(1, 1, 1, 1), 
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
                painter.setPen(QPen(Qt.GlobalColor.white))
                painter.drawText(text_rect, 
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
    
    def _draw_thumbnail(self, painter: QPainter, clip: TimelineClip, 
                       x: float, y: float, width: float, height: float):
        """Draw a thumbnail at the start of the image clip"""
        if not clip.image_path:
            return
            
        # Get or load pixmap
        if clip.image_path not in self.pixmap_cache:
            try:
                pixmap = QPixmap(clip.image_path)
                if not pixmap.isNull():
                    # Scale to fit track height (maintaining aspect ratio)
                    # Use a slightly smaller height to leave padding
                    scaled = pixmap.scaledToHeight(int(height - 4), Qt.TransformationMode.SmoothTransformation)
                    self.pixmap_cache[clip.image_path] = scaled
                else:
                    self.pixmap_cache[clip.image_path] = None
            except Exception as e:
                print(f"Failed to load thumbnail: {e}")
                self.pixmap_cache[clip.image_path] = None
                
        pixmap = self.pixmap_cache.get(clip.image_path)
        if pixmap:
            # Draw at the start of the clip
            # Ensure we don't draw outside the clip boundaries
            thumb_width = pixmap.width()
            draw_width = min(thumb_width, int(width))
            
            if draw_width > 5:
                # Draw the pixmap (or a portion of it if the clip is too short)
                painter.drawPixmap(int(x + 2), int(y + 2), pixmap, 0, 0, draw_width, pixmap.height())

    def _draw_waveform(self, painter: QPainter, clip: TimelineClip, 
                       x: float, y: float, width: float, height: float):
        """Draw the waveform visualization for a clip - spans entire clip"""
        waveform = clip.waveform
        num_samples = len(waveform)
        
        if num_samples == 0 or width < 2:
            return
        
        # Center waveform in track (reduced top offset for better centering)
        wave_y = y + 4
        wave_height = height - 6
        center_y = wave_y + wave_height / 2
        
        # Create path for waveform
        path = QPainterPath()
        
        # Map waveform samples to pixel width
        points_top = []
        points_bottom = []
        
        for px in range(int(width)):
            # Calculate which sample corresponds to this pixel
            sample_idx = int((px / width) * num_samples)
            sample_idx = min(sample_idx, num_samples - 1)
            
            # Get amplitude value
            amp = waveform[sample_idx] if sample_idx < num_samples else 0
            
            amp_height = amp * (wave_height / 2) * 0.9
            
            points_top.append((x + px, center_y - amp_height))
            points_bottom.append((x + px, center_y + amp_height))
        
        if len(points_top) < 2:
            return
        
        # Build path
        path.moveTo(points_top[0][0], points_top[0][1])
        for px_x, px_y in points_top[1:]:
            path.lineTo(px_x, px_y)
        
        # Continue to bottom in reverse
        for px_x, px_y in reversed(points_bottom):
            path.lineTo(px_x, px_y)
        
        path.closeSubpath()
        
        # Draw filled waveform
        wave_color = QColor(clip.color.lighter(120))
        wave_color.setAlpha(200)
        painter.setBrush(QBrush(wave_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        
        # Draw center line
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawLine(int(x), int(center_y), int(x + width), int(center_y))
    
    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press"""
        x = event.position().x()
        y = event.position().y()
        
        # Check if clicking on header (playhead area)
        if y < self.header_height:
            self.dragging_playhead = True
            new_time = self.x_to_time(x)
            self.playhead_time = max(0, new_time)
            self.playhead_moved.emit(self.playhead_time)
            self.update()
            return
        
        # Check for edge resize first
        edge_clip, edge = self.get_clip_edge_at(x, y)
        if edge_clip and edge and event.button() == Qt.MouseButton.LeftButton:
            self.resizing_clip = edge_clip.id
            self.resize_edge = edge
            self.resize_start_x = x
            self.resize_original_source_start = edge_clip.source_start
            self.resize_original_source_end = edge_clip.source_end
            self.resize_original_duration = edge_clip.duration
            self.resize_original_start = edge_clip.start
            self.selected_clip = edge_clip.id
            self.clip_selected.emit(edge_clip.id)
            
            # Check for Ctrl key - linked boundary mode
            self.linked_clip = None
            self.linked_original_start = 0.0
            self.linked_original_duration = 0.0
            
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Find adjacent clip at this boundary
                boundary_time = edge_clip.start if edge == "left" else edge_clip.start + edge_clip.duration
                
                for clip in self.clips:
                    if clip.id == edge_clip.id:
                        continue
                    if clip.track != edge_clip.track:
                        continue
                    
                    # Check if this clip's edge touches our boundary
                    clip_start = clip.start
                    clip_end = clip.start + clip.duration
                    
                    if edge == "left" and abs(clip_end - boundary_time) < 0.01:
                        # Adjacent clip's right edge touches our left edge
                        self.linked_clip = clip
                        self.linked_original_start = clip.start
                        self.linked_original_duration = clip.duration
                        break
                    elif edge == "right" and abs(clip_start - boundary_time) < 0.01:
                        # Adjacent clip's left edge touches our right edge
                        self.linked_clip = clip
                        self.linked_original_start = clip.start
                        self.linked_original_duration = clip.duration
                        break
            
            self.update()
            return
        
        clip = self.get_clip_at(x, y)
        
        if clip:
            self.selected_clip = clip.id
            self.clip_selected.emit(clip.id)
            
            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging_clip = clip.id
                self.drag_start_x = x
                self.drag_clip_start = clip.start
                # Store all clip positions for ripple dragging
                self.drag_initial_positions = {c.id: c.start for c in self.clips}
            elif event.button() == Qt.MouseButton.RightButton:
                # Show context menu
                self.clip_context_menu.emit(clip.id, event.globalPosition().toPoint())
        else:
            self.selected_clip = None
            # Click on empty area - move playhead
            new_time = self.x_to_time(x)
            self.playhead_time = max(0, new_time)
            self.playhead_moved.emit(self.playhead_time)
            self.dragging_playhead = True
        
        self.update()
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move"""
        x = event.position().x()
        y = event.position().y()
        
        # Handle playhead dragging
        if self.dragging_playhead:
            new_time = self.x_to_time(x)
            self.playhead_time = max(0, new_time)
            self.playhead_moved.emit(self.playhead_time)
            self.update()
            return
        
        # Handle edge resizing
        if self.resizing_clip:
            dx = x - self.resize_start_x
            dt = dx / self.zoom
            
            for clip in self.clips:
                if clip.id == self.resizing_clip:
                    if self.resize_edge == "left":
                        # Trim start - adjust source_start and duration
                        new_source_start = self.resize_original_source_start + dt
                        
                    if clip.clip_type == "audio":
                        # Audio-specific logic: Trim source and update duration/start
                        new_source_start = self.resize_original_source_start + dt
                        
                        if self.resize_edge == "left":
                            # Trim start
                            target_start = self.resize_original_start - (self.resize_original_source_start - new_source_start)
                            snapped_start = self.get_snap_time(target_start, exclude_clip_id=clip.id)
                            
                            if snapped_start != target_start:
                                dt -= (target_start - snapped_start)
                                new_source_start = self.resize_original_source_start + dt
                                
                            new_source_start = max(0, min(new_source_start, clip.source_end - 0.1))
                            duration_change = self.resize_original_source_start - new_source_start
                            new_duration = self.resize_original_duration + duration_change
                            
                            if new_duration > 0.1:
                                clip.source_start = new_source_start
                                clip.duration = new_duration
                                clip.start = self.resize_original_start - duration_change
                        
                        elif self.resize_edge == "right":
                            # Trim end
                            new_source_end = self.resize_original_source_end + dt
                            target_end = self.resize_original_start + self.resize_original_duration + dt
                            snapped_end = self.get_snap_time(target_end, exclude_clip_id=clip.id)
                            
                            if snapped_end != target_end:
                                dt -= (target_end - snapped_end)
                                new_source_end = self.resize_original_source_end + dt
                                
                            new_source_end = max(clip.source_start + 0.1, new_source_end)
                            duration_change = new_source_end - self.resize_original_source_end
                            new_duration = self.resize_original_duration + duration_change
                            
                            if new_duration > 0.1:
                                clip.source_end = new_source_end
                                clip.duration = new_duration
                    else:
                        # Non-audio logic: Just change timeline duration/start
                        if self.resize_edge == "left":
                            target_start = self.resize_original_start + dt
                            snapped_start = self.get_snap_time(target_start, exclude_clip_id=clip.id)
                            if snapped_start != target_start:
                                dt = snapped_start - self.resize_original_start
                                
                            new_start = self.resize_original_start + dt
                            new_duration = self.resize_original_duration - dt
                            
                            if new_duration > 0.1:
                                clip.start = new_start
                                clip.duration = new_duration
                                
                        elif self.resize_edge == "right":
                            target_end = self.resize_original_start + self.resize_original_duration + dt
                            snapped_end = self.get_snap_time(target_end, exclude_clip_id=clip.id)
                            if snapped_end != target_end:
                                dt = snapped_end - (self.resize_original_start + self.resize_original_duration)
                                
                            new_duration = self.resize_original_duration + dt
                            if new_duration > 0.1:
                                clip.duration = new_duration
                    break
            
            # Update linked clip if Ctrl+drag mode
            if self.linked_clip is not None:
                if self.resize_edge == "left":
                    # Our left edge moved, linked clip's right edge should follow
                    # dt > 0 means we moved right, linked clip should expand
                    new_linked_duration = self.linked_original_duration + dt
                    if new_linked_duration > 0.1:
                        self.linked_clip.duration = new_linked_duration
                elif self.resize_edge == "right":
                    # Our right edge moved, linked clip's left edge should follow
                    # dt > 0 means we moved right, linked clip should shrink and shift
                    new_linked_start = self.linked_original_start + dt
                    new_linked_duration = self.linked_original_duration - dt
                    if new_linked_duration > 0.1:
                        self.linked_clip.start = new_linked_start
                        self.linked_clip.duration = new_linked_duration
            
            # Update snap indicator for resizing
            self.active_snap_time = None
            if self.resize_edge == "left" and snapped_start != target_start:
                self.active_snap_time = snapped_start
            elif self.resize_edge == "right" and snapped_end != target_end:
                self.active_snap_time = snapped_end
            
            # Update waveform in real-time during resizing
            self.update_clip_waveform(self.resizing_clip)
                
            self.clip_editing.emit(self.resizing_clip)
            self.update()
            return
        
        # Handle clip dragging
        if self.dragging_clip:
            dx = x - self.drag_start_x
            dt = dx / self.zoom
            
            # Find the dragged clip
            dragged_clip = None
            for clip in self.clips:
                if clip.id == self.dragging_clip:
                    dragged_clip = clip
                    break
            
            if dragged_clip:
                # Check for ripple modifiers
                is_ripple_all = event.modifiers() & Qt.KeyboardModifier.ControlModifier  # All tracks
                is_ripple_track = event.modifiers() & Qt.KeyboardModifier.ShiftModifier  # Same track only
                
                new_start = self.drag_clip_start + dt
                # Apply snapping to the start of the dragged clip
                snapped_start = self.get_snap_time(new_start, exclude_clip_id=dragged_clip.id)
                
                # Also check if the end of the clip snaps to something
                snapped_end = self.get_snap_time(new_start + dragged_clip.duration, exclude_clip_id=dragged_clip.id)
                
                if snapped_start != new_start:
                    final_start = snapped_start
                elif snapped_end != (new_start + dragged_clip.duration):
                    final_start = snapped_end - dragged_clip.duration
                else:
                    final_start = new_start
                
                final_start = max(0, final_start)
                actual_dt = final_start - self.drag_clip_start
                
                if is_ripple_all:
                    # Ctrl: Move all subsequent clips across ALL tracks
                    for clip in self.clips:
                        initial_start = self.drag_initial_positions.get(clip.id, clip.start)
                        if initial_start >= self.drag_clip_start - 0.001:
                            clip.start = max(0, initial_start + actual_dt)
                elif is_ripple_track:
                    # Shift: Move all subsequent clips in SAME TRACK only
                    for clip in self.clips:
                        if clip.track != dragged_clip.track:
                            continue
                        initial_start = self.drag_initial_positions.get(clip.id, clip.start)
                        if initial_start >= self.drag_clip_start - 0.001:
                            clip.start = max(0, initial_start + actual_dt)
                else:
                    dragged_clip.start = final_start
            
                # Update snap indicator
                if final_start != new_start:
                    self.active_snap_time = final_start
                elif snapped_end != (new_start + dragged_clip.duration):
                    self.active_snap_time = snapped_end
                else:
                    self.active_snap_time = None
                
            self.update()
            return
        
        # Update cursor based on edge proximity
        edge_clip, edge = self.get_clip_edge_at(x, y)
        if edge_clip and edge:
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release"""
        if self.dragging_playhead:
            self.dragging_playhead = False
            return
        
        if self.resizing_clip:
            # Emit signal that clip was edited
            self.clip_edited.emit(self.resizing_clip)
            self.resizing_clip = None
            self.resize_edge = ""
            self.active_snap_time = None
            return
        
        if self.dragging_clip:
            for clip in self.clips:
                if clip.id == self.dragging_clip:
                    # Only emit if actually moved
                    if abs(clip.start - self.drag_clip_start) > 0.001:
                        self.clip_moved.emit(clip.id, clip.start)
                    break
            self.dragging_clip = None
            self.active_snap_time = None
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Handle mouse double click - for editing subtitles"""
        x = event.position().x()
        y = event.position().y()
        
        clip = self.get_clip_at(x, y)
        if clip:
            self.clip_double_clicked.emit(clip.id)
            
    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom
            delta = event.angleDelta().y() / 120
            old_zoom = self.zoom
            self.zoom = max(20, min(500, self.zoom * (1.1 ** delta)))
            
            # Adjust scroll to keep mouse position stable
            mouse_time = self.x_to_time(event.position().x())
            self.scroll_offset += mouse_time * (self.zoom - old_zoom)
        else:
            # Scroll
            self.scroll_offset -= event.angleDelta().x() + event.angleDelta().y()
            self.scroll_offset = max(0, self.scroll_offset)
        
        self.update()


class TimelineWidget(QWidget):
    """Timeline editor widget with controls"""
    
    playhead_changed = pyqtSignal(float)  # Emits time in seconds
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Track labels on the left, canvas on the right
        content_layout = QHBoxLayout()
        content_layout.setSpacing(0)
        
        # Track labels
        self.track_labels = QWidget()
        self.track_labels.setStyleSheet("background-color: #252525;")
        track_labels_layout = QVBoxLayout(self.track_labels)
        track_labels_layout.setContentsMargins(5, 25, 5, 0)
        
        audio_label = QLabel("🎵 Audio")
        audio_label.setStyleSheet("color: #ccc;")
        track_labels_layout.addWidget(audio_label)
        track_labels_layout.addSpacing(45)
        
        subtitle_label = QLabel("💬 Subs")
        subtitle_label.setStyleSheet("color: #ccc;")
        track_labels_layout.addWidget(subtitle_label)
        track_labels_layout.addSpacing(45)
        
        image_label = QLabel("🖼️ Images")
        image_label.setStyleSheet("color: #ccc;")
        track_labels_layout.addWidget(image_label)
        track_labels_layout.addStretch()
        
        self.track_labels.setFixedWidth(70)
        content_layout.addWidget(self.track_labels)
        
        # Timeline canvas
        self.canvas = TimelineCanvas()
        self.canvas.playhead_moved.connect(self._on_playhead_moved)
        content_layout.addWidget(self.canvas, 1)
        
        layout.addLayout(content_layout)
    
    def _on_playhead_moved(self, time: float):
        """Handle playhead movement from canvas"""
        self.playhead_changed.emit(time)
    
    def set_playhead(self, time: float, auto_scroll: bool = False):
        """Set playhead position from external source"""
        self.canvas.set_playhead(time, auto_scroll=auto_scroll)
    
    def set_clips(self, clips: list[TimelineClip]):
        """Set the clips to display"""
        self.canvas.set_clips(clips)
    
    def set_gap(self, gap_seconds: float):
        """Set the gap between clips"""
        self.canvas.set_gap(gap_seconds)
    
    def add_clip(
        self,
        clip_id: str,
        name: str,
        start: float,
        duration: float,
        track: int = 0,
        speaker: str = "",
        clip_type: str = "audio",
        waveform: list = None
    ):
        """Add a clip to the timeline"""
        color = self.canvas.get_color_for_speaker(speaker)
        clip = TimelineClip(
            id=clip_id,
            name=name,
            start=start,
            duration=duration,
            track=track,
            color=color,
            clip_type=clip_type,
            waveform=waveform or []
        )
        self.canvas.clips.append(clip)
        self.canvas._update_total_duration()
        self.canvas.update()
