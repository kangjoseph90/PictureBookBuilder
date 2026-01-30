from PyQt6.QtCore import QThread, pyqtSignal
from typing import Optional, Dict, List
import sys
import traceback

# Import types for type checking if needed
from runtime_config import get_config

class RenderThread(QThread):
    """Background thread for video rendering (ALL-IN-ONE FFmpeg)"""
    progress = pyqtSignal(int, str)  # progress %, status message
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, image_clips, audio_clips, subtitle_clips, output_path, 
                 render_settings=None, speaker_audio_map=None):
        super().__init__()
        self.image_clips = image_clips
        self.audio_clips = audio_clips  # Individual audio clips with timing
        self.subtitle_clips = subtitle_clips
        self.output_path = output_path
        self.render_settings = render_settings
        self.speaker_audio_map = speaker_audio_map or {}
        self._cancelled = False
    
    def cancel(self):
        """Request cancellation of rendering"""
        self._cancelled = True
    
    def run(self):
        try:
            from exporters.video_renderer import VideoRenderer, ImageSegment, SubtitleSegment
            
            self.progress.emit(0, "렌더링 준비 중...")
            
            # Convert clips to renderer format
            images = [
                ImageSegment(
                    image_path=img.image_path or "",
                    start_time=img.start,
                    end_time=img.start + img.duration,
                    track=getattr(img, "track", 0)
                )
                for img in self.image_clips
            ]
            
            subtitles = None
            if self.subtitle_clips:
                subtitles = [
                    SubtitleSegment(
                        text=sub.name,
                        start_time=sub.start,
                        end_time=sub.start + sub.duration
                    )
                    for sub in self.subtitle_clips
                ]
            
            # Initialize renderer with settings if available
            width = self.render_settings.get('width', 1920) if self.render_settings else 1920
            height = self.render_settings.get('height', 1080) if self.render_settings else 1080
            fps = self.render_settings.get('fps', 30) if self.render_settings else 30

            renderer = VideoRenderer(width=width, height=height, fps=fps)
            self.progress.emit(5, "비디오 렌더링 중...")
            
            # Render with all-in-one approach
            renderer.render(
                images=images,
                subtitles=subtitles,
                audio_clips=self.audio_clips,
                speaker_audio_map=self.speaker_audio_map,
                output_path=str(self.output_path),
                progress_callback=self._on_render_progress,
                settings=self.render_settings,
                cancel_check=lambda: self._cancelled
            )
            
            if self._cancelled:
                self.finished.emit(False, "사용자가 렌더링을 취소했습니다.")
            else:
                self.progress.emit(100, "완료")
                self.finished.emit(True, f"영상이 저장되었습니다:\n{self.output_path}")
            
        except Exception as e:
            traceback.print_exc()
            if self._cancelled:
                self.finished.emit(False, "사용자가 렌더링을 취소했습니다.")
            else:
                self.finished.emit(False, f"렌더링 실패: {str(e)}")
    
    def _on_render_progress(self, progress: int, message: str):
        """Callback from renderer for progress updates"""
        self.progress.emit(progress, message)


