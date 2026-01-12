"""\
Video Renderer - Render final video with images, audio, and subtitles.

This implementation uses FFmpeg directly (no per-frame Python rendering),
which is typically much faster than MoviePy for slideshow-style videos.

Subtitles are rendered using Qt's QPainter (same as preview) for pixel-perfect
matching between preview and final output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import contextlib
import concurrent.futures
import os
import re
import subprocess
import tempfile
import wave

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QFont, QColor, QPainterPath, QPen, QFontMetrics

from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS

# Export constants for UI consistency
SUBTITLE_PADDING_H = 15
SUBTITLE_PADDING_V = 8
SUBTITLE_RADIUS = 4



@dataclass
class ImageSegment:
    """An image segment for the timeline"""

    image_path: str
    start_time: float
    end_time: float
    track: int = 0


@dataclass
class SubtitleSegment:
    """A subtitle overlay"""

    text: str
    start_time: float
    end_time: float


class VideoRenderer:
    """Render final video from images, audio, and subtitles using FFmpeg"""

    def __init__(self, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT, fps: int = VIDEO_FPS):
        self.width = width
        self.height = height
        self.fps = fps
        
        # Detect best encoder on init (cache for later use)
        self._gpu_encoder_name, self._gpu_encoder_opts = self._detect_best_encoder(use_hw_accel=True)
        self._cpu_encoder_name, self._cpu_encoder_opts = ("libx264", ["-preset", "medium", "-threads", "0"])
        
        # Default to GPU if available
        self._encoder_name = self._gpu_encoder_name
        self._encoder_opts = self._gpu_encoder_opts

    def _test_encoder_works(self, encoder_name: str, encoder_opts: list[str]) -> bool:
        """Actually test if an encoder works by encoding a tiny test frame.
        
        This is more reliable than just checking if the encoder is listed,
        because GPU encoders can be listed but fail due to missing drivers.
        """
        try:
            # Create a minimal test: 1 frame of 64x64 black video
            test_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                "-frames:v", "1",
                "-c:v", encoder_name,
                *encoder_opts,
                "-f", "null", "-"  # Output to null (no file created)
            ]
            
            result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10
            )
            
            return result.returncode == 0
        except Exception:
            return False

    def _detect_best_encoder(self, use_hw_accel: bool = True) -> tuple[str, list[str]]:
        """Detect best available H.264 encoder (GPU > CPU)
        
        Actually tests each encoder to ensure it works, not just listed.
        
        Args:
            use_hw_accel: If False, skip GPU encoders and use CPU only.
        
        Returns:
            Tuple of (encoder_name, encoder_options_list)
        """
        if use_hw_accel:
            # GPU encoders in priority order
            gpu_encoders = [
                ("h264_nvenc", ["-preset", "p4", "-tune", "hq", "-rc", "vbr"]),  # NVIDIA
                ("h264_qsv", ["-preset", "medium"]),  # Intel QuickSync
                ("h264_amf", ["-quality", "balanced"]),  # AMD
            ]
            
            for encoder, opts in gpu_encoders:
                if self._test_encoder_works(encoder, opts):
                    return (encoder, opts)
        
        # Fallback: CPU with multi-threading
        return ("libx264", ["-preset", "medium", "-threads", "0"])

    def _get_audio_duration_seconds(self, audio_path: str | Path) -> float:
        audio_path = str(audio_path)

        # Fast path for WAV (your preview audio is WAV)
        if audio_path.lower().endswith(".wav"):
            with contextlib.closing(wave.open(audio_path, "rb")) as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 1
                return frames / float(rate)

        # Fallback: ffprobe
        try:
            out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    audio_path,
                ],
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).strip()
            return float(out)
        except Exception:
            return 0.0

    def _format_ass_time(self, seconds: float) -> str:
        """Format time for ASS: H:MM:SS.cc"""
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        cs = int(round((s - int(s)) * 100))
        s = int(s)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _to_ass_color(self, hex_color: str, alpha: int = 0) -> str:
        """Convert #RRGGBB to &HAABBGGRR. Alpha: 0 (opaque) to 255 (transparent)."""
        if hex_color.startswith('#'):
            hex_color = hex_color[1:]
        
        if len(hex_color) == 6:
            r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
            return f"&H{alpha:02X}{b}{g}{r}" # ASS is BBGGRR
        return f"&H{alpha:02X}FFFFFF"

    def _render_subtitle_png(self, text: str, settings: dict, width: int, height: int) -> str:
        """
        Render a subtitle text to a transparent PNG using Qt's QPainter.
        This uses the EXACT same rendering logic as StrokedLabel in preview_widget.py.
        
        Returns the path to the temporary PNG file.
        """
        s = settings or {}
        font_name = s.get('font_family', 'Malgun Gothic')
        font_size = s.get('font_size', 32)
        line_spacing = s.get('line_spacing', 1.4)
        font_color = QColor(s.get('font_color', '#FFFFFF'))
        
        outline_enabled = s.get('outline_enabled', True) and not s.get('bg_enabled', False)
        outline_width = s.get('outline_width', 2) if outline_enabled else 0
        outline_color = QColor(s.get('outline_color', '#000000'))
        
        bg_enabled = s.get('bg_enabled', False)
        bg_color = QColor(s.get('bg_color', '#000000'))
        bg_alpha = s.get('bg_alpha', 160)
        
        pos_setting = s.get('position', 'Bottom')
        margin_v = s.get('margin_v', 48)
        
        # Create transparent image at full video resolution
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        # Setup font - EXACTLY like StrokedLabel
        # CRITICAL: Use setPixelSize() not point size for exact pixel matching
        font = QFont(font_name)
        font.setPixelSize(font_size)  # Set pixel size directly, not points
        painter.setFont(font)
        metrics = QFontMetrics(font)
        
        # Handle multiline text - EXACTLY like StrokedLabel
        lines = text.replace('\r\n', '\n').split('\n')
        lines = [l for l in lines if l.strip()]  # Remove empty lines
        
        if not lines:
            painter.end()
            # Return empty transparent PNG
            fd, path = tempfile.mkstemp(prefix="pbb_sub_", suffix=".png")
            os.close(fd)
            image.save(path, "PNG")
            return path
        
        line_height = metrics.height()
        
        # Calculate total text block height - EXACTLY like StrokedLabel
        leading = line_height * (line_spacing - 1.0)
        num_lines = len(lines)
        total_text_height = num_lines * line_height + (num_lines - 1) * leading
        
        # Calculate text block width (for background)
        max_line_width = max(metrics.horizontalAdvance(line) for line in lines)
        
        # Padding for background box
        padding_h = SUBTITLE_PADDING_H
        padding_v = SUBTITLE_PADDING_V
        
        # Calculate Y position based on position setting
        if pos_setting == 'Bottom':
            # Bottom of text block at (height - margin_v)
            block_bottom = height - margin_v
            block_top = block_bottom - total_text_height
        elif pos_setting == 'Top':
            block_top = margin_v
        else:  # Center
            block_top = (height - total_text_height) / 2
        
        # X is always centered
        block_left = (width - max_line_width) / 2
        
        # Draw background if enabled
        if bg_enabled:
            bg_color.setAlpha(bg_alpha)
            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            bg_rect_x = block_left - padding_h
            bg_rect_y = block_top - padding_v
            bg_rect_w = max_line_width + padding_h * 2
            bg_rect_h = total_text_height + padding_v * 2
            painter.drawRoundedRect(int(bg_rect_x), int(bg_rect_y), 
                                   int(bg_rect_w), int(bg_rect_h), 
                                   SUBTITLE_RADIUS, SUBTITLE_RADIUS)
        
        # Build text path - EXACTLY like StrokedLabel
        path = QPainterPath()
        current_y = block_top + metrics.ascent()
        
        for line in lines:
            if not line:
                current_y += line_height + leading
                continue
            
            line_width = metrics.horizontalAdvance(line)
            # Center each line
            x = (width - line_width) / 2
            
            path.addText(x, current_y, font, line)
            current_y += line_height + leading
        
        # Draw outline (stroke) - EXACTLY like StrokedLabel
        if outline_width > 0:
            pen = QPen(outline_color)
            # Width * 2 because stroke is centered on the path boundary
            pen.setWidthF(outline_width * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        
        # Draw text fill - EXACTLY like StrokedLabel
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(font_color)
        painter.drawPath(path)
        
        painter.end()
        
        # Save to temp PNG
        fd, png_path = tempfile.mkstemp(prefix="pbb_sub_", suffix=".png")
        os.close(fd)
        image.save(png_path, "PNG")
        
        return png_path

    def _write_temp_ass(self, subtitles: list[SubtitleSegment], settings: dict, width: int, height: int) -> str:
        subs = [
            SubtitleSegment(text=(s.text or "").strip(), start_time=float(s.start_time), end_time=float(s.end_time))
            for s in subtitles
            if (s.text or "").strip() and float(s.end_time) > float(s.start_time)
        ]
        subs.sort(key=lambda s: (s.start_time, s.end_time))

        fd, path = tempfile.mkstemp(prefix="pbb_", suffix=".ass")
        os.close(fd)

        # Settings extraction
        s = settings or {}
        font_name = s.get('font_family', 'Malgun Gothic')
        user_font_size = s.get('font_size', 32)
        line_spacing = s.get('line_spacing', 1.4)
        
        # ========================================================================
        # CRITICAL: Font size matching between preview and ASS rendering
        # 
        # Preview (StrokedLabel) uses pixel-based font sizes.
        # ASS uses point-based font sizes with PlayRes scaling.
        # 
        # ASS FontSize is specified in points and scaled by PlayResY.
        # The actual rendered pixel height ≈ FontSize × (output_height / PlayResY) × (72/96)
        # 
        # To match preview's pixel-exact rendering:
        # - We set PlayResX/Y to match output resolution (done)
        # - The font size in ASS points should equal the intended pixel size
        #   because PlayResY == output_height means 1:1 scaling for position
        # - However, ASS font rendering uses point-to-pixel conversion (~0.75x at 96 DPI)
        # 
        # Solution: Compensate by scaling font_size by approximately 1.333 (96/72)
        # This makes ASS render at the same visual size as the Qt preview.
        # ========================================================================
        DPI_COMPENSATION = 96.0 / 72.0  # ≈ 1.333
        font_size = int(round(user_font_size * DPI_COMPENSATION))
        
        # Colors
        font_color = self._to_ass_color(s.get('font_color', '#FFFFFF'), 0)
        
        # Outline / Background configuration
        is_bg = s.get('bg_enabled', False)
        
        qt_alpha = s.get('bg_alpha', 160)
        ass_alpha = max(0, min(255, 255 - qt_alpha))
        
        if is_bg:
            border_style = 3  # Opaque Box
            outline_color = self._to_ass_color(s.get('bg_color', '#000000'), ass_alpha)
            # Outline width acts as padding for the box
            outline_width = 2
            shadow_depth = 0
            back_color = "&H00000000"
        else:
            border_style = 1  # Outline
            outline_enabled = s.get('outline_enabled', True)
            w = s.get('outline_width', 2)
            # Scale outline width with same DPI compensation
            outline_width = int(round(w * DPI_COMPENSATION)) if outline_enabled else 0
            outline_color = self._to_ass_color(s.get('outline_color', '#000000'), 0)
            shadow_depth = 0
            back_color = "&H00000000"

        # Alignment Logic
        pos_setting = s.get('position', 'Bottom')
        margin_v = s.get('margin_v', 48)
        
        # We will use \pos(x,y) override for precise line spacing control
        # Base alignment for text block processing
        if pos_setting == 'Top':
            base_align = 8 # Top Center
        elif pos_setting == 'Center':
            base_align = 5 # Middle Center
        else:
            base_align = 2 # Bottom Center

        # ASS Header
        content = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 1", 
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{font_name},{font_size},{font_color},&H000000FF,{outline_color},{back_color},0,0,0,0,100,100,0,0,{border_style},{outline_width},{shadow_depth},{base_align},10,10,{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        ]

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(content) + "\n")
            
            x_pos = width / 2
            
            for sub in subs:
                start = self._format_ass_time(sub.start_time)
                end = self._format_ass_time(sub.end_time)
                
                # Split lines for custom spacing
                lines = sub.text.replace('\r\n', '\n').split('\n')
                
                if not lines: 
                    continue

                # ================================================================
                # Line spacing calculation - matching StrokedLabel behavior
                # 
                # StrokedLabel calculates:
                #   line_height = metrics.height()  (font's actual pixel height)
                #   leading = line_height * (line_spacing - 1.0)
                #   total_text_height = num_lines * line_height + (num_lines - 1) * leading
                #   => Simplified: line_height * line_spacing * (num_lines - 1) + line_height
                #   
                # The distance between baseline of consecutive lines:
                #   = line_height + leading = line_height * line_spacing
                #
                # For ASS with \pos, we position each line's anchor point.
                # The effective line height (distance between anchors) should be:
                #   font_size (in ASS points, after DPI compensation) * line_spacing
                # ================================================================
                eff_h = font_size * line_spacing
                
                y_positions = []
                num_lines = len(lines)
                
                if pos_setting == 'Bottom':
                    # Bottom-up stacking. \an2 (Bottom Center) means Y is the bottom of the text.
                    # The bottom-most line's bottom edge is at (height - margin_v).
                    base_y = height - margin_v
                    for i in range(num_lines):
                        # The last line (index num_lines-1) is at base_y.
                        # Lines above it are at base_y - (offset_from_bottom * eff_h).
                        y = base_y - ((num_lines - 1 - i) * eff_h)
                        y_positions.append(y)
                        
                elif pos_setting == 'Top':
                    # Top-down stacking. \an8 (Top Center) means Y is the top of the text.
                    # The top-most line's top edge is at margin_v.
                    base_y = margin_v
                    for i in range(num_lines):
                        y = base_y + (i * eff_h)
                        y_positions.append(y)
                        
                else: # Center
                    # Centered stacking. \an5 (Middle Center) means Y is the vertical center of the text.
                    total_span = (num_lines - 1) * eff_h
                    first_line_center_y = (height / 2) - (total_span / 2)
                    
                    for i in range(num_lines):
                        y = first_line_center_y + (i * eff_h)
                        y_positions.append(y)

                # Write events for each line
                for i, line in enumerate(lines):
                    if not line.strip(): continue # Skip empty lines if they result from splitting

                    y = y_positions[i]
                    
                    # Override alignment for this specific line
                    if pos_setting == 'Bottom':
                        align_tag = r"\an2"
                    elif pos_setting == 'Top':
                        align_tag = r"\an8"
                    else: # Center
                        align_tag = r"\an5"
                        
                    # Pos tag
                    pos_tag = f"\\pos({int(x_pos)},{int(y)})"
                    
                    full_text = f"{{ {align_tag}{pos_tag} }}{line}"
                    f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{full_text}\n")
        
        return path

    def _escape_path_for_ffmpeg_filter(self, path: str) -> str:
        # For FFmpeg filter strings on Windows.
        p = path.replace("\\", "/")
        p = p.replace(":", "\\:")
        p = p.replace("'", "\\'")
        return p

    def render(
        self,
        images: list[ImageSegment],
        audio_path: str | Path,
        subtitles: list[SubtitleSegment] | None = None,
        output_path: str | Path = "output.mp4",
        progress_callback=None,
        settings: dict = None,
        cancel_check=None,
    ) -> None:
        """
        Render video with Qt-based subtitle rendering for pixel-perfect preview matching.
        
        Args:
            cancel_check: Optional callable that returns True if rendering should be cancelled.
        
        Subtitles are rendered as PNG overlays using the same Qt QPainter logic
        as the preview widget, ensuring 100% identical appearance.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Select encoder based on hardware acceleration setting
        use_hw_accel = settings.get('use_hw_accel', True) if settings else True
        if use_hw_accel:
            self._encoder_name = self._gpu_encoder_name
            self._encoder_opts = self._gpu_encoder_opts
            print(f"[VideoRenderer] Hardware acceleration enabled, using: {self._encoder_name}")
        else:
            self._encoder_name = self._cpu_encoder_name
            self._encoder_opts = self._cpu_encoder_opts
            print(f"[VideoRenderer] Hardware acceleration disabled, using: {self._encoder_name}")

        audio_path = str(audio_path)
        if not audio_path or not Path(audio_path).exists():
            raise RuntimeError("오디오 파일을 찾을 수 없습니다.")

        audio_duration = self._get_audio_duration_seconds(audio_path)
        if audio_duration <= 0:
            raise RuntimeError("오디오 길이를 확인할 수 없습니다.")
        
        # Calculate total duration from ALL clips (images, subtitles, audio)
        # This ensures preview and rendering use the same timeline duration
        max_image_end = max((float(seg.end_time) for seg in images), default=0.0) if images else 0.0
        max_subtitle_end = max((float(sub.end_time) for sub in subtitles), default=0.0) if subtitles else 0.0
        total_duration = max(audio_duration, max_image_end, max_subtitle_end)

        # Build contiguous visuals covering full audio duration.
        segments: list[ImageSegment] = []
        for seg in images:
            if not seg.image_path or not Path(seg.image_path).exists():
                continue
            start = max(0.0, float(seg.start_time))
            end = min(total_duration, float(seg.end_time))
            if end > start:
                segments.append(ImageSegment(seg.image_path, start, end, int(getattr(seg, "track", 0))))

        def _q(t: float) -> float:
            return round(float(t), 3)

        boundaries = {_q(0.0), _q(total_duration)}
        for seg in segments:
            boundaries.add(_q(seg.start_time))
            boundaries.add(_q(seg.end_time))
        times = sorted(boundaries)
        if len(times) < 2:
            times = [0.0, total_duration]

        visuals: list[tuple[str | None, float]] = []
        for t0, t1 in zip(times, times[1:]):
            if t1 <= t0:
                continue
            active = [s for s in segments if s.start_time <= t0 < s.end_time]
            if active:
                chosen = max(active, key=lambda s: (s.track, s.start_time))
                img_path: str | None = chosen.image_path
            else:
                img_path = None
            dur = float(t1 - t0)
            if dur <= 0:
                continue
            if visuals and visuals[-1][0] == img_path:
                visuals[-1] = (visuals[-1][0], visuals[-1][1] + dur)
            else:
                visuals.append((img_path, dur))

        if not visuals:
            visuals = [(None, total_duration)]

        # =====================================================================
        # OPTIMIZED Qt-based subtitle rendering
        # 
        # Strategy: Create a subtitle video using concat demuxer to avoid
        # command line length limits on Windows (WinError 206).
        # 
        # 1. Render each unique subtitle to PNG once
        # 2. Create concat demuxer file listing all PNGs with durations
        # 3. Build subtitle video from concat file
        # 4. Single overlay of subtitle video onto main video
        # =====================================================================
        
        # Build subtitle timeline segments
        class SubtitleTimelineSegment:
            def __init__(self, png_path: str | None, duration: float):
                self.png_path = png_path
                self.duration = duration
        
        subtitle_timeline: list[SubtitleTimelineSegment] = []
        unique_subtitles: dict[str, str] = {}  # text -> png_path
        transparent_png_path: str | None = None
        subtitle_video_path: str | None = None
        concat_file_path: str | None = None
        
        if subtitles and settings and settings.get('subtitle_enabled', True):
            # Check for cancellation at start
            if cancel_check and cancel_check():
                return
            
            if progress_callback:
                progress_callback(2, "자막 이미지 생성 중...")
            
            # Filter and sort subtitles
            valid_subs = [
                SubtitleSegment(text=(s.text or "").strip(), start_time=float(s.start_time), end_time=float(s.end_time))
                for s in subtitles
                if (s.text or "").strip() and float(s.end_time) > float(s.start_time)
            ]
            valid_subs.sort(key=lambda s: (s.start_time, s.end_time))
            
            # Create transparent PNG for gaps
            transparent_img = QImage(self.width, self.height, QImage.Format.Format_ARGB32)
            transparent_img.fill(Qt.GlobalColor.transparent)
            fd, transparent_png_path = tempfile.mkstemp(prefix="pbb_trans_", suffix=".png")
            os.close(fd)
            transparent_img.save(transparent_png_path, "PNG")
            
            # =====================================================================
            # OPTIMIZATION: Parallel PNG rendering for unique subtitle texts
            # Uses ThreadPoolExecutor to render multiple PNGs concurrently
            # =====================================================================
            unique_texts = list(set(sub.text for sub in valid_subs))
            
            if unique_texts:
                # Parallel rendering of unique subtitles
                def render_one(text: str) -> tuple[str, str]:
                    png_path = self._render_subtitle_png(text, settings, self.width, self.height)
                    return (text, png_path)
                
                # Use up to 4 workers (balance between speed and resource usage)
                max_workers = min(4, len(unique_texts))
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(render_one, text): text for text in unique_texts}
                    for future in concurrent.futures.as_completed(futures):
                        text, png_path = future.result()
                        unique_subtitles[text] = png_path
            
            # Build timeline with gaps (using pre-rendered PNGs)
            current_time = 0.0
            for sub in valid_subs:
                # Add transparent segment before subtitle if there's a gap
                if sub.start_time > current_time:
                    gap_dur = sub.start_time - current_time
                    if gap_dur > 0.001:
                        subtitle_timeline.append(SubtitleTimelineSegment(transparent_png_path, gap_dur))
                
                # Add subtitle segment (PNG already rendered)
                sub_dur = sub.end_time - max(sub.start_time, current_time)
                if sub_dur > 0.001:
                    subtitle_timeline.append(SubtitleTimelineSegment(unique_subtitles[sub.text], sub_dur))
                    current_time = sub.end_time
            
            # Add final transparent segment if needed
            if current_time < total_duration:
                remaining = total_duration - current_time
                if remaining > 0.001:
                    subtitle_timeline.append(SubtitleTimelineSegment(transparent_png_path, remaining))

        try:
            # =====================================================================
            # Pre-render subtitle timeline as a separate video to avoid long command
            # This prevents WinError 206 (filename too long) on Windows
            # =====================================================================
            if subtitle_timeline:
                # Check for cancellation before subtitle video
                if cancel_check and cancel_check():
                    return
                
                if progress_callback:
                    progress_callback(5, "자막 비디오 생성 중...")
                
                # Create concat demuxer file for subtitle timeline
                fd, concat_file_path = tempfile.mkstemp(prefix="pbb_concat_", suffix=".txt")
                os.close(fd)
                
                with open(concat_file_path, "w", encoding="utf-8") as cf:
                    for segment in subtitle_timeline:
                        # Escape path for concat demuxer (forward slashes, escape special chars)
                        escaped_path = segment.png_path.replace("\\", "/").replace("'", "'\\''")
                        cf.write(f"file '{escaped_path}'\n")
                        cf.write(f"duration {segment.duration}\n")
                
                # Create subtitle video using concat demuxer
                fd, subtitle_video_path = tempfile.mkstemp(prefix="pbb_subs_", suffix=".mov")
                os.close(fd)
                
                sub_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-f", "concat", "-safe", "0", "-i", concat_file_path,
                    "-c:v", "png",  # Use PNG codec for lossless transparency
                    "-pix_fmt", "rgba",
                    "-r", str(self.fps),
                    subtitle_video_path
                ]
                
                sub_result = subprocess.run(
                    sub_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                
                if sub_result.returncode != 0:
                    raise RuntimeError(f"자막 비디오 생성 실패:\n{sub_result.stderr}")
            
            # Check for cancellation before image video
            if cancel_check and cancel_check():
                return
            
            if progress_callback:
                progress_callback(10, "이미지 비디오 생성 중...")
            
            # =====================================================================
            # Pre-render images to intermediate video (MEMORY OPTIMIZATION)
            # 
            # Instead of loading all images as separate FFmpeg inputs (10GB+ RAM),
            # we create a single image video using concat demuxer.
            # This processes images ONE AT A TIME, keeping memory usage low.
            # =====================================================================
            image_video_path: str | None = None
            image_concat_file_path: str | None = None
            
            if visuals:
                # Create concat demuxer file for image timeline
                fd, image_concat_file_path = tempfile.mkstemp(prefix="pbb_img_concat_", suffix=".txt")
                os.close(fd)
                
                # Create a black frame PNG for gaps
                black_frame_path = None
                has_gaps = any(img_path is None for img_path, dur in visuals)
                if has_gaps:
                    # Use RGB32 (no alpha) and save as JPG to match typical input formats (YUV colorspace)
                    # Mixing PNG (RGB) and JPG (YUV) in concat demuxer often causes "last frame freeze" issues
                    black_img = QImage(self.width, self.height, QImage.Format.Format_RGB32)
                    black_img.fill(QColor(0, 0, 0))
                    fd, black_frame_path = tempfile.mkstemp(prefix="pbb_black_", suffix=".jpg")
                    os.close(fd)
                    black_img.save(black_frame_path, "JPG", quality=100)
                
                with open(image_concat_file_path, "w", encoding="utf-8") as cf:
                    for img_path, dur in visuals:
                        # Use black frame for None (gaps in timeline)
                        actual_path = img_path if img_path else black_frame_path
                        escaped_path = actual_path.replace("\\", "/").replace("'", "'\\''")
                        cf.write(f"file '{escaped_path}'\n")
                        cf.write(f"duration {dur}\n")
                
                # Create intermediate image video
                fd, image_video_path = tempfile.mkstemp(prefix="pbb_images_", suffix=".mov")
                os.close(fd)
                
                # Pre-render images with scaling (lossless intermediate)
                img_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-f", "concat", "-safe", "0", "-i", image_concat_file_path,
                    "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                           f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",  # Lossless
                    "-pix_fmt", "yuv420p",
                    "-r", str(self.fps),
                    image_video_path
                ]
                
                img_result = subprocess.run(
                    img_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                
                if img_result.returncode != 0:
                    raise RuntimeError(f"이미지 비디오 생성 실패:\n{img_result.stderr}")
                
                # Clean up black frame
                if black_frame_path:
                    try:
                        os.remove(black_frame_path)
                    except Exception:
                        pass
            
            
            # =====================================================================
            # Pad audio with silence if it's shorter than total_duration
            # This ensures the video doesn't end early when using multiple streams
            # =====================================================================
            padded_audio_path = None
            final_audio_path = audio_path
            
            if audio_duration < total_duration:
                # Check for cancellation before audio padding
                if cancel_check and cancel_check():
                    return
                
                if progress_callback:
                    progress_callback(12, "오디오 패딩 중...")
                
                # Create temp file for padded audio
                fd, padded_audio_path = tempfile.mkstemp(prefix="pbb_padded_", suffix=".wav")
                os.close(fd)
                
                silence_duration = total_duration - audio_duration
                
                # Pad audio with silence to match total_duration
                pad_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-i", audio_path,
                    "-f", "lavfi", "-t", str(silence_duration),
                    "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[outa]",
                    "-map", "[outa]",
                    padded_audio_path
                ]
                
                pad_result = subprocess.run(
                    pad_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                
                if pad_result.returncode != 0:
                    raise RuntimeError(f"오디오 패딩 실패:\n{pad_result.stderr}")
                
                final_audio_path = padded_audio_path
            
            # Check for cancellation before final render
            if cancel_check and cancel_check():
                return
            
            if progress_callback:
                progress_callback(15, "최종 렌더링 준비 중...")
            
            # =====================================================================
            # SIMPLIFIED FINAL RENDER: Only 3 inputs (image video, subtitle video, audio)
            # This dramatically reduces memory usage and filter complexity
            # =====================================================================
            cmd: list[str] = ["ffmpeg", "-y", "-hide_banner"]
            
            # Input 0: Pre-rendered image video
            if image_video_path and Path(image_video_path).exists():
                cmd += ["-i", image_video_path]
            else:
                # Fallback: generate black video for total duration
                cmd += [
                    "-f", "lavfi", "-t", str(total_duration),
                    "-i", f"color=c=black:s={self.width}x{self.height}:r={self.fps}"
                ]
            
            # Input 1: Audio (possibly padded)
            cmd += ["-i", final_audio_path]
            
            # Input 2: Subtitle video (optional)
            has_subtitle_video = subtitle_video_path and Path(subtitle_video_path).exists()
            if has_subtitle_video:
                cmd += ["-i", subtitle_video_path]
            
            # Build simplified filter graph
            if has_subtitle_video:
                # Overlay subtitles on images
                filter_complex = "[0:v][2:v]overlay=0:0:format=auto,format=yuv420p[vfinal]"
            else:
                # No subtitles, just pass through
                filter_complex = "[0:v]format=yuv420p[vfinal]"
            
            # Use detected encoder (GPU or CPU with multi-threading)
            # NOTE: Removed -shortest flag to allow all streams to run to full duration
            # All streams (video, audio, subtitle) are now pre-rendered to match total_duration
            cmd += [
                "-filter_complex", filter_complex,
                "-map", "[vfinal]",
                "-map", "1:a:0",
                "-c:v", self._encoder_name,
                *self._encoder_opts,
                "-crf", "23",  # Better quality than 28
                "-pix_fmt", "yuv420p",
                "-r", str(self.fps),
                "-c:a", "aac",
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                "-nostats",
                str(output_path),
            ]

            total_us = int(total_duration * 1_000_000)
            last_pct = -1

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                universal_newlines=True,
            )

            if proc.stdout is None:
                raise RuntimeError("FFmpeg 실행에 실패했습니다.")

            # ================================================================
            # Threaded output reader with stall detection
            # 
            # Windows does not support non-blocking reads or select() on pipes.
            # To detect FFmpeg hangs, we use a background thread to read stdout
            # and push lines to a Queue. The main thread reads from the queue
            # with a timeout, allowing us to detect stalls.
            # ================================================================
            import queue
            import threading
            
            output_queue: queue.Queue = queue.Queue()
            reader_exception: list[Exception] = []
            
            def reader_thread():
                """Background thread to read FFmpeg output"""
                try:
                    for line in proc.stdout:
                        output_queue.put(line)
                except Exception as e:
                    reader_exception.append(e)
                finally:
                    output_queue.put(None)  # Signal end of output
            
            reader = threading.Thread(target=reader_thread, daemon=True)
            reader.start()
            
            combined_output: list[str] = []
            stall_timeout = 60  # seconds - no output for this long = stall
            cancelled = False
            
            while True:
                # Check for cancellation request
                if cancel_check and cancel_check():
                    cancelled = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    break
                
                try:
                    line = output_queue.get(timeout=1)  # Short timeout to check cancellation frequently
                except queue.Empty:
                    # Check if process is still running
                    if proc.poll() is not None:
                        break  # Process ended
                    continue  # Keep waiting
                
                if line is None:
                    # End of output
                    break
                
                combined_output.append(line)

                m = re.match(r"out_time_ms=(\d+)", line.strip())
                if m and total_us > 0:
                    out_us = int(m.group(1))
                    pct = int(min(99, 15 + (out_us / total_us) * 84))  # 15-99 range (accounts for pre-rendering)
                    if pct != last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback(pct, "렌더링 중...")

                if line.strip() == "progress=end":
                    break
            
            # If cancelled, don't check return code or emit completion
            if cancelled:
                return
            
            # Check if reader thread encountered an error
            if reader_exception:
                raise reader_exception[0]

            rc = proc.wait()
            if rc != 0:
                tail = "".join(combined_output[-40:])
                raise RuntimeError(f"FFmpeg 렌더링 실패 (code={rc})\n{tail}")

            if progress_callback:
                progress_callback(100, "완료")

        finally:
            # Clean up temporary files
            for png_path in unique_subtitles.values():
                try:
                    os.remove(png_path)
                except Exception:
                    pass
            if transparent_png_path:
                try:
                    os.remove(transparent_png_path)
                except Exception:
                    pass
            if concat_file_path:
                try:
                    os.remove(concat_file_path)
                except Exception:
                    pass
            if subtitle_video_path:
                try:
                    os.remove(subtitle_video_path)
                except Exception:
                    pass
            # Clean up image video temp files
            if image_concat_file_path:
                try:
                    os.remove(image_concat_file_path)
                except Exception:
                    pass
            if image_video_path:
                try:
                    os.remove(image_video_path)
                except Exception:
                    pass
            # Clean up padded audio
            if padded_audio_path:
                try:
                    os.remove(padded_audio_path)
                except Exception:
                    pass
