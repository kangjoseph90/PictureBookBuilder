"""\
Video Renderer - Render final video with images, audio, and subtitles.

This implementation uses FFmpeg directly (no per-frame Python rendering),
which is typically much faster than MoviePy for slideshow-style videos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import contextlib
import os
import re
import subprocess
import tempfile
import wave

from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


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
        font_size = s.get('font_size', 32)
        line_spacing = s.get('line_spacing', 1.4)
        
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
            outline_width = w if outline_enabled else 0
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
            # Style line - Note Alignment is set to 5 (Center) as we might predominantly rely on \pos or center logic
            # Actually, let's keep base_align for the style to have sensible defaults
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

                # Calculate positions
                # Effective line height for spacing
                # This is the distance between the vertical anchor points of consecutive lines
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
                        # offset_from_bottom for line `i` is `(num_lines - 1 - i)`.
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
                    # Calculate the total vertical span of the text block (from center of first to center of last line).
                    total_span = (num_lines - 1) * eff_h
                    
                    # The center of the entire block should be at height / 2.
                    # The center of the first line will be: (height / 2) - (total_span / 2).
                    first_line_center_y = (height / 2) - (total_span / 2)
                    
                    for i in range(num_lines):
                        y = first_line_center_y + (i * eff_h)
                        y_positions.append(y)

                # Write events for each line
                for i, line in enumerate(lines):
                    if not line.strip(): continue # Skip empty lines if they result from splitting

                    y = y_positions[i]
                    
                    # Override alignment for this specific line to ensure \pos works as expected logic
                    # \an2 for Bottom/Base, \an8 for Top, \an5 for Center
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
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        audio_path = str(audio_path)
        if not audio_path or not Path(audio_path).exists():
            raise RuntimeError("오디오 파일을 찾을 수 없습니다.")

        audio_duration = self._get_audio_duration_seconds(audio_path)
        if audio_duration <= 0:
            raise RuntimeError("오디오 길이를 확인할 수 없습니다.")
        
        # Use the maximum of audio duration and image clip end times
        # This ensures images that extend beyond the audio are rendered correctly
        max_image_end = max((float(seg.end_time) for seg in images), default=0.0) if images else 0.0
        total_duration = max(audio_duration, max_image_end)

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
            # Quantize to milliseconds to avoid floating point boundary noise
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
                # Prefer higher track; break ties by later start_time (more specific override)
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

        # Ensure non-empty visuals
        if not visuals:
            visuals = [(None, total_duration)]

        ass_path = None
        if subtitles:
            ass_path = self._write_temp_ass(subtitles, settings, self.width, self.height)

        try:
            if progress_callback:
                progress_callback(5, "FFmpeg 렌더 준비 중...")

            cmd: list[str] = ["ffmpeg", "-y", "-hide_banner"]

            # Visual inputs
            for (img_path, dur) in visuals:
                dur = max(0.001, float(dur))
                if img_path is None:
                    cmd += [
                        "-f",
                        "lavfi",
                        "-t",
                        f"{dur}",
                        "-i",
                        f"color=c=black:s={self.width}x{self.height}:r={self.fps}",
                    ]
                else:
                    cmd += ["-loop", "1", "-t", f"{dur}", "-i", str(img_path)]

            # Audio input
            audio_input_index = len(visuals)
            cmd += ["-i", audio_path]

            # Filtergraph: Transform each input to match target resolution
            filter_parts: list[str] = []
            for i in range(len(visuals)):
                filter_parts.append(
                    f"[{i}:v]scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                    f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
                )

            concat_inputs = "".join([f"[v{i}]" for i in range(len(visuals))])
            filter_parts.append(f"{concat_inputs}concat=n={len(visuals)}:v=1:a=0[vcat]")

            if ass_path:
                ass_escaped = self._escape_path_for_ffmpeg_filter(ass_path)
                # Use subtitles filter with the ASS file. It will handle style and resolution perfectly.
                filter_parts.append(f"[vcat]subtitles='{ass_escaped}'[vout]")
            else:
                filter_parts.append("[vcat]null[vout]")

            filter_complex = ";".join(filter_parts)

            # Progress parsing via -progress pipe:1
            cmd += [
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                f"{audio_input_index}:a:0",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(self.fps),
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
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

            combined_output: list[str] = []
            for line in proc.stdout:
                combined_output.append(line)

                m = re.match(r"out_time_ms=(\d+)", line.strip())
                if m and total_us > 0:
                    out_us = int(m.group(1))
                    pct = int(min(99, (out_us / total_us) * 100))
                    if pct != last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback(pct, "렌더링 중...")

                if line.strip() == "progress=end":
                    break

            rc = proc.wait()
            if rc != 0:
                tail = "".join(combined_output[-40:])
                raise RuntimeError(f"FFmpeg 렌더링 실패 (code={rc})\n{tail}")

            if progress_callback:
                progress_callback(100, "완료")

        finally:
            if ass_path:
                try:
                    os.remove(ass_path)
                except Exception:
                    pass
