"""\
Video Renderer V2 - Hybrid FFmpeg Rendering

Phase 1: Create intermediate videos using concat demuxer (handles 100+ images/subtitles)
Phase 2: Final composition with FFmpeg direct audio mixing (precise timing via adelay)

This eliminates audio sync drift caused by pydub sequential merging.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import concurrent.futures
import os
import queue
import re
import subprocess
import tempfile
import threading

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QFont, QColor, QPainterPath, QPen, QFontMetrics

from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS

SUBTITLE_PADDING_H = 15
SUBTITLE_PADDING_V = 8
SUBTITLE_RADIUS = 4


@dataclass
class ImageSegment:
    image_path: str
    start_time: float
    end_time: float
    track: int = 0


@dataclass
class SubtitleSegment:
    text: str
    start_time: float
    end_time: float


class VideoRenderer:
    """Hybrid FFmpeg Video Renderer with precise audio sync"""

    def __init__(self, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT, fps: int = VIDEO_FPS):
        self.width = width
        self.height = height
        self.fps = fps
        self._gpu_encoder_name, self._gpu_encoder_opts = self._detect_best_encoder(True)
        self._cpu_encoder_name, self._cpu_encoder_opts = ("libx264", ["-preset", "medium", "-threads", "0"])
        self._encoder_name = self._gpu_encoder_name
        self._encoder_opts = self._gpu_encoder_opts

    def _test_encoder_works(self, encoder_name: str, encoder_opts: list[str]) -> bool:
        try:
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
                "-frames:v", "1", "-c:v", encoder_name, *encoder_opts,
                "-f", "null", "-"
            ]
            return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
        except Exception:
            return False

    def _detect_best_encoder(self, use_hw: bool = True) -> tuple[str, list[str]]:
        if use_hw:
            for enc, opts in [
                ("h264_nvenc", ["-preset", "p4", "-tune", "hq", "-rc", "vbr"]),
                ("h264_qsv", ["-preset", "medium"]),
                ("h264_amf", ["-quality", "balanced"]),
            ]:
                if self._test_encoder_works(enc, opts):
                    return (enc, opts)
        return ("libx264", ["-preset", "medium", "-threads", "0"])

    def _run_ffmpeg_with_progress(
        self, cmd: list[str], total_duration: float, start_pct: int, end_pct: int, label: str,
        progress_callback=None, cancel_check=None
    ) -> bool:
        """Run FFmpeg command and report real-time progress to callback"""
        if "-progress" not in cmd:
            cmd.extend(["-progress", "pipe:1", "-nostats"])

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )

        output_q: queue.Queue = queue.Queue()
        def reader():
            try:
                for line in proc.stdout:
                    output_q.put(line)
            except: pass
            finally: output_q.put(None)
        threading.Thread(target=reader, daemon=True).start()

        total_us = int(total_duration * 1_000_000)
        combined = []
        cancelled = False

        while True:
            if cancel_check and cancel_check():
                cancelled = True
                proc.terminate()
                try: proc.wait(timeout=5)
                except: proc.kill()
                break

            try:
                line = output_q.get(timeout=1)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            if line is None:
                break

            combined.append(line)
            m = re.match(r"out_time_ms=(\d+)", line.strip())
            if m and total_us > 0 and progress_callback:
                progress = int(m.group(1)) / total_us
                pct = int(start_pct + progress * (end_pct - start_pct))
                pct = max(start_pct, min(end_pct - 1, pct))
                progress_callback(pct, label)

            if line.strip() == "progress=end":
                break

        if cancelled:
            return False

        if proc.wait() != 0:
            # Show last part of log on error
            error_log = "".join(combined[-50:])
            raise RuntimeError(f"{label} 실패 (Exit {proc.returncode}):\n{error_log}")

        if progress_callback:
            progress_callback(end_pct, label)
        return True

    def _render_subtitle_png(self, text: str, settings: dict, width: int, height: int) -> str:
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

        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        font = QFont(font_name)
        font.setPixelSize(font_size)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        lines = [l for l in text.replace('\r\n', '\n').split('\n') if l.strip()]
        if not lines:
            painter.end()
            fd, path = tempfile.mkstemp(prefix="pbb_sub_", suffix=".png")
            os.close(fd)
            image.save(path, "PNG")
            return path

        line_height = metrics.height()
        leading = line_height * (line_spacing - 1.0)
        total_height = len(lines) * line_height + (len(lines) - 1) * leading
        max_width = max(metrics.horizontalAdvance(l) for l in lines)

        if pos_setting == 'Bottom':
            block_top = height - margin_v - total_height
        elif pos_setting == 'Top':
            block_top = margin_v
        else:
            block_top = (height - total_height) / 2

        if bg_enabled:
            bg_color.setAlpha(bg_alpha)
            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                int((width - max_width) / 2 - SUBTITLE_PADDING_H),
                int(block_top - SUBTITLE_PADDING_V),
                int(max_width + SUBTITLE_PADDING_H * 2),
                int(total_height + SUBTITLE_PADDING_V * 2),
                SUBTITLE_RADIUS, SUBTITLE_RADIUS
            )

        path = QPainterPath()
        y = block_top + metrics.ascent()
        for line in lines:
            x = (width - metrics.horizontalAdvance(line)) / 2
            path.addText(x, y, font, line)
            y += line_height + leading

        if outline_width > 0:
            pen = QPen(outline_color)
            pen.setWidthF(outline_width * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(font_color)
        painter.drawPath(path)
        painter.end()

        fd, png_path = tempfile.mkstemp(prefix="pbb_sub_", suffix=".png")
        os.close(fd)
        image.save(png_path, "PNG")
        return png_path

    def render(
        self,
        images: list[ImageSegment],
        subtitles: list[SubtitleSegment] | None = None,
        audio_clips: list = None,
        speaker_audio_map: dict = None,
        output_path: str | Path = "output.mp4",
        progress_callback=None,
        settings: dict = None,
        cancel_check=None,
    ) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        use_hw = settings.get('use_hw_accel', True) if settings else True
        self._encoder_name, self._encoder_opts = (
            (self._gpu_encoder_name, self._gpu_encoder_opts) if use_hw 
            else (self._cpu_encoder_name, self._cpu_encoder_opts)
        )
        print(f"Selected encoder: {self._encoder_name} (HW: {use_hw})")

        # Calculate total duration
        max_img = max((float(s.end_time) for s in images), default=0.0) if images else 0.0
        max_sub = max((float(s.end_time) for s in subtitles), default=0.0) if subtitles else 0.0
        max_aud = max((c.start + c.duration for c in audio_clips), default=0.0) if audio_clips else 0.0
        total_duration = max(max_img, max_sub, max_aud)
        if total_duration <= 0:
            raise RuntimeError("렌더링할 콘텐츠가 없습니다.")

        # =====================================================================
        # Build image timeline with quantized segment times
        # =====================================================================
        def _q(t): return round(float(t), 6)

        segments = []
        for seg in images:
            if seg.image_path and Path(seg.image_path).exists():
                s, e = max(0.0, float(seg.start_time)), min(total_duration, float(seg.end_time))
                if e > s:
                    segments.append((seg.image_path, _q(s), _q(e), seg.track))

        boundaries = {_q(0.0), _q(total_duration)}
        for _, s, e, _ in segments:
            boundaries.update([s, e])
        times = sorted(boundaries)

        visuals = []
        for t0, t1 in zip(times, times[1:]):
            if t1 <= t0:
                continue
            active = [(p, s, e, tr) for p, s, e, tr in segments if s <= t0 < e]
            img = max(active, key=lambda x: (x[3], x[1]))[0] if active else None
            dur = t1 - t0
            if visuals and visuals[-1][0] == img:
                visuals[-1] = (img, visuals[-1][1] + dur)
            else:
                visuals.append((img, dur))
        if not visuals:
            visuals = [(None, total_duration)]

        # Temp file paths
        temp_files = []
        image_video = None
        subtitle_video = None

        try:
            if cancel_check and cancel_check():
                return

            # =================================================================
            # PHASE 1A: Create image video using concat demuxer
            # =================================================================

            # Black frame for gaps (use JPG to match image formats in concat)
            black_path = None
            if any(p is None for p, _ in visuals):
                img = QImage(self.width, self.height, QImage.Format.Format_RGB32)
                img.fill(QColor(0, 0, 0))
                fd, black_path = tempfile.mkstemp(prefix="pbb_black_", suffix=".jpg")
                os.close(fd)
                img.save(black_path, "JPG", 100)
                temp_files.append(black_path)

            # Concat file
            fd, concat_path = tempfile.mkstemp(prefix="pbb_img_", suffix=".txt")
            os.close(fd)
            temp_files.append(concat_path)
            
            with open(concat_path, "w", encoding="utf-8") as f:
                last_path = None
                for img_path, dur in visuals:
                    p = (img_path or black_path).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{p}'\nduration {dur:.6f}\n")
                    last_path = p
                # FFmpeg concat demuxer bug: last file's duration is ignored
                # unless there's another file entry after it
                if last_path:
                    f.write(f"file '{last_path}'\n")

            fd, image_video = tempfile.mkstemp(prefix="pbb_imgv_", suffix=".mov")
            os.close(fd)
            temp_files.append(image_video)

            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "concat", "-safe", "0", "-i", concat_path,
                "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                       f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", self._encoder_name, *self._encoder_opts,
                "-pix_fmt", "yuv420p", "-r", str(self.fps),
                "-t", str(total_duration),  # Force exact duration
                image_video
            ]
            if not self._run_ffmpeg_with_progress(
                cmd, total_duration, 5, 15, "이미지 비디오 생성 중...", 
                progress_callback, cancel_check
            ): return

            # =================================================================
            # PHASE 1B: Create subtitle video using concat demuxer
            # =================================================================
            if subtitles and settings and settings.get('subtitle_enabled', True):
                if cancel_check and cancel_check():
                    return

                valid_subs = [
                    SubtitleSegment((s.text or "").strip(), float(s.start_time), float(s.end_time))
                    for s in subtitles if (s.text or "").strip() and float(s.end_time) > float(s.start_time)
                ]
                valid_subs.sort(key=lambda s: s.start_time)

                # Transparent PNG
                trans_img = QImage(self.width, self.height, QImage.Format.Format_ARGB32)
                trans_img.fill(Qt.GlobalColor.transparent)
                fd, trans_path = tempfile.mkstemp(prefix="pbb_trans_", suffix=".png")
                os.close(fd)
                trans_img.save(trans_path, "PNG")
                temp_files.append(trans_path)

                # Render unique subtitles
                unique_pngs = {}
                texts = list(set(s.text for s in valid_subs))
                if texts:
                    def render_one(t):
                        return (t, self._render_subtitle_png(t, settings, self.width, self.height))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(texts))) as ex:
                        for t, p in ex.map(lambda x: render_one(x), texts):
                            unique_pngs[t] = p
                            temp_files.append(p)

                # Build subtitle timeline with floating-point precision handling
                # Use a minimum duration threshold to avoid scientific notation issues
                MIN_DURATION = 0.001  # 1ms minimum
                sub_timeline = []
                cur = 0.0
                for sub in valid_subs:
                    gap = sub.start_time - cur
                    if gap > MIN_DURATION:
                        sub_timeline.append((trans_path, gap))
                    dur = sub.end_time - max(sub.start_time, cur)
                    if dur > MIN_DURATION:
                        sub_timeline.append((unique_pngs[sub.text], dur))
                        cur = sub.end_time
                remaining = total_duration - cur
                if remaining > MIN_DURATION:
                    sub_timeline.append((trans_path, remaining))

                # Concat file
                fd, sub_concat = tempfile.mkstemp(prefix="pbb_sub_", suffix=".txt")
                os.close(fd)
                temp_files.append(sub_concat)
                with open(sub_concat, "w", encoding="utf-8") as f:
                    last_sub = None
                    for p, d in sub_timeline:
                        # Format duration with fixed decimal places to avoid scientific notation
                        d_formatted = f"{d:.6f}"
                        escaped = p.replace(chr(92), '/').replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))
                        f.write(f"file '{escaped}'\nduration {d_formatted}\n")
                        last_sub = escaped
                    # FFmpeg concat demuxer bug fix
                    if last_sub:
                        f.write(f"file '{last_sub}'\n")

                fd, subtitle_video = tempfile.mkstemp(prefix="pbb_subv_", suffix=".mov")
                os.close(fd)
                temp_files.append(subtitle_video)

                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-f", "concat", "-safe", "0", "-i", sub_concat,
                    "-c:v", "png", "-pix_fmt", "rgba", "-r", str(self.fps),
                    "-t", str(total_duration),  # Force exact duration
                    subtitle_video
                ]
                if not self._run_ffmpeg_with_progress(
                    cmd, total_duration, 15, 25, "자막 비디오 생성 중...", 
                    progress_callback, cancel_check
                ): return

            # =================================================================
            # PHASE 2: Final composition with FFmpeg direct audio mixing
            # =================================================================
            if cancel_check and cancel_check():
                return
            if progress_callback:
                progress_callback(25, "최종 렌더링 준비 중...")

            # Build filter for audio mixing
            filter_lines = []
            audio_inputs = []
            audio_labels = []

            # Video overlay
            if subtitle_video and Path(subtitle_video).exists():
                filter_lines.append("[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[vout]")
                audio_input_offset = 2
            else:
                filter_lines.append("[0:v]format=yuv420p[vout]")
                audio_input_offset = 1

            # Audio clips with precise adelay
            if audio_clips and speaker_audio_map:
                speaker_to_idx = {}
                for speaker, audio_file in speaker_audio_map.items():
                    if audio_file and Path(audio_file).exists():
                        audio_inputs.append(audio_file)
                        speaker_to_idx[speaker] = audio_input_offset + len(audio_inputs) - 1

                for i, clip in enumerate(audio_clips):
                    speaker = getattr(clip, 'speaker', None)
                    if speaker not in speaker_to_idx:
                        continue
                    idx = speaker_to_idx[speaker]
                    offset = float(getattr(clip, 'offset', 0.0))
                    dur = float(clip.duration)
                    delay_ms = int(float(clip.start) * 1000)
                    label = f"a{i}"
                    filter_lines.append(
                        f"[{idx}:a]atrim=start={offset:.6f}:duration={dur:.6f},"
                        f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms}[{label}]"
                    )
                    audio_labels.append(f"[{label}]")

            # Audio mix
            if len(audio_labels) > 1:
                filter_lines.append(
                    f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:normalize=0,"
                    f"apad=whole_dur={total_duration:.6f}[aout]"
                )
            elif audio_labels:
                filter_lines.append(f"{audio_labels[0]}apad=whole_dur={total_duration:.6f}[aout]")
            else:
                filter_lines.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{total_duration:.6f}[aout]")

            # Save filter script
            fd, filter_path = tempfile.mkstemp(prefix="pbb_filter_", suffix=".txt")
            os.close(fd)
            temp_files.append(filter_path)
            with open(filter_path, "w", encoding="utf-8") as f:
                f.write(";\n".join(filter_lines))



            # Build final command
            cmd = ["ffmpeg", "-y", "-hide_banner"]
            cmd.extend(["-i", image_video])
            if subtitle_video:
                cmd.extend(["-i", subtitle_video])
            for af in audio_inputs:
                cmd.extend(["-i", af])
            cmd.extend([
                "-filter_complex_script", filter_path,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", self._encoder_name, *self._encoder_opts,
                "-pix_fmt", "yuv420p", "-r", str(self.fps),
                "-c:a", "aac", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats",
                str(output_path),
            ])

            # Execute
            if not self._run_ffmpeg_with_progress(
                cmd, total_duration, 30, 100, "렌더링 중...", 
                progress_callback, cancel_check
            ): return

            if progress_callback:
                progress_callback(100, "완료")

        finally:
            for f in temp_files:
                try: os.remove(f)
                except: pass
