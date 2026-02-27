"""
Timeline Widget - Visual timeline editor with waveform display and playhead
"""
from dataclasses import dataclass, field
from typing import Optional, List
from collections import OrderedDict
import copy
import os
from pathlib import Path
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, 
    QLabel, QMenu, QScrollBar, QToolTip
)
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QTimer, QMimeData, QUrl
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, 
    QMouseEvent, QWheelEvent, QPaintEvent, QPainterPath, QCursor, QPixmap,
    QDragEnterEvent, QDragMoveEvent, QDropEvent
)

from .clip import TimelineClip
from .image_cache import get_image_cache

class TimelineCanvas(QWidget):
    """Canvas widget for drawing the timeline with playhead"""
    
    clip_selected = pyqtSignal(str)  # Emits clip id
    clip_moved = pyqtSignal(str, float)  # Emits clip id and new start time
    clip_editing = pyqtSignal(str)  # Emits during dragging/editing
    clip_edited = pyqtSignal(str)  # Emits clip id when source boundaries changed
    clip_double_clicked = pyqtSignal(str)  # Emits clip id
    clip_context_menu = pyqtSignal(str, object)  # Emits clip id and QPoint for context menu
    clip_delete_requested = pyqtSignal(str)  # Emits clip id when delete key pressed
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    split_requested = pyqtSignal()
    playhead_moved = pyqtSignal(float)  # Emits time in seconds
    image_dropped = pyqtSignal(str, float)  # Emits image path and timeline position
    view_changed = pyqtSignal()  # Emits when zoom/scroll/size changes
    
    # NEW: Signal to notify command generation
    # action_type: 'move', 'resize', etc.
    # data: dictionary with relevant data to construct the command
    history_command_generated = pyqtSignal(str, dict)

    EDGE_THRESHOLD = 8  # Pixels from edge to trigger resize
    SNAP_THRESHOLD = 10  # Pixels for snapping effect
    
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)  # Enable drop events for drag-and-drop
        
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
        self.header_height = 30 # Increased for ruler ticks
        
        # Interaction state
        self.selected_clip: Optional[str] = None
        self.selected_clips: list[str] = []
        self.dragging_clip: Optional[str] = None
        self.drag_start_x: float = 0
        self.drag_clip_start: float = 0
        self.drag_is_ripple: bool = False
        self.drag_group_clip_ids: list[str] = []
        self.drag_initial_positions: dict[str, float] = {}
        
        # State tracking for Undo/Redo
        self.drag_start_state: dict[str, TimelineClip] = {} # Map ID -> Copy of Clip

        # Edge resize state
        self.resizing_clip: Optional[str] = None
        self.resize_edge: str = ""  # "left" or "right"
        self.resize_start_x: float = 0
        self.resize_original_offset: float = 0    # Original offset in source audio
        self.resize_original_duration: float = 0
        self.resize_original_start: float = 0
        
        # Undo state for resize
        self.resize_start_state: Optional[TimelineClip] = None
        self.linked_clip_start_state: Optional[TimelineClip] = None

        # Volume dragging state
        self.dragging_volume: bool = False
        self.volume_drag_start_val: float = 0.0
        self.volume_drag_start_y: float = 0.0
        self.volume_clip_id: Optional[str] = None

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
        
        # Audio cache for real-time waveform updates
        self.speaker_audio_cache: dict[str, 'AudioSegment'] = {}
        self.speaker_audio_map: dict[str, str] = {}  # Map speaker to file path for missing file detection
        self.waveform_extractor = None  # Will be set by main_window
        
        # Waveform rendering optimization
        self._waveform_path_cache: OrderedDict[str, QPainterPath] = OrderedDict()  # Cache QPainterPath for each clip
        self._max_waveform_cache_size = 1000  # Prevent memory leaks from zooming
        
        # Image cache for thumbnails (pre-generated)
        self._image_cache = get_image_cache()
        
        # Render caching
        self._cached_background: Optional[QPixmap] = None
        self._background_dirty: bool = True

        # Throttling for mouse move events (to reduce CPU usage during fast dragging)
        self._update_throttle_timer = QTimer(self)
        self._update_throttle_timer.setSingleShot(True)
        self._update_throttle_timer.timeout.connect(self._process_pending_update)
        self._update_interval_ms = 16  # ~60fps max update rate
        self._pending_update = False
        self._pending_waveform_clip_id: Optional[str] = None

        # Drag-box selection state
        self.dragging_selection_box: bool = False
        self.selection_box_start: Optional[tuple[float, float]] = None
        self.selection_box_end: Optional[tuple[float, float]] = None
        self.selection_box_additive: bool = False
    
    def set_clips(self, clips: list[TimelineClip]):
        """Set the clips to display"""
        self.clips = clips
        clip_ids = {c.id for c in self.clips}
        self.selected_clips = [cid for cid in self.selected_clips if cid in clip_ids]
        if self.selected_clip and self.selected_clip not in clip_ids:
            self.selected_clip = self.selected_clips[0] if self.selected_clips else None
        elif self.selected_clip and self.selected_clip not in self.selected_clips:
            self.selected_clips.append(self.selected_clip)
        self._update_total_duration()
        self._background_dirty = True
        self.update()
        self.view_changed.emit()

    def set_selected_clip_ids(self, clip_ids: list[str]):
        """Set selected clips while keeping selected_clip in sync."""
        valid_ids = {c.id for c in self.clips}
        unique_ids = [cid for cid in dict.fromkeys(clip_ids) if cid in valid_ids]
        self.selected_clips = unique_ids
        self.selected_clip = unique_ids[0] if unique_ids else None
        if self.selected_clip:
            self.clip_selected.emit(self.selected_clip)
        self._background_dirty = True
        self.update()

    def _select_following_clips(self, anchor_clip: TimelineClip, same_track_only: bool):
        """Select clips from anchor position to the right.

        Args:
            anchor_clip: Clicked clip used as selection anchor.
            same_track_only: If True, limit to anchor clip track.
        """
        if same_track_only:
            selected = [
                c.id for c in sorted(self.clips, key=lambda c: c.start)
                if c.track == anchor_clip.track and c.start >= anchor_clip.start - 0.001
            ]
        else:
            selected = [
                c.id for c in sorted(self.clips, key=lambda c: (c.start, c.track))
                if c.start >= anchor_clip.start - 0.001
            ]

        self.selected_clips = selected
        self.selected_clip = anchor_clip.id if anchor_clip.id in selected else (selected[0] if selected else None)
        if self.selected_clip:
            self.clip_selected.emit(self.selected_clip)
        self._background_dirty = True
        self.update()

    def select_following_from_clip_id(self, clip_id: str, same_track_only: bool):
        """Public helper for selecting following clips from a given clip id."""
        anchor = next((c for c in self.clips if c.id == clip_id), None)
        if not anchor:
            return
        self._select_following_clips(anchor, same_track_only=same_track_only)
    
    def _schedule_throttled_update(self, waveform_clip_id: Optional[str] = None):
        """Schedule a throttled update to reduce CPU usage during fast dragging.
        
        Args:
            waveform_clip_id: If provided, also update this clip's waveform
        """
        self._pending_update = True
        if waveform_clip_id:
            self._pending_waveform_clip_id = waveform_clip_id
        
        # If timer is not running, start it
        if not self._update_throttle_timer.isActive():
            self._update_throttle_timer.start(self._update_interval_ms)
    
    def _process_pending_update(self):
        """Process pending update when throttle timer fires."""
        if self._pending_update:
            # Update waveform if needed
            if self._pending_waveform_clip_id:
                self.update_clip_waveform(self._pending_waveform_clip_id)
                self._pending_waveform_clip_id = None
            
            self._pending_update = False
            self._background_dirty = True
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
        """Update waveform for a clip based on current offset and duration
        
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
                            # Calculate original segment duration
                            padded_duration_ms = int(clip.duration * 1000)
                            segment_duration_ms = padded_duration_ms
                            segment_end = clip.offset + (segment_duration_ms / 1000.0)
                            
                            start_ms = max(0, int(clip.offset * 1000))
                            end_ms = min(len(audio), int(segment_end * 1000))
                            segment = audio[start_ms:end_ms]
                            
                            # Generate and update waveform
                            clip.waveform = self.waveform_extractor(segment)
                            
                            # Invalidate cached path for this clip
                            if clip_id in self._waveform_path_cache:
                                del self._waveform_path_cache[clip_id]

                            self._background_dirty = True
                        except Exception as e:
                            print(f"Error updating waveform for clip {clip_id}: {e}")
                break
    
    def set_playhead(self, time: float, auto_scroll: bool = False):
        """Set playhead position
        
        Args:
            time: Playhead time in seconds
            auto_scroll: If True, scroll to keep playhead visible
        """
        old_scroll = self.scroll_offset
        self.playhead_time = max(0, time)  # Allow any positive time
        
        # Auto-scroll to keep playhead visible (only when explicitly requested)
        if auto_scroll:
            playhead_x = self.time_to_x(self.playhead_time)
            left_margin = 20  # Small margin from left edge
            
            if playhead_x > self.width() or playhead_x < 0:
                # Playhead is off screen, scroll so playhead is at left edge
                self.scroll_offset = self.playhead_time * self.zoom - left_margin
                self.scroll_offset = max(0, self.scroll_offset)

        if self.scroll_offset != old_scroll:
            self._background_dirty = True
            self.view_changed.emit()
        
        self.update()
    
    def set_gap(self, gap_seconds: float):
        """Set the gap between clips"""
        self.gap_seconds = gap_seconds
        self._recalculate_positions()
        self._background_dirty = True
        self.update()
        self.view_changed.emit()
    
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
        # Check selected clip first (conceptually on top)
        if self.selected_clip:
            for clip in self.clips:
                if clip.id == self.selected_clip:
                    clip_x = self.time_to_x(clip.start)
                    clip_width = clip.duration * self.zoom
                    clip_y = self.get_track_y(clip.track)

                    if (clip_x <= x <= clip_x + clip_width and
                        clip_y <= y <= clip_y + self.track_height):
                        return clip
                    break

        # Iterate in reverse so the top-most (last drawn) clip wins when overlapping.
        for clip in reversed(self.clips):
            if clip.id == self.selected_clip:
                continue

            clip_x = self.time_to_x(clip.start)
            clip_width = clip.duration * self.zoom
            clip_y = self.get_track_y(clip.track)
            
            if (clip_x <= x <= clip_x + clip_width and 
                clip_y <= y <= clip_y + self.track_height):
                return clip
        return None
    
    def get_clips_at(self, x: float, y: float) -> list[TimelineClip]:
        """Get all clips at a given position"""
        hit_clips = []
        # Iterate in reverse order (top to bottom visual)
        for clip in reversed(self.clips):
            clip_x = self.time_to_x(clip.start)
            clip_width = clip.duration * self.zoom
            clip_y = self.get_track_y(clip.track)
            
            if (clip_x <= x <= clip_x + clip_width and 
                clip_y <= y <= clip_y + self.track_height):
                hit_clips.append(clip)
        return hit_clips

    def get_clip_ids_in_rect(self, rect: QRectF) -> list[str]:
        """Get clip IDs intersecting a selection rectangle."""
        selected = []
        for clip in self.clips:
            clip_rect = QRectF(
                self.time_to_x(clip.start),
                self.get_track_y(clip.track),
                clip.duration * self.zoom,
                self.track_height
            )
            if rect.intersects(clip_rect):
                selected.append(clip.id)
        return selected
    
    def get_clip_edge_at(self, x: float, y: float) -> tuple[Optional[TimelineClip], str]:
        """Check if mouse is near a clip edge
        
        Returns:
            Tuple of (clip, edge) where edge is "left", "right", or ""
        """
        best_clip = None
        best_edge = ""
        min_dist = self.EDGE_THRESHOLD + 0.1 # Strictly less than threshold to be valid

        # Iterate all clips to find the CLOSEST edge
        # We don't prioritize selected clip anymore, just distance.
        # Reverse order means if distances are equal, we pick the one "on top" (last in list)
        for clip in reversed(self.clips):
            clip_x = self.time_to_x(clip.start)
            clip_width = clip.duration * self.zoom
            clip_y = self.get_track_y(clip.track)
            
            # Check if in vertical range
            if not (clip_y <= y <= clip_y + self.track_height):
                continue
            
            # Determine if mouse is "inside" the clip (visually)
            # This is used as a tie-breaker: if distances are similar, prefer the clip
            # that physically contains the mouse cursor.
            is_inside = clip_x <= x <= (clip_x + clip_width)
            bias = 0.001 if is_inside else 0.0

            # Check left edge
            dist_left = abs(x - clip_x)
            # Use effective distance (actual distance - bias)
            # If inside, effective distance is slightly smaller, winning the tie.
            if (dist_left - bias) < min_dist:
                min_dist = dist_left - bias
                best_clip = clip
                best_edge = "left"
            
            # Check right edge
            dist_right = abs(x - (clip_x + clip_width))
            if (dist_right - bias) < min_dist:
                min_dist = dist_right - bias
                best_clip = clip
                best_edge = "right"
        
        return best_clip, best_edge
    
    def resizeEvent(self, event):
        self._background_dirty = True
        super().resizeEvent(event)
        self.view_changed.emit()

    def _update_background_cache(self):
        """Update the cached background pixmap"""
        if self.width() <= 0 or self.height() <= 0:
            return

        # High-DPI support: multiply size by device pixel ratio
        dpr = self.devicePixelRatio()
        self._cached_background = QPixmap(self.size() * dpr)
        self._cached_background.setDevicePixelRatio(dpr)
        self._cached_background.fill(Qt.GlobalColor.transparent)

        painter = QPainter(self._cached_background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background
        painter.fillRect(self.rect(), self.bg_color)
        
        # Draw time grid
        self._draw_grid(painter)
        
        # Calculate visible time range for culling
        visible_start = self.x_to_time(0)
        visible_end = self.x_to_time(self.width())
        
        # Draw clips (only visible ones)
        # Draw all clips in their natural layering (order in clips list)
        for clip in self.clips:
            # Cull clips outside visible range
            if clip.start + clip.duration < visible_start:
                continue
            if clip.start > visible_end:
                continue
            
            self._draw_clip(painter, clip)
        
        # 3. Draw overlaps on top of clips
        self._draw_overlaps(painter)

        # 4. Draw selection highlight on top of everything
        if self.selected_clip:
            self._draw_selection_highlight(painter)

        painter.end()
        self._background_dirty = False

    def paintEvent(self, event: QPaintEvent):
        """Paint the timeline"""
        # Early return for zero-size widget
        if self.width() <= 0 or self.height() <= 0:
            return
        
        # Check if we need to update the cache
        # Note: Size check handles resize events roughly, but dpr changes should also trigger invalidation ideally.
        # However, dpr changes usually come with window moves/resizes which trigger paint events.
        if (self._background_dirty or 
            self._cached_background is None or 
            self._cached_background.size() != self.size() * self.devicePixelRatio()):
            self._update_background_cache()


        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw cached background
        if self._cached_background:
            painter.drawPixmap(0, 0, self._cached_background)
        
        # Draw playhead (on top of everything)
        self._draw_playhead(painter)
        
        # Draw snap indicator
        if self.active_snap_time is not None:
            snap_x = int(self.time_to_x(self.active_snap_time))
            if 0 <= snap_x <= self.width():
                painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.PenStyle.DashLine))
                painter.drawLine(snap_x, 0, snap_x, self.height())

        self._draw_selection_box(painter)
        
        painter.end()
    
    def _draw_grid(self, painter: QPainter):
        """Draw time grid lines and labels with dynamic intervals (ruler)"""
        # Draw header background
        painter.fillRect(0, 0, self.width(), self.header_height, QColor("#2D2D2D"))
        painter.setPen(QPen(QColor("#3E3E42"), 1))
        painter.drawLine(0, self.header_height, self.width(), self.header_height)
        
        # Calculate optimal step based on zoom (pixels per second)
        # We want labels to have at least ~80px of space
        min_label_width = 80
        possible_steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600]
        
        step = possible_steps[-1]
        for s in possible_steps:
            if s * self.zoom >= min_label_width:
                step = s
                break
        
        # Minor step for smaller ticks (ruler style)
        if step >= 60: minor_step = 10
        elif step >= 10: minor_step = 1
        elif step >= 1: minor_step = 0.5
        else: minor_step = step / 5
            
        painter.setFont(QFont("Segoe UI", 8))
        
        # Calculate visible time range
        start_time = self.x_to_time(0)
        end_time = self.x_to_time(self.width())
        
        # Start at the first multiple of minor_step
        t = (max(0.0, start_time) // minor_step) * minor_step
        
        while t <= end_time:
            x = self.time_to_x(t)
            
            # Use epsilon for float modulo/division checks
            is_major = abs(t / step - round(t / step)) < 0.0001
            
            if is_major:
                # Major tick (longer)
                painter.setPen(QPen(QColor("#888888"), 1))
                painter.drawLine(int(x), self.header_height - 15, int(x), self.header_height)
                
                # Grid line (full height)
                painter.setPen(QPen(self.grid_color))
                painter.drawLine(int(x), self.header_height, int(x), self.height())
                
                # Time label
                minutes = int(t // 60)
                seconds = t % 60
                if step < 1:
                    label = f"{minutes}:{seconds:04.1f}"
                else:
                    label = f"{minutes}:{int(seconds):02d}"
                
                painter.setPen(QPen(self.text_color))
                painter.drawText(int(x) + 4, 15, label)
            else:
                # Minor tick (shorter)
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(int(x), self.header_height - 8, int(x), self.header_height)
            
            t += minor_step
    
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
    
    def _is_clip_source_missing(self, clip: TimelineClip) -> bool:
        """Check if the source file for the clip is missing"""
        if clip.clip_type == "image":
            if clip.image_path:
                return not os.path.exists(clip.image_path)
            return True  # No path means missing
        elif clip.clip_type == "audio":
            # For audio, check speaker map
            speaker = clip.speaker
            if not speaker and ":" in clip.name:
                speaker = clip.name.split(":")[0].strip()

            if speaker:
                path = self.speaker_audio_map.get(speaker)
                if path:
                    return not os.path.exists(path)
                return True # No path mapped means missing
        return False

    def _draw_clip(self, painter: QPainter, clip: TimelineClip):
        """Draw a single clip with waveform"""
        x = self.time_to_x(clip.start)
        y = self.get_track_y(clip.track)
        width = clip.duration * self.zoom
        height = self.track_height
        
        if width < 1:
            return
        
        # Check for missing file
        is_missing = self._is_clip_source_missing(clip)

        # Clip rectangle
        color = clip.color
        if clip.id in self.selected_clips:
            color = color.lighter(130)
        
        # Draw background
        if is_missing:
            # Yellow diagonal pattern for warning
            painter.setBrush(QBrush(QColor(255, 200, 0, 100), Qt.BrushStyle.DiagCrossPattern))
            painter.setPen(QPen(QColor(255, 200, 0), 2))
        else:
            painter.setBrush(QBrush(color.darker(180)))
            painter.setPen(QPen(color, 1))

        painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
        
        # Draw waveform only for audio clips (if not missing)
        if not is_missing and clip.clip_type == "audio":
            if clip.waveform and len(clip.waveform) > 0:
                self._draw_waveform(painter, clip, x, y, width, height)
            # Only draw volume line if clip is selected
            if clip.id in self.selected_clips:
                self._draw_volume_line(painter, clip, x, y, width, height)
        
        # Draw thumbnail for image clips (if not missing)
        if not is_missing and clip.clip_type == "image" and clip.image_path:
            self._draw_thumbnail(painter, clip, x, y, width, height)
        
        # Warning Text for missing files
        if is_missing and width > 20:
             painter.setPen(QPen(QColor(255, 200, 0)))
             painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
             painter.drawText(QRectF(x, y, width, height),
                              Qt.AlignmentFlag.AlignCenter, "⚠️ MISSING")

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
    
    def _draw_overlaps(self, painter: QPainter):
        """Draw red overlay on overlapping clip regions"""
        tracks: dict[int, list[TimelineClip]] = {}
        for clip in self.clips:
            if clip.track not in tracks:
                tracks[clip.track] = []
            tracks[clip.track].append(clip)
        
        # Style for overlap highlight - rounded to match clip aesthetic
        painter.setPen(QPen(QColor(255, 80, 80, 150), 1))
        painter.setBrush(QColor(255, 50, 50, 80))  # Semi-transparent red
        
        # Epsilon for floating point comparison (1ms tolerance)
        EPSILON = 0.001
        
        for track, track_clips in tracks.items():
            if len(track_clips) < 2:
                continue
            
            # Create events: (time, type) where type -1=end, 1=start
            events = []
            for clip in track_clips:
                events.append((clip.start, 1))
                events.append((clip.start + clip.duration, -1))
            
            # Process END (-1) before START (1) to NOT count touching as overlap
            events.sort(key=lambda x: (x[0], x[1]))
            
            active_count = 0
            last_time = None
            
            idx = 0
            while idx < len(events):
                time = events[idx][0]
                
                # If we were in an overlap state, draw the segment from last event to current event
                # Only draw if the overlap region is larger than epsilon (avoid false positives from touching clips)
                if active_count >= 2 and last_time is not None and (time - last_time) > EPSILON:
                    x = self.time_to_x(last_time)
                    # Subtract a tiny amount from width if we want a gap, 
                    # but usually just drawing two rounded rects at the same boundary
                    # will create the visual separation naturally.
                    width = (time - last_time) * self.zoom
                    y = self.get_track_y(track)
                    height = self.track_height
                    painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
                
                # Process all events at approximately the same timestamp (within epsilon) to get net change
                # This groups events that should be at the same time but differ due to float precision
                while idx < len(events) and abs(events[idx][0] - time) < EPSILON:
                    active_count += events[idx][1]
                    idx += 1
                
                last_time = time

    def _draw_selection_highlight(self, painter: QPainter):
        """Draw a bold selection outline on top of everything"""
        if not self.selected_clips:
            return

        selected_ids = set(self.selected_clips)
        for clip in self.clips:
            if clip.id in selected_ids:
                x = self.time_to_x(clip.start)
                y = self.get_track_y(clip.track)
                width = clip.duration * self.zoom
                height = self.track_height
                
                painter.save()
                painter.setBrush(Qt.BrushStyle.NoBrush)
                # Use a thick white pen for selection
                painter.setPen(QPen(Qt.GlobalColor.white, 2))
                painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
                painter.restore()

    def _draw_selection_box(self, painter: QPainter):
        """Draw drag selection rectangle."""
        if not self.dragging_selection_box or not self.selection_box_start or not self.selection_box_end:
            return
        x1, y1 = self.selection_box_start
        x2, y2 = self.selection_box_end
        rect = QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        painter.save()
        painter.setBrush(QBrush(QColor(120, 170, 255, 40)))
        painter.setPen(QPen(QColor(120, 170, 255, 180), 1, Qt.PenStyle.DashLine))
        painter.drawRect(rect)
        painter.restore()

    def _draw_thumbnail(self, painter: QPainter, clip: TimelineClip, 
                       x: float, y: float, width: float, height: float):
        """Draw a thumbnail at the start of the image clip"""
        if not clip.image_path:
            return
        
        # Get from global cache (already optimized for timeline)
        pixmap = self._image_cache.get_thumbnail_timeline(clip.image_path)
        
        if pixmap and not pixmap.isNull():
            # Draw at the start of the clip, scaled to fit track height
            # We subtract padding to match original visual
            target_h = height - 4
            # Keep aspect ratio
            target_w = pixmap.width() * (target_h / pixmap.height())
            
            draw_width = min(target_w, width - 4)
            
            if draw_width > 5:
                # Use drawPixmap with target rectangle for automatic scaling
                target_rect = QRectF(x + 2, y + 2, draw_width, target_h)
                # Source rect: we only take what fits in draw_width
                source_w = pixmap.width() * (draw_width / target_w)
                source_rect = QRectF(0, 0, source_w, pixmap.height())
                
                painter.drawPixmap(target_rect, pixmap, source_rect)
                # For debugging:
                # print(f"[Timeline] Drove thumb: {Path(clip.image_path).name}")

    def _draw_volume_line(self, painter: QPainter, clip: TimelineClip,
                          x: float, y: float, width: float, height: float):
        """Draw interactive volume line on audio clip"""
        vol = getattr(clip, 'volume', 1.0)
        # Map 0.0 -> bottom, 1.0 -> 50%, 2.0 -> top
        y_ratio = 1.0 - (vol / 2.0)
        line_y = y + height * y_ratio

        # Clamp visual line to clip bounds
        line_y = max(y, min(y + height, line_y))

        # Draw line
        painter.save()
        painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
        painter.drawLine(int(x), int(line_y), int(x + width), int(line_y))

        # Draw text if not 1.0
        if abs(vol - 1.0) > 0.01 and width > 30:
            painter.setFont(QFont("Arial", 7))
            label = f"{vol:.2f}x"
            # Draw above line
            text_y = int(line_y - 2)
            if text_y < y + 12: # If too close to top, draw below
                text_y = int(line_y + 10)

            painter.setPen(QPen(QColor(255, 255, 255, 255)))
            painter.drawText(int(x + 5), text_y, label)
        painter.restore()

    def _draw_waveform(self, painter: QPainter, clip: TimelineClip, 
                       x: float, y: float, width: float, height: float):
        """Draw the waveform visualization for a clip - optimized with caching"""
        waveform = clip.waveform
        num_samples = len(waveform)
        
        # Skip very small clips (not worth rendering waveform)
        if num_samples == 0 or width < 10:
            return
        
        # Center waveform in track
        wave_y = y + 4
        wave_height = height - 6
        center_y = wave_y + wave_height / 2
        
        # Check if we have a cached path for this clip
        # Cache key must include offset and duration to handle resize correctly
        cache_key = f"{clip.id}_{int(width)}_{int(height)}_{clip.offset:.2f}_{clip.duration:.2f}"
        
        if cache_key in self._waveform_path_cache:
            # Use cached path and mark as recently used
            path = self._waveform_path_cache[cache_key]
            self._waveform_path_cache.move_to_end(cache_key)
        else:
            # Generate new path
            path = QPainterPath()
            
            # Adaptive sampling: limit samples to pixel width
            # This prevents unnecessary computation for zoomed-out views
            max_samples = min(int(width), num_samples)
            points_top = []
            points_bottom = []
            
            for px in range(max_samples):
                # Calculate which sample corresponds to this pixel
                sample_idx = int((px / max_samples) * num_samples)
                sample_idx = min(sample_idx, num_samples - 1)
                
                # Get amplitude value
                amp = waveform[sample_idx]
                amp_height = amp * (wave_height / 2) * 0.9
                
                # Scale px to actual width for proper positioning
                actual_x = (px / max_samples) * width
                points_top.append((actual_x, center_y - amp_height))
                points_bottom.append((actual_x, center_y + amp_height))
            
            if len(points_top) < 2:
                return
            
            # Build path (relative coordinates, will translate when drawing)
            path.moveTo(points_top[0][0], points_top[0][1])
            for px_x, px_y in points_top[1:]:
                path.lineTo(px_x, px_y)
            
            # Continue to bottom in reverse
            for px_x, px_y in reversed(points_bottom):
                path.lineTo(px_x, px_y)
            
            path.closeSubpath()
            
            # Cache the path with LRU eviction
            self._waveform_path_cache[cache_key] = path
            self._waveform_path_cache.move_to_end(cache_key)

            if len(self._waveform_path_cache) > self._max_waveform_cache_size:
                self._waveform_path_cache.popitem(last=False)  # Remove oldest
        
        # Draw the cached or newly created path
        painter.save()
        painter.translate(x, 0)  # Translate to clip position
        
        wave_color = QColor(clip.color.lighter(120))
        wave_color.setAlpha(200)
        painter.setBrush(QBrush(wave_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        
        # Draw center line
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawLine(0, int(center_y), int(width), int(center_y))
        
        painter.restore()
    
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
        
        # Check for volume line interaction
        if event.button() == Qt.MouseButton.LeftButton:
            vol_clip = self.get_clip_at(x, y)
            # Only allow interaction if clip is selected AND it is audio
            if vol_clip and vol_clip.clip_type == "audio" and vol_clip.id == self.selected_clip:
                vol = getattr(vol_clip, 'volume', 1.0)
                clip_y = self.get_track_y(vol_clip.track)
                # Map 0.0 -> bottom, 1.0 -> 50%, 2.0 -> top
                line_y = clip_y + self.track_height * (1.0 - vol / 2.0)
                line_y = max(clip_y, min(clip_y + self.track_height, line_y))

                if abs(y - line_y) <= 5:
                    self.dragging_volume = True
                    self.volume_clip_id = vol_clip.id
                    self.volume_drag_start_val = vol
                    self.volume_drag_start_y = y
                    self.drag_start_state = {vol_clip.id: copy.deepcopy(vol_clip)}
                    self.update()
                    return

        # Check for edge resize first
        edge_clip, edge = self.get_clip_edge_at(x, y)
        if edge_clip and edge and event.button() == Qt.MouseButton.LeftButton:
            self._background_dirty = True # Selection/resize start changes visual state
            self.resizing_clip = edge_clip.id
            self.resize_edge = edge
            self.resize_start_x = x
            self.resize_original_offset = edge_clip.offset
            self.resize_original_duration = edge_clip.duration
            self.resize_original_start = edge_clip.start
            self.selected_clip = edge_clip.id
            self.selected_clips = [edge_clip.id]
            self.clip_selected.emit(edge_clip.id)
            
            # Snapshot state for undo
            self.resize_start_state = copy.deepcopy(edge_clip)
            self.linked_clip_start_state = None

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
                        self.linked_clip_start_state = copy.deepcopy(clip)
                        break
                    elif edge == "right" and abs(clip_start - boundary_time) < 0.01:
                        # Adjacent clip's left edge touches our right edge
                        self.linked_clip = clip
                        self.linked_original_start = clip.start
                        self.linked_original_duration = clip.duration
                        self.linked_clip_start_state = copy.deepcopy(clip)
                        break
            
            self.update()
            return
        
        hit_clips = self.get_clips_at(x, y)
        
        if hit_clips:
            self._background_dirty = True
            hit_clip_ids = [c.id for c in hit_clips]
            
            # Choose correct clip from stack
            # If current selection is in the stack, stick with it (allows dragging it)
            # Selection cycling is now handled in mouseReleaseEvent for better UX
            clip = hit_clips[0]
            if self.selected_clip in hit_clip_ids:
                clip = next(c for c in hit_clips if c.id == self.selected_clip)

            modifiers = event.modifiers()

            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Click: select following clips in same track
                # Ctrl+Shift+Click: select following clips in all tracks
                same_track_only = not bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                self._select_following_clips(clip, same_track_only=same_track_only)
                return

            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if clip.id in self.selected_clips:
                    self.selected_clips = [cid for cid in self.selected_clips if cid != clip.id]
                else:
                    self.selected_clips.append(clip.id)
                self.selected_clip = self.selected_clips[0] if self.selected_clips else None
                if self.selected_clip:
                    self.clip_selected.emit(self.selected_clip)
                self._background_dirty = True
                self.update()
                return
            elif clip.id not in self.selected_clips:
                self.selected_clips = [clip.id]
                self.selected_clip = clip.id

            if self.selected_clip:
                self.clip_selected.emit(self.selected_clip)

            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging_clip = clip.id
                self.drag_start_x = x
                self.drag_clip_start = clip.start
                # Store all clip positions for dragging
                self.drag_initial_positions = {c.id: c.start for c in self.clips}

                selected_ids = [cid for cid in self.selected_clips if cid in self.drag_initial_positions]
                if len(selected_ids) > 1 and clip.id in selected_ids:
                    self.drag_group_clip_ids = selected_ids
                else:
                    self.drag_group_clip_ids = []

                # Snapshot all clips for Undo
                self.drag_start_state = {c.id: copy.deepcopy(c) for c in self.clips}

            elif event.button() == Qt.MouseButton.RightButton:
                # Show context menu
                self.clip_context_menu.emit(clip.id, event.globalPosition().toPoint())
        else:
            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging_selection_box = True
                self.selection_box_start = (x, y)
                self.selection_box_end = (x, y)
                self.selection_box_additive = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if not self.selection_box_additive:
                    self.selected_clip = None
                    self.selected_clips = []
                    self._background_dirty = True
            else:
                self._background_dirty = True
                self.selected_clip = None
                self.selected_clips = []
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

        # Handle volume dragging
        if self.dragging_volume and self.volume_clip_id:
            clip = next((c for c in self.clips if c.id == self.volume_clip_id), None)
            if clip:
                dy = self.volume_drag_start_y - y # Up is positive
                # Height = 2.0 volume change
                d_vol = (dy / self.track_height) * 2.0
                new_vol = self.volume_drag_start_val + d_vol
                new_vol = max(0.0, min(2.0, new_vol))

                # Snap to 1.0
                if abs(new_vol - 1.0) < 0.05:
                    new_vol = 1.0

                if abs(clip.volume - new_vol) > 0.001:
                    clip.volume = new_vol
                    # Note: We do NOT emit clip_editing here to prevent flooding the backend
                    # with costly re-generation of boosted audio files during drag.
                    # The change is committed on mouseRelease via clip_edited.
                    self._background_dirty = True
                    self.update()

                    QToolTip.showText(event.globalPosition().toPoint(), f"Volume: {new_vol:.2f}x")
            return
        
        # Handle edge resizing - simplified offset-based logic
        if self.resizing_clip:
            dx = x - self.resize_start_x
            dt = dx / self.zoom
            
            # Track snap state for visual indicator
            snapped_start = None
            snapped_end = None
            
            for clip in self.clips:
                if clip.id == self.resizing_clip:
                    if clip.clip_type == "audio":
                        # Audio and subtitle clips: adjust offset and duration together
                        if self.resize_edge == "left":
                            # Left edge: change offset and start, adjust duration
                            new_offset = self.resize_original_offset + dt
                            new_start = self.resize_original_start + dt
                            new_duration = self.resize_original_duration - dt
                            
                            # Apply snapping to timeline start
                            snapped_start = self.get_snap_time(new_start, exclude_clip_id=clip.id)
                            if snapped_start != new_start:
                                snap_dt = snapped_start - self.resize_original_start
                                new_offset = self.resize_original_offset + snap_dt
                                new_start = snapped_start
                                new_duration = self.resize_original_duration - snap_dt
                            
                            # Limit: offset cannot go below 0 (can't extend before source audio start)
                            if new_offset < 0:
                                # Clamp to offset=0 and recalculate
                                new_offset = 0
                                actual_dt = -self.resize_original_offset  # How much we actually moved
                                new_start = self.resize_original_start + actual_dt
                                new_duration = self.resize_original_duration - actual_dt
                            
                            # Ensure valid bounds
                            if new_duration > 0.1:
                                clip.offset = new_offset
                                clip.start = new_start
                                clip.duration = new_duration
                        
                        elif self.resize_edge == "right":
                            # Right edge: change duration only (offset stays same)
                            new_duration = self.resize_original_duration + dt
                            target_end = self.resize_original_start + new_duration
                            
                            # Apply snapping to timeline end
                            snapped_end = self.get_snap_time(target_end, exclude_clip_id=clip.id)
                            if snapped_end != target_end:
                                new_duration = snapped_end - self.resize_original_start
                            
                            # Limit: cannot extend beyond source audio end
                            # source_end = offset + duration, so max_duration = source_audio_length - offset
                            if clip.speaker and clip.speaker in self.speaker_audio_cache:
                                source_audio = self.speaker_audio_cache[clip.speaker]
                                source_audio_length = len(source_audio) / 1000.0  # ms to seconds
                                max_duration = source_audio_length - clip.offset
                                if new_duration > max_duration:
                                    new_duration = max_duration
                            
                            if new_duration > 0.1:
                                clip.duration = new_duration
                    else:
                        # Non-audio clips: just change timeline position/duration
                        if self.resize_edge == "left":
                            new_start = self.resize_original_start + dt
                            new_duration = self.resize_original_duration - dt
                            
                            snapped_start = self.get_snap_time(new_start, exclude_clip_id=clip.id)
                            if snapped_start != new_start:
                                snap_dt = snapped_start - self.resize_original_start
                                new_start = snapped_start
                                new_duration = self.resize_original_duration - snap_dt
                            
                            if new_duration > 0.1:
                                clip.start = new_start
                                clip.duration = new_duration
                                
                        elif self.resize_edge == "right":
                            new_duration = self.resize_original_duration + dt
                            target_end = self.resize_original_start + new_duration
                            
                            snapped_end = self.get_snap_time(target_end, exclude_clip_id=clip.id)
                            if snapped_end != target_end:
                                new_duration = snapped_end - self.resize_original_start
                            
                            if new_duration > 0.1:
                                clip.duration = new_duration
                    
                    # Update snap indicator based on what was snapped
                    self.active_snap_time = None
                    if self.resize_edge == "left" and snapped_start is not None:
                        new_start = clip.start
                        original_new_start = self.resize_original_start + dt
                        if abs(new_start - original_new_start) > 0.001:
                            self.active_snap_time = new_start
                    elif self.resize_edge == "right" and snapped_end is not None:
                        new_end = clip.start + clip.duration
                        original_new_end = self.resize_original_start + self.resize_original_duration + dt
                        if abs(new_end - original_new_end) > 0.001:
                            self.active_snap_time = new_end
                    
                    break
            
            # Update linked clip if Ctrl+drag mode
            if self.linked_clip is not None:
                actual_dt = clip.start - self.resize_original_start if self.resize_edge == "left" else \
                           (clip.duration - self.resize_original_duration)
                if self.resize_edge == "left":
                    # Our left edge moved, linked clip's right edge should follow
                    new_linked_duration = self.linked_original_duration + actual_dt
                    if new_linked_duration > 0.1:
                        self.linked_clip.duration = new_linked_duration
                elif self.resize_edge == "right":
                    # Our right edge moved, linked clip's left edge should follow
                    new_linked_start = self.linked_original_start + actual_dt
                    new_linked_duration = self.linked_original_duration - actual_dt
                    if new_linked_duration > 0.1:
                        self.linked_clip.start = new_linked_start
                        self.linked_clip.duration = new_linked_duration
            
            # Schedule throttled update (waveform update is also throttled)
            self.clip_editing.emit(self.resizing_clip)
            self._schedule_throttled_update(waveform_clip_id=self.resizing_clip)
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

                if self.drag_group_clip_ids:
                    group_initial_starts = [
                        self.drag_initial_positions.get(cid, 0.0)
                        for cid in self.drag_group_clip_ids
                    ]
                    min_group_start = min(group_initial_starts) if group_initial_starts else 0.0
                    if actual_dt < -min_group_start:
                        actual_dt = -min_group_start

                    for clip in self.clips:
                        if clip.id in self.drag_group_clip_ids:
                            initial_start = self.drag_initial_positions.get(clip.id, clip.start)
                            clip.start = max(0, initial_start + actual_dt)

                    final_start = self.drag_clip_start + actual_dt
                else:
                    dragged_clip.start = final_start
            
                # Update snap indicator
                if final_start != new_start:
                    self.active_snap_time = final_start
                elif snapped_end != (new_start + dragged_clip.duration):
                    self.active_snap_time = snapped_end
                else:
                    self.active_snap_time = None
                
            self._schedule_throttled_update()
            return

        if self.dragging_selection_box and self.selection_box_start:
            self.selection_box_end = (x, y)
            self._background_dirty = True
            self.update()
            return
        
        # Update cursor based on edge proximity
        edge_clip, edge = self.get_clip_edge_at(x, y)
        if edge_clip and edge:
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            return

        # Check volume line hover
        vol_clip = self.get_clip_at(x, y)
        if vol_clip and vol_clip.clip_type == "audio" and vol_clip.id == self.selected_clip:
             vol = getattr(vol_clip, 'volume', 1.0)
             clip_y = self.get_track_y(vol_clip.track)
             line_y = clip_y + self.track_height * (1.0 - vol / 2.0)
             line_y = max(clip_y, min(clip_y + self.track_height, line_y))

             if abs(y - line_y) <= 5:
                 self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
                 return

        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release"""
        if self.dragging_playhead:
            self.dragging_playhead = False
            return

        if self.dragging_volume:
            if self.volume_clip_id:
                old_clip = self.drag_start_state.get(self.volume_clip_id)
                current_clip = next((c for c in self.clips if c.id == self.volume_clip_id), None)

                if old_clip and current_clip and abs(old_clip.volume - current_clip.volume) > 0.001:
                    modifications = [(self.volume_clip_id, old_clip, copy.deepcopy(current_clip))]
                    self.history_command_generated.emit('modify', {'modifications': modifications, 'description': f'Adjust volume {current_clip.name}'})

                self.clip_edited.emit(self.volume_clip_id)

            self.dragging_volume = False
            self.volume_clip_id = None
            self.drag_start_state.clear()
            self._background_dirty = True
            self.update()
            return
        
        if self.resizing_clip:
            # Flush any pending throttled update immediately
            self._update_throttle_timer.stop()
            if self._pending_waveform_clip_id:
                self.update_clip_waveform(self._pending_waveform_clip_id)
                self._pending_waveform_clip_id = None
            self._pending_update = False
            
            # Emit signal that clip was edited
            self.clip_edited.emit(self.resizing_clip)

            # Emit command for undo/redo
            if self.resize_start_state:
                # Find current state of resized clip
                current_clip = next((c for c in self.clips if c.id == self.resizing_clip), None)
                if current_clip:
                    modifications = [(self.resizing_clip, self.resize_start_state, copy.deepcopy(current_clip))]

                    if self.linked_clip and self.linked_clip_start_state:
                         modifications.append((self.linked_clip.id, self.linked_clip_start_state, copy.deepcopy(self.linked_clip)))

                    self.history_command_generated.emit('modify', {'modifications': modifications, 'description': f'Resize {current_clip.name}'})

            self.resizing_clip = None
            self.resize_edge = ""
            self.active_snap_time = None
            self._background_dirty = True
            self.update()
            return
        
        if self.dragging_clip:
            # Flush any pending throttled update immediately
            self._update_throttle_timer.stop()
            self._pending_update = False
            
            dragged_clip_id = self.dragging_clip
            moved = False

            for clip in self.clips:
                if clip.id == dragged_clip_id:
                    # Only emit if actually moved
                    if abs(clip.start - self.drag_clip_start) > 0.001:
                        moved = True
                        self.clip_moved.emit(clip.id, clip.start)
                    break

            if moved:
                # Emit undo command
                # Identify which clips changed
                modifications = []
                for cid, old_clip in self.drag_start_state.items():
                    current_clip = next((c for c in self.clips if c.id == cid), None)
                    if current_clip and (abs(current_clip.start - old_clip.start) > 0.001):
                        modifications.append((cid, old_clip, copy.deepcopy(current_clip)))

                if modifications:
                     self.history_command_generated.emit('modify', {'modifications': modifications, 'description': 'Move clips'})
            else:
                # Clicked but didn't move: handle cycling if in a stack
                hit_clips = self.get_clips_at(event.position().x(), event.position().y())
                if len(hit_clips) > 1 and self.selected_clip in [c.id for c in hit_clips]:
                    # Cycle to next clip in stack
                    current_idx = next(i for i, c in enumerate(hit_clips) if c.id == self.selected_clip)
                    next_idx = (current_idx + 1) % len(hit_clips)
                    new_clip = hit_clips[next_idx]
                    self.selected_clip = new_clip.id
                    self.selected_clips = [new_clip.id]
                    self.clip_selected.emit(new_clip.id)
                    self._background_dirty = True

            self.dragging_clip = None
            self.drag_group_clip_ids = []
            self.active_snap_time = None
            self.drag_start_state.clear()
            self._background_dirty = True
            self.update()

        if self.dragging_selection_box and self.selection_box_start and self.selection_box_end:
            x1, y1 = self.selection_box_start
            x2, y2 = self.selection_box_end
            moved = abs(x2 - x1) > 4 or abs(y2 - y1) > 4

            if moved:
                rect = QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
                box_selected = self.get_clip_ids_in_rect(rect)
                if self.selection_box_additive:
                    existing = list(self.selected_clips)
                    for cid in box_selected:
                        if cid not in existing:
                            existing.append(cid)
                    self.selected_clips = existing
                else:
                    self.selected_clips = box_selected
                self.selected_clip = self.selected_clips[0] if self.selected_clips else None
                if self.selected_clip:
                    self.clip_selected.emit(self.selected_clip)
            elif not self.selection_box_additive:
                self.selected_clip = None
                self.selected_clips = []
                new_time = self.x_to_time(event.position().x())
                self.playhead_time = max(0, new_time)
                self.playhead_moved.emit(self.playhead_time)

            self.dragging_selection_box = False
            self.selection_box_start = None
            self.selection_box_end = None
            self.selection_box_additive = False
            self._background_dirty = True
            self.update()
            return

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
            
            # anchor_time: the time coordinate actually under the mouse (before zoom change)
            anchor_time = self.x_to_time(event.position().x())
            
            old_zoom = self.zoom
            self.zoom = max(20, min(500, self.zoom * (1.1 ** delta)))
            
            # Adjust scroll to keep mouse position stable:
            # new_scroll = old_scroll + anchor_time * (new_zoom - old_zoom)
            self.scroll_offset += anchor_time * (self.zoom - old_zoom)
            self.scroll_offset = max(0, self.scroll_offset)
        else:
            # Scroll
            self.scroll_offset -= event.angleDelta().x() + event.angleDelta().y()
            self.scroll_offset = max(0, self.scroll_offset)
        
        self._background_dirty = True
        self.update()
        self.view_changed.emit()

    def keyPressEvent(self, event):
        """Handle key press events"""
        if event.key() == Qt.Key.Key_Space:
            event.ignore()  # Let main window handle playback toggle
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.copy_requested.emit()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            self.paste_requested.emit()
        elif event.key() == Qt.Key.Key_Delete:
            # Delete selected clip
            if self.selected_clip:
                self.clip_delete_requested.emit(self.selected_clip)
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_E:
            self.split_requested.emit()
        else:
            super().keyPressEvent(event)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter - accept if dragging images"""
        if event.mimeData().hasUrls():
            # Check if any URL is an image
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile().lower()
                    if path.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                        event.setDropAction(Qt.DropAction.CopyAction)
                        event.accept()
                        return
        # Also accept text with file paths (from QListWidget)
        if event.mimeData().hasText():
            text = event.mimeData().text()
            if text.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                return
        event.ignore()
    
    def dragMoveEvent(self, event: QDragMoveEvent):
        """Handle drag move - show drop indicator on image track"""
        y = event.position().y()
        # Track 2 is the image track
        image_track_y = self.get_track_y(2)
        
        # Accept if within image track area
        if image_track_y <= y <= image_track_y + self.track_height:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            # Still accept but could show different feedback
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
    
    def dropEvent(self, event: QDropEvent):
        """Handle drop - create image clip at drop position"""
        x = event.position().x()
        drop_time = max(0, self.x_to_time(x))
        
        image_path = None
        
        # Try to get from URLs first
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                        image_path = path
                        break
        
        # Fallback to text
        if not image_path and event.mimeData().hasText():
            text = event.mimeData().text()
            if text.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                image_path = text
        
        if image_path:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.image_dropped.emit(image_path, drop_time)
        else:
            event.ignore()



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
        track_labels_layout.setContentsMargins(0, 30, 0, 0) # Top margin matches header height (30px)
        track_labels_layout.setSpacing(5) # Spacing matches track padding
        
        # Helper to create centered label with fixed height
        def create_track_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #ccc; font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(50) # Match track height
            return lbl
        
        track_labels_layout.addWidget(create_track_label("오디오"))
        track_labels_layout.addWidget(create_track_label("자막"))
        track_labels_layout.addWidget(create_track_label("이미지"))
        track_labels_layout.addStretch()
        
        self.track_labels.setFixedWidth(70)
        content_layout.addWidget(self.track_labels)
        
        # Timeline canvas
        self.canvas = TimelineCanvas()
        self.canvas.playhead_moved.connect(self._on_playhead_moved)
        self.canvas.view_changed.connect(self._update_scrollbar)
        content_layout.addWidget(self.canvas, 1)
        
        layout.addLayout(content_layout)

        # Scrollbar layout (Spacer + ScrollBar)
        scrollbar_layout = QHBoxLayout()
        scrollbar_layout.setSpacing(0)

        # Spacer to align scrollbar with canvas (skipping track labels)
        spacer = QWidget()
        spacer.setFixedWidth(70) # Match track_labels width
        spacer.setStyleSheet("background-color: #252525;") # Match track labels bg
        scrollbar_layout.addWidget(spacer)

        # Horizontal ScrollBar
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._on_scroll_moved)
        scrollbar_layout.addWidget(self.scrollbar)

        layout.addLayout(scrollbar_layout)
    
    def _update_scrollbar(self):
        """Update scrollbar range and value based on canvas state"""
        if not self.canvas or self.canvas.width() <= 0:
            return

        visible_width = self.canvas.width()
        content_width = self.canvas.total_duration * self.canvas.zoom

        # Add padding so we can scroll past the last clip
        padding = visible_width * 0.5
        total_scrollable_width = content_width + padding

        # Maximum value for scrollbar
        max_scroll = max(0, total_scrollable_width - visible_width)

        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, int(max_scroll))
        self.scrollbar.setPageStep(int(visible_width))
        self.scrollbar.setSingleStep(int(self.canvas.zoom)) # Scroll 1 second per step
        self.scrollbar.setValue(int(self.canvas.scroll_offset))
        self.scrollbar.blockSignals(False)

    def _on_scroll_moved(self, value: int):
        """Handle scrollbar movement"""
        self.canvas.scroll_offset = float(value)
        self.canvas._background_dirty = True
        self.canvas.update()

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
        self.canvas._background_dirty = True
        self.canvas.update()
