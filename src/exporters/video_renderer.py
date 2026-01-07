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
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        audio_path = str(audio_path)
        if not audio_path or not Path(audio_path).exists():
            raise RuntimeError("오디오 파일을 찾을 수 없습니다.")

        total_duration = self._get_audio_duration_seconds(audio_path)
        if total_duration <= 0:
            raise RuntimeError("오디오 길이를 확인할 수 없습니다.")

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
                filter_parts.append(
                    f"[{i}:v]scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                    f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
                )

            concat_inputs = "".join([f"[v{i}]" for i in range(len(visuals))])
            filter_parts.append(f"{concat_inputs}concat=n={len(visuals)}:v=1:a=0[vcat]")

            if srt_path:
                srt_escaped = self._escape_path_for_ffmpeg_filter(srt_path)
                # Match preview-like size: smaller font, bottom-center.
                style = "Fontname=Malgun Gothic,Fontsize=32,Outline=2,Shadow=0,Alignment=2,MarginV=48"
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
