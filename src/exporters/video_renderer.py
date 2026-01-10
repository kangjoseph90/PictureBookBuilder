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

    def _format_srt_time(self, seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        ms = int(round(seconds * 1000.0))
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1000
        ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _write_temp_srt(self, subtitles: list[SubtitleSegment]) -> str:
        subs = [
            SubtitleSegment(text=(s.text or "").strip(), start_time=float(s.start_time), end_time=float(s.end_time))
            for s in subtitles
            if (s.text or "").strip() and float(s.end_time) > float(s.start_time)
        ]
        subs.sort(key=lambda s: (s.start_time, s.end_time))

        fd, path = tempfile.mkstemp(prefix="pbb_", suffix=".srt")
        os.close(fd)

        with open(path, "w", encoding="utf-8") as f:
            for i, s in enumerate(subs, start=1):
                f.write(f"{i}\n")
                f.write(f"{self._format_srt_time(s.start_time)} --> {self._format_srt_time(s.end_time)}\n")
                f.write(s.text + "\n\n")

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
        # If image clips overlap, choose the one on the highest track (top-most).
        segments: list[ImageSegment] = []
        for seg in images:
            if not seg.image_path:
                continue
            start = max(0.0, float(seg.start_time))
            end = min(total_duration, float(seg.end_time))
            if end <= start:
                continue
            if not Path(seg.image_path).exists():
                # Missing image -> treat as black
                continue
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

        srt_path = None
        if subtitles:
            srt_path = self._write_temp_srt(subtitles)

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

            # Filtergraph
            filter_parts: list[str] = []
            for i in range(len(visuals)):
                # Apply scaling using self.width and self.height
                filter_parts.append(
                    f"[{i}:v]scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                    f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
                )

            concat_inputs = "".join([f"[v{i}]" for i in range(len(visuals))])
            filter_parts.append(f"{concat_inputs}concat=n={len(visuals)}:v=1:a=0[vcat]")

            if srt_path:
                srt_escaped = self._escape_path_for_ffmpeg_filter(srt_path)

                # Default style settings
                s = settings or {}
                font_name = s.get('font_family', 'Malgun Gothic')
                # Scale font size based on video height if necessary, but here we treat it as pixels for simplicity
                # or match the logic user expects.
                # Note: FFmpeg Fontsize is height in pixels.
                font_size = s.get('font_size', 32)

                # Colors are usually &HBBGGRR in ASS/SSA, but force_style might take different formats.
                # force_style uses ASS style parameters.
                # ASS color format: &HAABBGGRR (Alpha, Blue, Green, Red) in hex.
                # The user provides #RRGGBB via settings.

                def to_ass_color(hex_color: str, alpha: int = 0) -> str:
                    # Convert #RRGGBB to &HAABBGGRR
                    # Alpha: 0 (opaque) to 255 (transparent) in Qt
                    # ASS Alpha: 00 (opaque) to FF (transparent)

                    if hex_color.startswith('#'):
                        hex_color = hex_color[1:]

                    if len(hex_color) == 6:
                        r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
                        # ASS is BBGGRR
                        return f"&H{alpha:02X}{b}{g}{r}"
                    return f"&H{alpha:02X}FFFFFF"

                font_color = to_ass_color(s.get('font_color', '#FFFFFF'), 0) # Opaque text

                # Outline
                outline = s.get('outline_width', 2) if s.get('outline_enabled', True) else 0
                outline_color = to_ass_color(s.get('outline_color', '#000000'), 0)

                # Background (Box)
                # ASS BorderStyle=3 is "Opaque Box"
                # BorderStyle=1 is "Outline + Shadow"
                # We need to switch based on bg_enabled
                border_style = 3 if s.get('bg_enabled', False) else 1

                # If bg_enabled, 'Outline' becomes the box padding or border?
                # Actually, standard force_style doesn't support complex box styling easily.
                # BorderStyle=3 puts a box behind the text. The color of the box is OutlineColour.
                # So if BorderStyle=3:
                #   PrimaryColour = Text Color
                #   OutlineColour = Background Color
                #   Outline = Background Padding (roughly)

                # However, user wants BOTH Outline AND Background sometimes?
                # Standard SRT/subtitles filter is limited. ASS supports it fully but force_style is a hack.
                # Let's try to map as best as possible.

                # Strategy:
                # If BG enabled: Use BorderStyle=3. OutlineColour=BG Color.
                #   We lose text outline in this mode usually with standard filters.
                # If BG disabled: Use BorderStyle=1. OutlineColour=Outline Color.

                # Wait, BorderStyle=4 is "Box with background" in libass? No.
                # Default is 1 (Outline). 3 is Opaque Box.

                if s.get('bg_enabled', False):
                    border_style = 3
                    # Background color (BackColour) isn't fully supported in all versions for Box?
                    # Actually for BorderStyle=3, OutlineColour is the box background color.
                    # Text outline is lost.

                    # Wait, let's look at standard ASS.
                    # BorderStyle=1: Outline + DropShadow
                    # BorderStyle=3: Opaque Box

                    # If we want BG, we use BorderStyle=3.
                    # The color of the box is usually derived from OutlineColour?
                    # Let's check docs: "BorderStyle: 1=Outline, 3=Opaque Box"

                    # Mapping:
                    # Fontname, Fontsize
                    # PrimaryColour -> Text Color
                    # OutlineColour -> Box Color (if BS=3) or Outline Color (if BS=1)
                    # BackColour -> Shadow Color (if BS=1)

                    # We might need to compromise or generate a full .ass file instead of .srt + force_style
                    # for full control (Text Outline + Box Background).
                    # But for now, let's stick to simple mapping.

                    # If BG enabled, prioritize BG over Outline?
                    # Or maybe we can use BackColour for background if alignment allows?

                    # Let's stick to:
                    # If BG enabled -> BorderStyle=3 (Box). Box color = bg_color. Text outline = lost.
                    # If BG disabled -> BorderStyle=1 (Outline). Outline color = outline_color.

                    if s.get('bg_enabled'):
                        border_style = 3
                        # In Qt alpha is 0-255 (255 opaque). In Settings we stored 0-255 (255 opaque?).
                        # Wait, Qt QColor.alpha() is 255 for opaque.
                        # settings['bg_alpha'] is from slider 0-255.
                        # ASS Alpha: 00 (Opaque) - FF (Transparent).
                        # So we need to invert.
                        qt_alpha = s.get('bg_alpha', 160)
                        ass_alpha = 255 - qt_alpha

                        outline_color_ass = to_ass_color(s.get('bg_color', '#000000'), ass_alpha)

                        # Apply to OutlineColour
                        ass_outline_colour = outline_color_ass
                        ass_outline_width = 0 # No extra expansion? Or use margin?

                    else:
                        border_style = 1
                        outline_color_ass = to_ass_color(s.get('outline_color', '#000000'), 0)
                        ass_outline_colour = outline_color_ass
                        ass_outline_width = outline

                else:
                    # Default if logic fails
                    border_style = 1
                    ass_outline_colour = to_ass_color(s.get('outline_color', '#000000'), 0)
                    ass_outline_width = outline

                # Alignment
                # ASS: 1=Left, 2=Center, 3=Right (Subtitles) - Legacy numpad?
                # 1=SW, 2=S, 3=SE, 5=NW ...
                # Standard alignment: 2 (Bottom Center).
                # 10 (Center Center) ??
                # Numpad layout:
                # 7 8 9 (Top)
                # 4 5 6 (Mid)
                # 1 2 3 (Bot)

                pos = s.get('position', 'Bottom')
                if pos == 'Top':
                    alignment = 8 # Top Center
                elif pos == 'Center':
                    alignment = 5 # Middle Center
                else:
                    alignment = 2 # Bottom Center

                margin_v = s.get('margin_v', 48)

                style = (
                    f"Fontname={font_name},"
                    f"Fontsize={font_size},"
                    f"PrimaryColour={font_color},"
                    f"OutlineColour={ass_outline_colour},"
                    f"BorderStyle={border_style},"
                    f"Outline={ass_outline_width},"
                    f"Shadow=0,"
                    f"Alignment={alignment},"
                    f"MarginV={margin_v}"
                )

                filter_parts.append(f"[vcat]subtitles='{srt_escaped}':force_style='{style}'[vout]")
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
            if srt_path:
                try:
                    os.remove(srt_path)
                except Exception:
                    pass