class ProcessingThread(QThread):
    """Background thread for audio processing"""
    progress = pyqtSignal(int, str)  # progress %, status message
    finished = pyqtSignal(bool, str, object)  # success, message, result data
    
    def __init__(self, script_content: str, speaker_audio_map: dict, image_folder: str):
        super().__init__()
        self.script_content = script_content
        self.speaker_audio_map = speaker_audio_map  # Now directly passed
        self.image_folder = image_folder
        self._cancelled = False
    
    def cancel(self):
        """Request cancellation of the processing"""
        self._cancelled = True
    
    def _check_cancelled(self) -> bool:
        """Check if cancellation was requested and emit finished signal if so"""
        if self._cancelled:
            self.finished.emit(False, "사용자가 취소함", None)
            return True
        return False
    
    def run(self):
        # Store model references for cleanup
        self._transcriber = None
        self._vad = None
        self._qwen3_aligner = None
        
        try:
            from core.script_parser import ScriptParser
            from core.transcriber import Transcriber
            from core.aligner import Aligner
            from core.vad_processor import VADProcessor
            from pydub import AudioSegment
            
            # Step 1: Parse script
            self.progress.emit(10, "스크립트 파싱 중...")
            parser = ScriptParser()
            dialogues = parser.parse_text(self.script_content)
            speakers = parser.get_unique_speakers(dialogues)
            
            # Step 2: Validate speaker-audio mapping
            self.progress.emit(15, "오디오 파일 확인 중...")
            missing_speakers = []
            for speaker in speakers:
                if speaker not in self.speaker_audio_map or not self.speaker_audio_map[speaker]:
                    missing_speakers.append(speaker)
            
            if missing_speakers:
                self.finished.emit(False, f"오디오 파일이 지정되지 않은 화자: {', '.join(missing_speakers)}", None)
                return
            
            # Step 2.5: Build initial prompt from script for Whisper (if used)
            script_text = ' '.join(d.text for d in dialogues)
            initial_prompt = self._build_whisper_prompt(speakers, script_text)
            print(f"Whisper initial prompt: {initial_prompt}")
            
            # Check for cancellation before heavy processing
            if self._check_cancelled():
                return
            
            config = get_config()
            transcriptions = {}

            if config.use_qwen3_forced_aligner:
                # Step 3: Qwen3 ForcedAligner (experimental)
                self.progress.emit(20, "Qwen3 ForcedAligner 로딩 중...")
                from core.qwen3_forced_aligner import Qwen3ForcedAlignerWrapper

                if self._check_cancelled():
                    return

                qwen_aligner = Qwen3ForcedAlignerWrapper(
                    max_audio_seconds=config.qwen3_max_audio_seconds
                )
                self._qwen3_aligner = qwen_aligner

                if self._check_cancelled():
                    return

                # Step 4: Align dialogues with Qwen3
                self.progress.emit(50, "Qwen3 대사 정렬 중...")
                aligned = qwen_aligner.align_all(
                    dialogues,
                    self.speaker_audio_map,
                    language=config.whisper_language,
                )
            else:
                # Step 3: Transcribe audio files (Whisper)
                self.progress.emit(20, "Whisper 모델 로딩 중...")
                self._transcriber = Transcriber()

                # Check for cancellation after model loading
                if self._check_cancelled():
                    return

                total_speakers = len(self.speaker_audio_map)
                for i, (speaker, audio_path) in enumerate(self.speaker_audio_map.items()):
                    if audio_path:
                        progress = 20 + int((i / total_speakers) * 25)
                        self.progress.emit(progress, f"Whisper 변환 중: {speaker}...")
                        # Get configured language
                        whisper_lang = config.whisper_language
                        if whisper_lang == "auto":
                            whisper_lang = None
                            
                        transcriptions[speaker] = self._transcriber.transcribe(
                            audio_path, 
                            language=whisper_lang,
                            initial_prompt=initial_prompt
                        )
                        
                        # Check for cancellation after each speaker
                        if self._check_cancelled():
                            return

                # Step 4: Align dialogues
                self.progress.emit(50, "대사 정렬 중...")
                aligner = Aligner()
                aligned = aligner.align_all(dialogues, transcriptions)
            
            # Check for cancellation before VAD/padding
            if self._check_cancelled():
                return

            if config.use_qwen3_forced_aligner:
                # Apply padding only (no VAD) for Qwen3
                padding_sec = max(0.0, float(config.vad_padding_ms) / 1000.0)
                if padding_sec > 0:
                    speaker_durations: dict[str, float] = {}
                    for speaker, audio_path in self.speaker_audio_map.items():
                        if audio_path:
                            try:
                                audio = AudioSegment.from_file(audio_path)
                                speaker_durations[speaker] = float(len(audio)) / 1000.0
                            except Exception as e:
                                print(f"Padding duration read failed for {speaker}: {e}")

                    for segment in aligned:
                        speaker = segment.dialogue.speaker
                        duration = speaker_durations.get(speaker, None)
                        start_time = max(0.0, segment.start_time - padding_sec)
                        if duration is not None:
                            end_time = min(duration, segment.end_time + padding_sec)
                        else:
                            end_time = segment.end_time + padding_sec

                        segment.start_time = start_time
                        segment.end_time = end_time
            else:
                # Step 5: VAD Refinement - refine segment boundaries with Silero VAD
                self.progress.emit(60, "VAD로 경계 보정 중...")
                self._vad = VADProcessor()

                # Load speaker audio files for VAD
                speaker_audio: dict[str, AudioSegment] = {}
                for speaker, audio_path in self.speaker_audio_map.items():
                    if audio_path:
                        speaker_audio[speaker] = AudioSegment.from_file(audio_path)

                # Refine each aligned segment with VAD
                # Track previous end time per speaker to avoid overlap
                prev_end_by_speaker: dict[str, float] = {}

                total_segments = len(aligned)
                for i, segment in enumerate(aligned):
                    if i % 10 == 0:  # Update progress every 10 segments
                        progress = 60 + int((i / total_segments) * 25)
                        self.progress.emit(progress, f"VAD 보정 중 ({i+1}/{total_segments})...")
                    
                    speaker = segment.dialogue.speaker
                    if speaker in speaker_audio:
                        audio = speaker_audio[speaker]
                        
                        # Get previous end time for this speaker
                        prev_end = prev_end_by_speaker.get(speaker, None)
                        
                        # Refine boundaries using VAD with previous segment constraint
                        try:
                            refined_start, refined_end, raw_voice_end = self._vad.trim_segment_boundaries(
                                audio,
                                segment.start_time,
                                segment.end_time,
                                prev_end_time=prev_end
                            )
                            
                            # Update segment with refined boundaries
                            segment.start_time = refined_start
                            segment.end_time = refined_end
                            
                            # Store raw voice end (without padding) for next iteration
                            # This allows next segment's analysis to start closer to actual voice end
                            prev_end_by_speaker[speaker] = raw_voice_end
                        except Exception as e:
                            # If VAD fails, keep original boundaries
                            print(f"VAD refinement failed for segment {i}: {e}")
                            prev_end_by_speaker[speaker] = segment.end_time

            # Step 6: Build result
            self.progress.emit(90, "결과 생성 중...")
            result = {
                'dialogues': dialogues,
                'speakers': speakers,
                'transcriptions': transcriptions,
                'aligned': aligned,
                'speaker_audio_map': self.speaker_audio_map
            }
            
            self.progress.emit(100, "완료!")
            self.finished.emit(True, f"처리 완료! {len(aligned)}개 대사 정렬됨.", result)
            
        except Exception as e:
            traceback.print_exc()
            self.finished.emit(False, f"오류 발생: {str(e)}", None)
        
        finally:
            # Cleanup: Release models from GPU memory
            self._cleanup_models()
    
    def _build_whisper_prompt(self, speakers: list[str], script_text: str, max_length: int = 200) -> str:
        """Build initial prompt for Whisper from speakers and script keywords
        
        Args:
            speakers: List of speaker names (e.g., ["흥부", "놀부"])
            script_text: Full script text
            max_length: Maximum prompt length
            
        Returns:
            Comma-separated prompt string
        """
        import re
        from collections import Counter
        
        # Start with speaker names
        prompt_parts = list(speakers)
        
        # Extract 3+ char Korean words and count frequency
        words = re.findall(r'[가-힣]{3,}', script_text)
        word_freq = Counter(words)
        
        # Add top frequent words (excluding already added speakers)
        speaker_set = set(speakers)
        top_words = [w for w, _ in word_freq.most_common(30) if w not in speaker_set]
        prompt_parts.extend(top_words[:20])
        
        # Join and truncate to max_length
        result = ', '.join(dict.fromkeys(prompt_parts))  # Remove duplicates, keep order
        if len(result) > max_length:
            result = result[:max_length].rsplit(', ', 1)[0]
        
        return result
    
    def _cleanup_models(self):
        """Clean up models and release GPU memory after processing"""
        import gc
        
        try:
            # Delete Whisper model
            if hasattr(self, '_transcriber') and self._transcriber is not None:
                if hasattr(self._transcriber, 'model'):
                    del self._transcriber.model
                del self._transcriber
                self._transcriber = None
            
            # Delete VAD model
            if hasattr(self, '_vad') and self._vad is not None:
                if hasattr(self._vad, 'model'):
                    del self._vad.model
                del self._vad
                self._vad = None

            # Delete Qwen3 aligner model
            if hasattr(self, '_qwen3_aligner') and self._qwen3_aligner is not None:
                if hasattr(self._qwen3_aligner, 'model'):
                    del self._qwen3_aligner.model
                del self._qwen3_aligner
                self._qwen3_aligner = None
            
            # Force garbage collection
            gc.collect()
            
            # Clear GPU cache if torch is available
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    print("GPU memory released successfully")
            except ImportError:
                pass
                
        except Exception as e:
            print(f"Warning: Model cleanup failed: {e}")
