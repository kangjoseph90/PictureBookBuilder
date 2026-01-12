"""
Main Window - Primary application window with speaker-audio mapping
"""
import sys
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QSplitter, QTextEdit, QSlider, QSpinBox, QDoubleSpinBox, QProgressBar, QDialog,
    QGroupBox, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QToolBar, QStyle, QMenu, QStatusBar, QSizePolicy,
    QStyledItemDelegate, QStyleOptionViewItem, QAbstractItemView,
    QLineEdit, QPlainTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QEvent, QRect
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPalette, QFontMetrics
from typing import TYPE_CHECKING

import copy
from .timeline_widget import TimelineWidget
from .clip import TimelineClip
from .undo_system import UndoStack, ModifyClipsCommand, AddRemoveClipsCommand, ReplaceAllClipsCommand, MacroCommand
from .preview_widget import PreviewWidget
from .settings_widget import SettingsWidget, SettingsDialog
from .render_settings_dialog import RenderSettingsDialog
from .theme import ModernDarkTheme
from config import DEFAULT_GAP_SECONDS
from runtime_config import get_config, set_config, RuntimeConfig

if TYPE_CHECKING:
    from pydub import AudioSegment



class ImageGridDelegate(QStyledItemDelegate):
    """Custom delegate to render icons with text below in ListMode"""
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        
        # Draw standard background (selection/hover)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        
        # Layout metrics
        rect = option.rect
        icon_size = 64
        spacing_text = 2 # Closer text
        
        # Draw Icon (Centered horizontally, Top aligned)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        actual_icon_h = 0
        if icon:
            pixmap = icon.pixmap(icon_size, icon_size)
            if not pixmap.isNull():
                # Center pixmap horizontally in the cell
                x = rect.x() + (rect.width() - pixmap.width()) // 2
                y = rect.y() + 5 # Small top padding
                painter.drawPixmap(x, y, pixmap)
                actual_icon_h = pixmap.height()
            
        # Draw Text (Centered horizontally, Below icon)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            # Text area below icon - dynamic Y based on actual icon height
            y_offset = (actual_icon_h if actual_icon_h > 0 else icon_size) + 5 + spacing_text
            text_rect = QRect(rect.x(), rect.y() + int(y_offset), rect.width(), 20)
            
            # Elide text if needed
            fm = QFontMetrics(option.font)
            elided_text = fm.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width() - 4)
            
            # Text color
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
            if option.state & QStyle.StateFlag.State_Selected:
                 painter.setPen(option.palette.color(QPalette.ColorRole.HighlightedText))
                 
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
            
    def sizeHint(self, option, index):
        # Reduced height (110 -> 100) for tighter spacing
        return QSize(100, 100)


class DraggableImageListWidget(QListWidget):
    """Custom QListWidget that provides file URLs when dragging for external drop targets"""
    
    def mimeData(self, items):
        """Override to include file URLs in the mime data for drag operations"""
        mime = super().mimeData(items)
        
        # Add file URLs for the dragged items
        urls = []
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                from PyQt6.QtCore import QUrl
                urls.append(QUrl.fromLocalFile(path))
        
        if urls:
            mime.setUrls(urls)
        
        return mime
    
    def supportedDropActions(self):
        """Support copy action for external drops while maintaining internal move"""
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction


class ProgressDialog(QDialog):
    """Processing progress dialog with refined UI matching settings style"""
    
    cancelled = pyqtSignal()
    
    def __init__(self, parent=None, title="오디오 처리"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(400)
        self.setWindowFlags(
            Qt.WindowType.Dialog | 
            Qt.WindowType.CustomizeWindowHint | 
            Qt.WindowType.WindowTitleHint
        )
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 20)
        layout.setSpacing(15)
        
        # Status label with slightly better font/color
        self.status_label = QLabel("준비 중...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-weight: bold; color: #CCCCCC;")
        layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        layout.addWidget(self.progress_bar)
        
        # Spacer
        layout.addSpacing(5)
        
        # Cancel button centered
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setFixedWidth(100)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        self._is_cancelled = False
        
        # Auto-adjust height
        self.adjustSize()
        self.setFixedSize(self.width(), self.sizeHint().height())
    
    def update_progress(self, percent: int, message: str):
        """Update progress bar and status message"""
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)
    
    def _on_cancel(self):
        """Handle cancel button click"""
        self._is_cancelled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("취소 중...")
        self.status_label.setText("취소 중... 잠시 기다려주세요")
        self.cancelled.emit()
    
    def closeEvent(self, event):
        """Prevent closing dialog by X button during processing"""
        if not self._is_cancelled:
            event.ignore()
        else:
            event.accept()


class RenderThread(QThread):
    """Background thread for video rendering"""
    progress = pyqtSignal(int, str)  # progress %, status message
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, image_clips, audio_path, subtitle_clips, output_path, render_settings=None):
        super().__init__()
        self.image_clips = image_clips
        self.audio_path = audio_path
        self.subtitle_clips = subtitle_clips
        self.output_path = output_path
        self.render_settings = render_settings
    
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
            self.progress.emit(10, "비디오 렌더링 중...")
            
            # Render with progress callback
            renderer.render(
                images=images,
                audio_path=str(self.audio_path),
                subtitles=subtitles,
                output_path=str(self.output_path),
                progress_callback=self._on_render_progress,
                settings=self.render_settings
            )
            
            self.progress.emit(100, "완료")
            self.finished.emit(True, f"영상이 저장되었습니다:\n{self.output_path}")
            
        except Exception as e:
            self.finished.emit(False, f"렌더링 실패: {str(e)}")
    
    def _on_render_progress(self, progress: int, message: str):
        """Callback from renderer for progress updates"""
        self.progress.emit(progress, message)


class ProcessingThread(QThread):
    """Background thread for audio processing"""
    progress = pyqtSignal(int, str)  # progress %, status message
    finished = pyqtSignal(bool, str, object)  # success, message, result data
    
    def __init__(self, script_path: str, speaker_audio_map: dict, image_folder: str):
        super().__init__()
        self.script_path = script_path
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
        
        try:
            from core.script_parser import ScriptParser
            from core.transcriber import Transcriber
            from core.aligner import Aligner
            from core.vad_processor import VADProcessor
            from pydub import AudioSegment
            
            # Step 1: Parse script
            self.progress.emit(10, "스크립트 파싱 중...")
            parser = ScriptParser()
            dialogues = parser.parse_file(self.script_path)
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
            
            # Step 2.5: Build initial prompt from script for Whisper
            script_text = ' '.join(d.text for d in dialogues)
            initial_prompt = self._build_whisper_prompt(speakers, script_text)
            print(f"Whisper initial prompt: {initial_prompt}")
            
            # Check for cancellation before heavy processing
            if self._check_cancelled():
                return
            
            # Step 3: Transcribe audio files
            self.progress.emit(20, "Whisper 모델 로딩 중...")
            self._transcriber = Transcriber()
            
            # Check for cancellation after model loading
            if self._check_cancelled():
                return
            
            transcriptions = {}
            
            total_speakers = len(self.speaker_audio_map)
            for i, (speaker, audio_path) in enumerate(self.speaker_audio_map.items()):
                if audio_path:
                    progress = 20 + int((i / total_speakers) * 25)
                    self.progress.emit(progress, f"Whisper 변환 중: {speaker}...")
                    # Get configured language
                    config = get_config()
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
            
            # Check for cancellation before VAD
            if self._check_cancelled():
                return

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
            import traceback
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
        
        # Start with speaker names (确定的固有名詞)
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


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PictureBookBuilder")
        self.setMinimumSize(1200, 800)
        
        # Set window icon
        if getattr(sys, 'frozen', False):
            # PyInstaller mode
            base_path = Path(sys._MEIPASS)
        else:
            # Normal mode
            base_path = Path(__file__).parent.parent.parent
            
        icon_path = base_path / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        self.script_path: Optional[str] = None
        self.image_folder: Optional[str] = None
        self.speakers: list[str] = []
        self.speaker_audio_map: dict[str, str] = {}
        self.audio_files: list[Path] = []
        self.result_data: Optional[dict] = None
        self.project_path: Optional[str] = None  # Current project file path
        
        # Audio cache for fast waveform extraction during real-time edit
        self.speaker_audio_cache: dict[str, 'AudioSegment'] = {}
        self._waveform_cache: dict[str, list[float]] = {}  # Cache by (clip_id, start, end)
        
        # Runtime configuration
        self.runtime_config = get_config()
        
        # Undo system
        self.undo_stack = UndoStack()

        self._setup_menu_bar()
        self._setup_ui()

    def _make_unique_clip_id(self, base_id: str) -> str:
        """Generate a clip id that is unique within the current timeline."""
        clips = getattr(self.timeline_widget.canvas, 'clips', [])
        used_ids = {getattr(c, 'id', None) for c in clips}
        used_ids.discard(None)

        if base_id not in used_ids:
            return base_id

        suffix = 1
        while f"{base_id}_{suffix}" in used_ids:
            suffix += 1
        return f"{base_id}_{suffix}"
    
    def _setup_menu_bar(self):
        """Setup the menu bar with comprehensive options"""
        from PyQt6.QtGui import QAction, QKeySequence
        
        menu_bar = self.menuBar()
        
        # --- File Menu ---
        file_menu = menu_bar.addMenu("파일")
        
        # New project
        new_action = QAction("새 프로젝트", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)
        
        # Open project
        open_action = QAction("프로젝트 열기...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        # Save project
        save_action = QAction("저장", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)
        
        # Save as
        save_as_action = QAction("다른 이름으로 저장...", self)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)
        
        file_menu.addSeparator()
        
        # Settings (Moved to File menu)
        settings_action = QAction("설정...", self)
        settings_action.triggered.connect(self._show_settings)
        file_menu.addAction(settings_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("종료", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # --- Edit Menu ---
        edit_menu = menu_bar.addMenu("편집")

        self.undo_action = QAction("실행 취소", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self._undo)
        self.undo_action.setEnabled(False)
        edit_menu.addAction(self.undo_action)

        self.redo_action = QAction("다시 실행", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self._redo)
        self.redo_action.setEnabled(False)
        edit_menu.addAction(self.redo_action)

        # --- Project Menu ---
        project_menu = menu_bar.addMenu("프로젝트")
        
        load_script_action = QAction("스크립트 불러오기...", self)
        load_script_action.triggered.connect(self._load_script)
        project_menu.addAction(load_script_action)
        
        load_images_action = QAction("이미지 폴더 불러오기...", self)
        load_images_action.triggered.connect(self._load_image_folder)
        project_menu.addAction(load_images_action)
        
        self.reload_images_action = QAction("이미지 폴더 다시읽기", self)
        self.reload_images_action.setShortcut("F6")
        self.reload_images_action.triggered.connect(self._reload_image_folder)
        self.reload_images_action.setEnabled(False)  # 이미지 폴더가 설정되면 활성화
        project_menu.addAction(self.reload_images_action)
        
        # --- Tools Menu ---
        tools_menu = menu_bar.addMenu("도구")
        
        self.action_process = QAction("처리 시작", self)
        self.action_process.setShortcut(QKeySequence("F5"))
        self.action_process.triggered.connect(self._start_processing)
        self.action_process.setEnabled(False)
        tools_menu.addAction(self.action_process)
        
        tools_menu.addSeparator()
        
        self.action_format_subs = QAction("자막 자동 정리", self)
        self.action_format_subs.triggered.connect(self._auto_format_subtitles)
        self.action_format_subs.setEnabled(False)
        tools_menu.addAction(self.action_format_subs)
        
        self.action_apply_images = QAction("이미지 일괄 적용", self)
        self.action_apply_images.triggered.connect(self._apply_images_to_timeline)
        self.action_apply_images.setEnabled(False)
        tools_menu.addAction(self.action_apply_images)
        
        # --- Export Menu ---
        export_menu = menu_bar.addMenu("내보내기")
        
        self.action_render = QAction("영상 렌더링...", self)
        self.action_render.setShortcut(QKeySequence("F9"))
        self.action_render.triggered.connect(self._render_video)
        self.action_render.setEnabled(False)
        export_menu.addAction(self.action_render)

        export_menu.addSeparator()

        self.action_export_audio = QAction("오디오 내보내기...", self)
        self.action_export_audio.triggered.connect(self._export_audio_dialog)
        self.action_export_audio.setEnabled(False)
        export_menu.addAction(self.action_export_audio)
        
        self.action_export_srt = QAction("SRT 자막 내보내기...", self)
        self.action_export_srt.triggered.connect(self._export_srt)
        self.action_export_srt.setEnabled(False)
        export_menu.addAction(self.action_export_srt)
        
        self.action_export_xml = QAction("XML 프로젝트 내보내기...", self)
        self.action_export_xml.triggered.connect(self._export_xml)
        self.action_export_xml.setEnabled(False)
        export_menu.addAction(self.action_export_xml)
    
    def _setup_ui(self):
        """Setup the main UI layout"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # --- Main Toolbar ---
        self._create_main_toolbar()
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Main content area (splitter)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)
        
        # Left panel - Script and speaker mapping
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel - Preview and timeline
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        splitter.setSizes([400, 800])
        main_layout.addWidget(splitter, 1)
        
        # Status bar is already created by QMainWindow
        self.statusBar().showMessage("준비")
    
    def _create_main_toolbar(self):
        """Create the top main toolbar"""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        
        # Add actions
        toolbar.addAction(self.action_process)
        toolbar.addSeparator()
        toolbar.addAction(self.action_format_subs)
        toolbar.addAction(self.action_apply_images)
        
        # Spacer
        dummy = QWidget()
        dummy.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        toolbar.addWidget(dummy)
        
        # Right side actions
        toolbar.addAction(self.action_render)

    
    def _create_left_panel(self) -> QWidget:
        """Create left panel with script view and speaker mapping"""
        # Wrapper widget
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 5, 0, 0)  # Consistent margins
        
        # Vertical Splitter
        from PyQt6.QtWidgets import QSplitter
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(10)
        
        # --- 1. Script Section ---
        script_group = QGroupBox("스크립트")
        script_layout = QVBoxLayout(script_group)
        script_layout.setContentsMargins(10, 15, 10, 10) # Increased margins for consistency 
        # No local buttons anymore, functionality moved to Project menu
        self.script_text = QTextEdit()
        self.script_text.setReadOnly(True)
        self.script_text.setPlaceholderText("여기를 클릭하여 스크립트 파일을 불러오세요...\n\n지원 형식:\n* 화자: 대사\n- 화자: 대사\n화자: 대사")
        # Install event filter for click-to-load
        self.script_text.viewport().installEventFilter(self)
        
        script_layout.addWidget(self.script_text)
        splitter.addWidget(script_group)
        
        # --- 2. Speaker Mapping Section ---
        mapping_group = QGroupBox("화자별 오디오")
        mapping_layout = QVBoxLayout(mapping_group)
        mapping_layout.setContentsMargins(10, 15, 10, 10) # Increased margins for consistency
        self.mapping_table = QTableWidget()
        self.mapping_table.setColumnCount(2)
        self.mapping_table.setHorizontalHeaderLabels(["화자", "오디오 파일 (클릭하여 파일 지정)"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mapping_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.mapping_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mapping_table.cellClicked.connect(self._on_mapping_table_clicked)
        mapping_layout.addWidget(self.mapping_table)
        
        # Info label
        self.mapping_info = QLabel("스크립트를 불러오면 화자 목록이 표시됩니다.")
        self.mapping_info.setStyleSheet("color: gray; font-style: italic;")
        mapping_layout.addWidget(self.mapping_info)
        
        splitter.addWidget(mapping_group)
        
        # --- 3. Image Files Section ---
        image_group = QGroupBox("이미지 파일")
        image_layout = QVBoxLayout(image_group)
        image_layout.setContentsMargins(10, 15, 10, 10) # Increased margins for consistency
        
        # Image list with thumbnails and drag-drop reordering
        self.image_list = DraggableImageListWidget()
        self.image_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.image_list.setIconSize(QSize(64, 64))  # Larger thumbnails
        self.image_list.setGridSize(QSize(100, 100))  # Match delegate sizeHint for consistent spacing
        # Use ListMode with wrapping for proper reordering behavior (IconMode allows free positioning)
        self.image_list.setViewMode(QListWidget.ViewMode.ListMode)
        self.image_list.setFlow(QListWidget.Flow.LeftToRight)
        self.image_list.setWrapping(True)
        self.image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.image_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)  # Multi-select with Ctrl/Shift
        self.image_list.setDragDropMode(QListWidget.DragDropMode.DragDrop)  # Enable drag to external widgets
        self.image_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.image_list.setDragEnabled(True)
        self.image_list.setAcceptDrops(True)
        self.image_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        # Use custom delegate to draw text BELOW icon
        self.image_list.setItemDelegate(ImageGridDelegate(self.image_list))
        
        # Install event filter for click-to-load
        self.image_list.viewport().installEventFilter(self)
        
        image_layout.addWidget(self.image_list)
        splitter.addWidget(image_group)
        
        # Set initial sizes
        splitter.setSizes([200, 300, 300])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)
        
        layout.addWidget(splitter)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """Create right panel with preview and timeline (with splitter)"""
        # Wrapper widget to handle margins
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 5, 10, 0)  # Consistent margins with left panel
        layout.setSpacing(10)

        # Create a vertical splitter for preview and timeline
        from PyQt6.QtWidgets import QSplitter
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(10)
        
        # Preview widget
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        preview_group = QGroupBox("미리보기")
        preview_group_layout = QVBoxLayout(preview_group)
        self.preview_widget = PreviewWidget()
        
        # Connect preview position to timeline
        self.preview_widget.position_changed.connect(self._on_preview_position_changed)
        
        preview_group_layout.addWidget(self.preview_widget)
        preview_layout.addWidget(preview_group)
        
        splitter.addWidget(preview_container)
        
        # Timeline widget
        timeline_container = QWidget()
        timeline_layout = QVBoxLayout(timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        
        timeline_group = QGroupBox("타임라인")
        timeline_group_layout = QVBoxLayout(timeline_group)
        self.timeline_widget = TimelineWidget()
        
        # Connect time sync signals
        self.timeline_widget.playhead_changed.connect(self._on_timeline_playhead_changed)
        
        # Connect clip edit signals
        self.timeline_widget.canvas.clip_editing.connect(self._on_clip_editing)
        self.timeline_widget.canvas.clip_edited.connect(self._on_clip_edited)
        self.timeline_widget.canvas.clip_moved.connect(self._on_clip_moved)
        self.timeline_widget.canvas.clip_double_clicked.connect(self._on_clip_double_clicked)
        self.timeline_widget.canvas.clip_context_menu.connect(self._on_clip_context_menu)
        self.timeline_widget.canvas.history_command_generated.connect(self._on_history_command)
        self.timeline_widget.canvas.image_dropped.connect(self._on_image_dropped)
        self.timeline_widget.canvas.clip_delete_requested.connect(self._on_clip_delete_requested)
        
        timeline_group_layout.addWidget(self.timeline_widget)
        timeline_layout.addWidget(timeline_group)
        
        splitter.addWidget(timeline_container)
        
        # Set initial sizes (preview smaller, timeline larger)
        splitter.setSizes([300, 200])
        
        layout.addWidget(splitter)
        return container

    def mousePressEvent(self, event):
        """Clear focus when clicking on empty space (background)"""
        # Get currently focused widget
        focused = QApplication.focusWidget()
        
        # If it's an input widget, clear focus
        if focused and isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
            focused.clearFocus()
            
        super().mousePressEvent(event)
    
    def keyPressEvent(self, event):
        """Handle global key press events"""
        # Spacebar to toggle playback, unless a text field is focused
        if event.key() == Qt.Key.Key_Space:
            focused = QApplication.focusWidget()
            # If focus is on a text input widget, allow standard behavior (typing space)
            if isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                # If it's a read-only text widget, still allow playback toggle
                if hasattr(focused, 'isReadOnly') and not focused.isReadOnly():
                    super().keyPressEvent(event)
                    return

            # Otherwise toggle playback
            if hasattr(self, 'preview_widget'):
                self.preview_widget.toggle_playback()
                return

        super().keyPressEvent(event)

    def eventFilter(self, source, event):
        """Handle clicks on placeholders when empty"""
        if event.type() == QEvent.Type.MouseButtonRelease:
            if source is self.script_text.viewport() and not self.script_path:
                self._load_script()
                return True
            elif source is self.image_list.viewport() and not self.image_folder:
                self._load_image_folder()
                return True
        return super().eventFilter(source, event)
    
    def _create_bottom_controls(self):
        """Create controls in status bar - Removed as they are now in Toolbar/Menu"""
        pass

    def _update_undo_redo_actions(self):
        """Update enabled state of Undo/Redo actions"""
        self.undo_action.setEnabled(self.undo_stack.can_undo())
        self.undo_action.setText(f"실행 취소({self.undo_stack.undo_stack[-1].text()})" if self.undo_stack.can_undo() else "실행 취소")
        self.redo_action.setEnabled(self.undo_stack.can_redo())
        self.redo_action.setText(f"다시 실행({self.undo_stack.redo_stack[-1].text()})" if self.undo_stack.can_redo() else "다시 실행")

    def _undo(self):
        """Undo last action"""
        if self.undo_stack.can_undo():
            text = self.undo_stack.undo()
            self._update_undo_redo_actions()
            self.statusBar().showMessage(f"실행 취소됨: {text}")

    def _redo(self):
        """Redo last action"""
        if self.undo_stack.can_redo():
            text = self.undo_stack.redo()
            self._update_undo_redo_actions()
            self.statusBar().showMessage(f"다시 실행됨: {text}")

    def _on_undo_redo_callback(self):
        """Callback after undo/redo to refresh UI"""
        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        self.timeline_widget.canvas._update_total_duration()

        # Sync to preview
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self._regenerate_preview_from_clips() # Might be heavy but ensures audio sync

    def _on_history_command(self, action_type, data):
        """Handle history command generation from TimelineCanvas"""
        cmd = None
        if action_type == 'modify':
            cmd = ModifyClipsCommand(
                self.timeline_widget.canvas,
                data['modifications'],
                data['description'],
                callback=self._on_undo_redo_callback
            )

        if cmd:
            self.undo_stack.push(cmd)
            self._update_undo_redo_actions()
    
    def _load_script(self):
        """Load script file and detect speakers"""
        path, _ = QFileDialog.getOpenFileName(
            self, "스크립트 파일 선택", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            self.script_path = path
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.script_text.setText(content)
            
            # Parse script to detect speakers
            self._detect_speakers()
            self._check_ready()
    
    def _detect_speakers(self):
        """Detect speakers from script and update mapping table"""
        if not self.script_path:
            return
        
        from core.script_parser import ScriptParser
        parser = ScriptParser()
        dialogues = parser.parse_file(self.script_path)
        self.speakers = parser.get_unique_speakers(dialogues)
        
        # Update mapping table
        self.mapping_table.setRowCount(len(self.speakers))
        self.speaker_audio_map = {}
        
        for i, speaker in enumerate(self.speakers):
            # Speaker name
            speaker_item = QTableWidgetItem(speaker)
            speaker_item.setFlags(speaker_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.mapping_table.setItem(i, 0, speaker_item)
            
            # Audio file (empty initially)
            audio_item = QTableWidgetItem("(선택 안됨)")
            audio_item.setForeground(QColor(150, 150, 150))
            self.mapping_table.setItem(i, 1, audio_item)
            
            self.speaker_audio_map[speaker] = ""
        
        # Enable grid lines for better visibility
        self.mapping_table.setShowGrid(True)
        self.mapping_table.setStyleSheet("QTableWidget::item { border-bottom: 1px solid #333333; }")
        
        # Update info
        self.mapping_info.setText(f"{len(self.speakers)}명의 화자 감지됨. 각 화자에 오디오 파일을 지정하세요.")
        self.mapping_info.setStyleSheet("color: orange;")
    
    def _on_mapping_table_clicked(self, row, column):
        """Handle click on speaker mapping table"""
        speaker = self.mapping_table.item(row, 0).text()
        self._select_audio_for_speaker(speaker, row)
        
    def _select_audio_for_speaker(self, speaker: str, row: int):
        """Open file dialog to select audio for a specific speaker"""
        path, _ = QFileDialog.getOpenFileName(
            self, f"'{speaker}' 오디오 파일 선택", "",
            "Audio Files (*.wav *.mp3 *.m4a *.ogg);;All Files (*)"
        )
        if path:
            self.speaker_audio_map[speaker] = path
            
            # Update table
            audio_item = QTableWidgetItem(Path(path).name)
            audio_item.setForeground(QColor(100, 200, 100))
            self.mapping_table.setItem(row, 1, audio_item)
            
            # Check if all speakers have audio
            self._update_mapping_status()
            self._check_ready()
    
    def _update_mapping_status(self):
        """Update the mapping info label based on current state"""
        mapped = sum(1 for v in self.speaker_audio_map.values() if v)
        total = len(self.speakers)
        
        if mapped == total:
            self.mapping_info.setText(f"모든 화자({total}명)에 오디오가 지정되었습니다!")
            self.mapping_info.setStyleSheet("color: green;")
        else:
            self.mapping_info.setText(f"{mapped}/{total}명 지정됨. 모든 화자에 오디오를 지정하세요.")
            self.mapping_info.setStyleSheet("color: orange;")
    
    def _load_image_folder(self):
        """Load image folder"""
        path = QFileDialog.getExistingDirectory(self, "이미지 폴더 선택")
        if path:
            self.image_folder = path
            self._populate_image_list(path)
            
            # Enable reload action
            self.reload_images_action.setEnabled(True)
            
            # If processing is already done, enable apply button
            if self.timeline_widget.canvas.clips:
                self.action_apply_images.setEnabled(True)
    
    def _reload_image_folder(self):
        """Reload images from the current image folder"""
        if self.image_folder:
            self._populate_image_list(self.image_folder)
            self.statusBar().showMessage(f"이미지 폴더를 다시 불러왔습니다: {self.image_folder}")
    
    def _populate_image_list(self, folder_path: str):
        """Populate image list with thumbnails (loads all upfront, natural sorting)"""
        import re
        from .image_cache import get_image_cache
        
        def natural_key(text):
            return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]

        self.image_list.clear()
        image_path = Path(folder_path)
        images = []
        for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
            images.extend(image_path.glob(ext))
        
        # Sort images naturally (1, 2, 10 instead of 1, 10, 2)
        images.sort(key=lambda x: natural_key(x.name))
        
        # Get image cache
        cache = get_image_cache()
        
        # Disconnect previous signal if connected
        try:
            cache.image_loaded.disconnect(self._on_thumbnail_ready)
        except TypeError:
            pass  # Not connected
        
        # Connect signal for thumbnail updates
        cache.image_loaded.connect(self._on_thumbnail_ready)
        
        # Store path to item mapping for updates
        self._image_path_to_item: dict[str, QListWidgetItem] = {}
        
        # Add items with placeholder icons immediately
        image_paths_to_load = []
        for f in images:
            path_str = str(f)
            
            # Create item (will set icon below if cached)
            item = QListWidgetItem(f"🖼️ {f.name}")
            item.setData(Qt.ItemDataRole.UserRole, path_str)
            self.image_list.addItem(item)
            
            # Store mapping for async update
            self._image_path_to_item[path_str] = item
            
            # Check if already cached - if so, apply thumbnail immediately
            if cache.is_loaded(path_str):
                pixmap = cache.get_thumbnail_small(path_str)
                if pixmap and not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
                    item.setText(f.name)
            else:
                # Need to load this image
                image_paths_to_load.append(path_str)
        
        # Load only images not already in cache
        if image_paths_to_load:
            cache.load_images(image_paths_to_load)
    
    def _on_thumbnail_ready(self, path: str):
        """Handle image load completion - update list item with thumbnail"""
        if not hasattr(self, '_image_path_to_item'):
            return
        
        from .image_cache import get_image_cache
        cache = get_image_cache()
        
        item = self._image_path_to_item.get(path)
        if item:
            pixmap = cache.get_thumbnail_small(path)
            if pixmap and not pixmap.isNull():
                icon = QIcon(pixmap)
                item.setIcon(icon)
                item.setText(Path(path).name)
        
        # Also update timeline if this image is used there
        for clip in self.timeline_widget.canvas.clips:
            if clip.clip_type == "image" and clip.image_path == path:
                # Clear pixmap cache to force refresh
                self.timeline_widget.canvas.pixmap_cache.pop(path, None)
                self.timeline_widget.canvas._background_dirty = True
                self.timeline_widget.canvas.update()
                break
    
    def _apply_images_to_timeline(self):
        """Apply images from list to timeline, mapping 1:1 with audio clips"""
        
        # Get audio clips from timeline
        audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
        if not audio_clips:
            QMessageBox.warning(self, "오류", "타임라인에 오디오 클립이 없습니다.")
            return
        
        # Get images from list (in current order)
        image_paths = []
        for i in range(self.image_list.count()):
            item = self.image_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                image_paths.append(path)
        
        if not image_paths:
            QMessageBox.warning(self, "오류", "이미지 목록이 비어있습니다.")
            return
        
        # Sort audio clips by start time
        audio_clips_sorted = sorted(audio_clips, key=lambda c: c.start)
        
        # Check for surplus images and ask user what to do
        surplus_count = len(image_paths) - len(audio_clips_sorted)
        append_surplus = False
        
        if surplus_count > 0:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setWindowTitle("이미지 남음")
            msg.setText(f"이미지가 {surplus_count}개 남습니다.")
            msg.setInformativeText(f"총 이미지: {len(image_paths)}개\n오디오 클립: {len(audio_clips_sorted)}개")
            
            ignore_btn = msg.addButton("무시하기", QMessageBox.ButtonRole.RejectRole)
            append_btn = msg.addButton("뒤에 추가", QMessageBox.ButtonRole.AcceptRole)
            msg.setDefaultButton(ignore_btn)
            
            msg.exec()
            
            if msg.clickedButton() == append_btn:
                append_surplus = True
        
        # Check if there are existing image clips
        existing_image_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "image"]
        if existing_image_clips:
            reply = QMessageBox.warning(
                self, "경고",
                f"기존 이미지 클립 {len(existing_image_clips)}개가 모두 삭제됩니다.\n계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        new_image_clips = []
        
        # Map images to audio clips 1:1
        for i, audio_clip in enumerate(audio_clips_sorted):
            if i < len(image_paths):
                img_path = image_paths[i]
            else:
                # No more images - leave blank (no image clip)
                continue
            
            # Calculate image duration
            img_start = audio_clip.start
            if i + 1 < len(audio_clips_sorted):
                img_end = audio_clips_sorted[i + 1].start
            else:
                img_end = audio_clip.start + audio_clip.duration
            
            img_clip = TimelineClip(
                id=self._make_unique_clip_id(f"img_{i}"),
                name=Path(img_path).name,
                start=img_start,
                duration=img_end - img_start,
                track=2,  # Image track
                color=QColor("#9E9E9E"),
                clip_type="image",
                waveform=[],
                image_path=img_path
            )
            new_image_clips.append(img_clip)
        
        # Append surplus images if user requested
        if append_surplus and surplus_count > 0:
            # Calculate where to start appending (after last audio clip)
            if audio_clips_sorted:
                last_audio = audio_clips_sorted[-1]
                append_start = last_audio.start + last_audio.duration
            else:
                append_start = 0.0
            
            # Add surplus images with 5 second duration each
            for i in range(len(audio_clips_sorted), len(image_paths)):
                img_path = image_paths[i]
                img_duration = 5.0  # 5 seconds default
                
                img_clip = TimelineClip(
                    id=self._make_unique_clip_id(f"img_{i}"),
                    name=Path(img_path).name,
                    start=append_start,
                    duration=img_duration,
                    track=2,
                    color=QColor("#9E9E9E"),
                    clip_type="image",
                    waveform=[],
                    image_path=img_path
                )
                new_image_clips.append(img_clip)
                append_start += img_duration

        # Create undo command
        cmd = AddRemoveClipsCommand(
            self.timeline_widget.canvas,
            added=new_image_clips,
            removed=existing_image_clips,
            description="Apply images to timeline",
            callback=self._on_undo_redo_callback
        )
        self.undo_stack.push(cmd)
        cmd.redo()
        self._update_undo_redo_actions()

        # Update timeline
        self.timeline_widget.canvas._update_total_duration()
        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        
        # Sync to preview
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips)
        
        # Show result
        applied = len(new_image_clips)
        if append_surplus and surplus_count > 0:
            self.statusBar().showMessage(f"이미지 {applied}개가 적용되었습니다. ({surplus_count}개 뒤에 추가됨)")
        else:
            self.statusBar().showMessage(f"이미지 {applied}개가 적용되었습니다.")
    
    def _check_ready(self):
        """Check if we have all inputs to start processing"""
        # Need script and all speakers mapped
        all_mapped = all(self.speaker_audio_map.get(s) for s in self.speakers)
        ready = bool(self.script_path and self.speakers and all_mapped)
        self.action_process.setEnabled(ready)
    
    def _start_processing(self):
        """Start the processing thread with progress dialog"""
        self.action_process.setEnabled(False)
        
        # Create and show progress dialog
        self.progress_dialog = ProgressDialog(self, "오디오 처리 중...")
        
        self.processing_thread = ProcessingThread(
            self.script_path,
            self.speaker_audio_map.copy(),
            self.image_folder or ""
        )
        
        # Connect signals
        self.processing_thread.progress.connect(self._on_progress)
        self.processing_thread.finished.connect(self._on_processing_finished)
        self.progress_dialog.cancelled.connect(self.processing_thread.cancel)
        
        self.processing_thread.start()
        self.progress_dialog.show()
    
    def _on_progress(self, percent: int, message: str):
        """Handle progress updates"""
        self.statusBar().showMessage(f"{message} ({percent}%)")
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.update_progress(percent, message)
    
    def _on_processing_finished(self, success: bool, message: str, result: Optional[dict]):
        """Handle processing completion"""
        # Close progress dialog
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog._is_cancelled = True  # Allow closing
            self.progress_dialog.close()
            self.progress_dialog = None
        
        self.action_process.setEnabled(True)
        self.result_data = result
        
        if success:
            self.statusBar().showMessage("처리 완료")
            self.action_export_srt.setEnabled(True)
            self.action_export_xml.setEnabled(True)
            self.action_render.setEnabled(True)
            
            # Update timeline with aligned clips
            if result and 'aligned' in result:
                self._update_timeline(result)
                # Generate preview audio
                self._generate_preview_audio(result)
                
                # Enable image apply button if we have images
                if self.image_list.count() > 0:
                    self.action_apply_images.setEnabled(True)
            
            QMessageBox.information(self, "완료", message)
        else:
            self.statusBar().showMessage("취소됨" if "취소" in message else "오류 발생")
            if "취소" not in message:
                QMessageBox.critical(self, "오류", message)
    
    def _update_timeline(self, result: dict):
        """Update the timeline with aligned segments and waveforms"""
        from .timeline_widget import TimelineClip
        from pydub import AudioSegment
        import numpy as np
        
        aligned = result.get('aligned', [])
        speaker_audio_map = result.get('speaker_audio_map', {})
        clips = []
        current_time = 0.0
        current_time = 0.0
        gap = DEFAULT_GAP_SECONDS
        
        # Load speaker audio files for waveform extraction
        speaker_audio: dict[str, AudioSegment] = {}
        for speaker, audio_path in speaker_audio_map.items():
            if audio_path:
                try:
                    speaker_audio[speaker] = AudioSegment.from_file(audio_path)
                except:
                    pass
        
        for i, segment in enumerate(aligned):
            duration = segment.end_time - segment.start_time
            
            # Extract waveform data
            waveform = []
            speaker = segment.dialogue.speaker
            actual_duration = duration
            
            if speaker in speaker_audio:
                audio = speaker_audio[speaker]
                
                start_ms = max(0, int(segment.start_time * 1000))
                end_ms = min(len(audio), int(segment.end_time * 1000))
                clip_audio = audio[start_ms:end_ms]
                
                # Use actual extracted clip duration for timeline sync
                actual_duration = len(clip_audio) / 1000.0
                
                waveform = self._extract_waveform_from_audio(clip_audio)
            
            # 1. Audio clip (Track 0)

            audio_clip = TimelineClip(
                id=f"audio_{i}",
                name="", # Remove text from audio clip for cleaner look
                start=current_time,
                duration=actual_duration,
                track=0,
                color=self.timeline_widget.canvas.get_color_for_speaker(segment.dialogue.speaker),
                clip_type="audio",
                waveform=waveform,
                offset=segment.start_time,  # Store segment start time
                segment_index=i,
                speaker=segment.dialogue.speaker
            )
            clips.append(audio_clip)
            
            # 2. Subtitle clip (Track 1)
            sub_clip = TimelineClip(
                id=f"sub_{i}",
                name=segment.dialogue.text, # Full text here
                start=current_time,
                duration=actual_duration,
                track=1,
                color=QColor(self.timeline_widget.canvas.get_color_for_speaker(segment.dialogue.speaker)).lighter(150),
                clip_type="subtitle", # New type
                waveform=[],
                offset=segment.start_time,  # Store VAD-adjusted start time
                segment_index=i,
                speaker=segment.dialogue.speaker,
                words=segment.words or []  # Pass word timestamps for editing
            )
            clips.append(sub_clip)
            
            current_time += actual_duration + gap
        
        # Add image clips to track 1 (synced with audio clips)
        if self.image_folder:
            from pathlib import Path as P
            image_folder = P(self.image_folder)
            images = []
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                images.extend(sorted(image_folder.glob(ext)))
            
            if images:
                # Get audio clip start times for image sync
                audio_clip_starts = [c.start for c in clips]
                audio_clip_ends = [c.start + c.duration for c in clips]
                
                # Number of images to use (match with audio clips or fewer)
                num_images_to_use = min(len(images), len(clips))
                
                for i in range(num_images_to_use):
                    img_path = images[i]
                    
                    # Image starts when audio clip starts
                    img_start = audio_clip_starts[i]
                    
                    # Image ends when next audio clip starts (or at the end)
                    if i + 1 < len(audio_clip_starts):
                        img_end = audio_clip_starts[i + 1]
                    else:
                        img_end = audio_clip_ends[i]
                    
                    img_duration = img_end - img_start
                    
                    img_clip = TimelineClip(
                        id=f"img_{i}",
                        name=img_path.name,
                        start=img_start,
                        duration=img_duration,
                        track=2,  # Image track moved to 2
                        color=QColor("#9E9E9E"),  # Gray color for images
                        clip_type="image",
                        waveform=[],
                        image_path=str(img_path)
                    )
                    clips.append(img_clip)
        
        self.timeline_widget.set_clips(clips)
        
        # Sync to preview widget
        self.preview_widget.set_timeline_clips(clips)
        
        # Enable subtitle formatting button
        self.action_format_subs.setEnabled(True)
    
    def _extract_waveform_from_audio(self, audio_segment) -> list[float]:
        """Extract normalized waveform data from an audio segment"""
        import numpy as np
        
        # Convert to numpy array
        samples = np.array(audio_segment.get_array_of_samples())
        waveform = []
        
        if len(samples) > 0:
            # Downsample for display - use duration-based sampling for consistent resolution
            # 100 samples per second ensures all clips have similar visual density
            duration_seconds = len(audio_segment) / 1000.0  # pydub uses milliseconds
            samples_per_second = 100
            target_samples = max(50, int(duration_seconds * samples_per_second))
            if len(samples) > target_samples:
                chunk_size = len(samples) // target_samples
                downsampled = []
                for j in range(0, len(samples), chunk_size):
                    chunk = samples[j:j+chunk_size]
                    if len(chunk) > 0:
                        # Take max absolute value for each chunk
                        downsampled.append(float(np.max(np.abs(chunk))))
                waveform = downsampled
            else:
                waveform = [float(abs(s)) for s in samples]
            
            # Normalize to 0-1 range
            if waveform:
                max_val = max(waveform) if max(waveform) > 0 else 1
                waveform = [v / max_val for v in waveform]
        
        return waveform
    
    def _generate_preview_audio(self, result: dict):
        """Setup AudioMixer with clips for preview playback"""
        try:
            from .audio_mixer import ScheduledClip
            
            aligned = result.get('aligned', [])
            speaker_audio_map = result.get('speaker_audio_map', {})
            
            if not aligned:
                return
            
            self.statusBar().showMessage("미리보기 오디오 준비 중...")
            
            # Build scheduled clips from timeline clips
            scheduled_clips = []
            audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
            
            for clip in audio_clips:
                scheduled_clips.append(ScheduledClip(
                    clip_id=clip.id,
                    speaker=clip.speaker,
                    timeline_start=clip.start,
                    timeline_end=clip.start + clip.duration,
                    source_offset=clip.offset,
                    source_path=speaker_audio_map.get(clip.speaker, ""),
                    duration=clip.duration
                ))
            
            # Set up the AudioMixer
            self.preview_widget.set_audio_clips(scheduled_clips, speaker_audio_map)
            
            # Calculate total timeline duration from ALL clips
            all_clips = self.timeline_widget.canvas.clips
            total_duration = max((c.start + c.duration for c in all_clips), default=0.0)
            self.preview_widget.set_total_duration(total_duration)
            
            # Connect preview position to timeline playhead (only once)
            if not hasattr(self, '_preview_connected') or not self._preview_connected:
                self.preview_widget.audio_mixer.position_changed.connect(self._on_preview_position_changed)
                self._preview_connected = True
            
            # Sync timeline clips to preview for image/subtitle display
            self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips)
            
            self.statusBar().showMessage("미리보기 준비 완료")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"미리보기 오디오 준비 실패: {str(e)}")
    
    def _on_timeline_playhead_changed(self, time: float):
        """Handle playhead change from timeline - sync to preview"""
        if hasattr(self, 'preview_widget') and self.preview_widget.audio_path:
            # Convert time to milliseconds and seek preview
            position_ms = int(time * 1000)
            self.preview_widget.audio_mixer.seek(time)
    
    def _on_preview_position_changed(self, position_ms: int):
        """Handle position change from preview - sync to timeline"""
        if hasattr(self, 'timeline_widget'):
            time_sec = position_ms / 1000.0
            # Only auto-scroll when playing
            is_playing = self.preview_widget.audio_mixer.is_playing
            self.timeline_widget.set_playhead(time_sec, auto_scroll=is_playing)
    
    
    def _on_clip_editing(self, clip_id: str):
        """Handle real-time clip boundary change - update waveform and AudioMixer"""
        # Find the editing clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        if not clip: return

        # Sync preview in real-time (preserve current playhead position)
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)

        # Only audio clips should generate/display waveforms.
        if getattr(clip, 'clip_type', None) != "audio":
            return
        
        # Update AudioMixer with the modified clip in real-time
        from .audio_mixer import ScheduledClip
        speaker_audio_map = self.result_data.get('speaker_audio_map', {}) if self.result_data else self.speaker_audio_map
        
        scheduled_clip = ScheduledClip(
            clip_id=clip.id,
            speaker=clip.speaker,
            timeline_start=clip.start,
            timeline_end=clip.start + clip.duration,
            source_offset=clip.offset,
            source_path=speaker_audio_map.get(clip.speaker, ""),
            duration=clip.duration
        )
        self.preview_widget.update_audio_clip(scheduled_clip)

        # Update waveform only (fast path) using offset + duration
        try:
            # Check cache for speaker audio
            if clip.speaker not in self.speaker_audio_cache:
                audio_path = speaker_audio_map.get(clip.speaker)
                if audio_path:
                    from pydub import AudioSegment
                    self.speaker_audio_cache[clip.speaker] = AudioSegment.from_file(audio_path)
            
            audio = self.speaker_audio_cache.get(clip.speaker)
            if audio:
                # Extract audio using offset and duration
                segment_end = clip.offset + clip.duration
                
                start_ms = max(0, int(clip.offset * 1000))
                end_ms = min(len(audio), int(segment_end * 1000))
                
                # Simple cache for waveform itself during drag to prevent flickering
                cache_key = f"{clip_id}_{start_ms}_{end_ms}"
                if cache_key in self._waveform_cache:
                    clip.waveform = self._waveform_cache[cache_key]
                else:
                    clip_audio = audio[start_ms:end_ms]
                    clip.waveform = self._extract_waveform_from_audio(clip_audio)
                    self._waveform_cache[cache_key] = clip.waveform
                
                self.timeline_widget.canvas.update()
        except Exception as e:
            pass

    def _on_clip_edited(self, clip_id: str):
        """Handle final clip boundary edit - sync to result_data and update AudioMixer"""
        # Find the edited clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        if not clip:
            return
        
        # Audio-specific: Update aligned segment data using offset + duration
        if clip.clip_type == "audio" and clip.segment_index >= 0 and self.result_data:
            aligned = self.result_data.get('aligned', [])
            if clip.segment_index < len(aligned):
                segment = aligned[clip.segment_index]
                # Update segment times based on clip's offset and duration
                segment.start_time = clip.offset
                segment.end_time = clip.offset + clip.duration
                self.statusBar().showMessage(f"오디오 클립 수정됨: {clip.offset:.2f}s ~ {clip.source_end:.2f}s")
        else:
            self.statusBar().showMessage(f"클립 수정됨: {clip.name}")
        
        # Sync to preview widget for ALL types
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        
        # Update AudioMixer for audio clips (already updated in _on_clip_editing, but finalize here)
        if clip.clip_type == "audio":
            from .audio_mixer import ScheduledClip
            speaker_audio_map = self.result_data.get('speaker_audio_map', {}) if self.result_data else self.speaker_audio_map
            
            scheduled_clip = ScheduledClip(
                clip_id=clip.id,
                speaker=clip.speaker,
                timeline_start=clip.start,
                timeline_end=clip.start + clip.duration,
                source_offset=clip.offset,
                source_path=speaker_audio_map.get(clip.speaker, ""),
                duration=clip.duration
            )
            self.preview_widget.update_audio_clip(scheduled_clip)
            
            self.preview_widget.update_audio_clip(scheduled_clip)
            
        # Update total duration for ALL clip types
        all_clips = self.timeline_widget.canvas.clips
        total_duration = max((c.start + c.duration for c in all_clips), default=0.0)
        self.preview_widget.set_total_duration(total_duration)
    
    def _on_clip_moved(self, clip_id: str, new_start: float):
        """Handle clip position change"""
        self.statusBar().showMessage(f"클립 이동됨: {new_start:.2f}s")
        
        # Find the moved clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        # Sync to preview widget
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        
        # Update AudioMixer for audio clips
        if clip and clip.clip_type == "audio":
            from .audio_mixer import ScheduledClip
            speaker_audio_map = self.result_data.get('speaker_audio_map', {}) if self.result_data else self.speaker_audio_map
            
            scheduled_clip = ScheduledClip(
                clip_id=clip.id,
                speaker=clip.speaker,
                timeline_start=clip.start,
                timeline_end=clip.start + clip.duration,
                source_offset=clip.offset,
                source_path=speaker_audio_map.get(clip.speaker, ""),
                duration=clip.duration
            )
            self.preview_widget.update_audio_clip(scheduled_clip)
            
            self.preview_widget.update_audio_clip(scheduled_clip)
            
        # Update total duration for ALL clip types
        all_clips = self.timeline_widget.canvas.clips
        total_duration = max((c.start + c.duration for c in all_clips), default=0.0)
        self.preview_widget.set_total_duration(total_duration)
    
    def _on_clip_double_clicked(self, clip_id: str):
        """Handle clip double click - edit subtitle text"""
        # Find the clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        if not clip or clip.clip_type != "subtitle":
            return
            
        # Open dialog to edit text
        from PyQt6.QtWidgets import QInputDialog
        new_text, ok = QInputDialog.getMultiLineText(
            self, "자막 수정", "대사 내용을 수정하세요:", clip.name
        )
        
        if ok and new_text:
            old_state = copy.deepcopy(clip)

            clip.name = new_text
            # Also update the actual data
            if self.result_data and clip.segment_index >= 0:
                aligned = self.result_data.get('aligned', [])
                if clip.segment_index < len(aligned):
                    aligned[clip.segment_index].dialogue.text = new_text
            
            new_state = copy.deepcopy(clip)

            # Undo Command
            cmd = ModifyClipsCommand(
                self.timeline_widget.canvas,
                [(clip.id, old_state, new_state)],
                description="Edit subtitle text",
                callback=self._on_undo_redo_callback
            )
            self.undo_stack.push(cmd)
            self._update_undo_redo_actions()

            self.timeline_widget.canvas._background_dirty = True
            self.timeline_widget.canvas.update()
            self.statusBar().showMessage("자막이 수정되었습니다.")
            
            # Sync to preview immediately
            playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
            self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
    
    def _on_clip_context_menu(self, clip_id: str, pos):
        """Show context menu for clip operations"""
        from PyQt6.QtWidgets import QMenu
        
        # Find the clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        if not clip:
            return
        
        menu = QMenu(self)
        
        if clip.clip_type == "subtitle":
            edit_action = menu.addAction("텍스트 수정")
            split_action = menu.addAction("자막 나누기...")
            menu.addSeparator()
            
            # Find if there's a next subtitle clip
            next_clip = self._find_adjacent_subtitle(clip, direction=1)
            merge_action = None
            if next_clip:
                merge_action = menu.addAction("다음 자막과 병합")
            
            action = menu.exec(pos)
            
            if action == edit_action:
                self._on_clip_double_clicked(clip_id)
            elif action == split_action:
                self._show_subtitle_editor(clip)
            elif merge_action and action == merge_action:
                self._merge_subtitle_clips(clip, next_clip)
        
        elif clip.clip_type == "image":
            change_image_action = menu.addAction("이미지 변경...")
            realign_action = menu.addAction("여기서 다시 정렬")
            menu.addSeparator()
            delete_action = menu.addAction("삭제")
            
            action = menu.exec(pos)
            
            if action == change_image_action:
                self._change_clip_image(clip)
            elif action == realign_action:
                self._realign_images_from(clip)
            elif action == delete_action:
                self._delete_clip(clip)
        
        elif clip.clip_type == "audio":
            insert_image_action = menu.addAction("이 위치에 이미지 삽입...")
            menu.addSeparator()
            
            action = menu.exec(pos)
            
            if action == insert_image_action:
                self._insert_image_at_clip(clip)
    
    def _find_adjacent_subtitle(self, clip, direction=1):
        """Find adjacent subtitle clip (direction: 1=next, -1=prev)"""
        sub_clips = [c for c in self.timeline_widget.canvas.clips 
                     if c.clip_type == "subtitle"]
        sub_clips.sort(key=lambda c: c.start)
        
        try:
            idx = next(i for i, c in enumerate(sub_clips) if c.id == clip.id)
            target_idx = idx + direction
            if 0 <= target_idx < len(sub_clips):
                return sub_clips[target_idx]
        except StopIteration:
            pass
        return None
    
    def _show_subtitle_editor(self, clip):
        """Show dialog for splitting subtitle at a specific point"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout
        
        dialog = QDialog(self)
        dialog.setWindowTitle("자막 나누기")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("나눌 위치의 앞 텍스트를 남기세요:"))
        
        text_edit = QTextEdit()
        text_edit.setPlainText(clip.name)
        layout.addWidget(text_edit)
        
        layout.addWidget(QLabel("커서 위치에서 자막이 나눠집니다."))
        
        btn_layout = QHBoxLayout()
        split_btn = QPushButton("나누기")
        cancel_btn = QPushButton("취소")
        btn_layout.addWidget(split_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        def do_split():
            cursor_pos = text_edit.textCursor().position()
            self._split_subtitle_at(clip, cursor_pos)
            dialog.accept()
        
        split_btn.clicked.connect(do_split)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog.exec()
    
    def _split_subtitle_at(self, clip, char_pos: int):
        """Split subtitle clip at character position (NEW API)"""
        from core.subtitle_processor import SubtitleProcessor
        
        if char_pos <= 0 or char_pos >= len(clip.name):
            self.statusBar().showMessage("나눌 위치가 올바르지 않습니다.")
            return
        
        # Audio-anchored split only
        if not clip.words:
            self.statusBar().showMessage("단어 타임스탬프가 없어 오디오 기준 분할을 할 수 없습니다.")
            return

        audio_anchor = self._find_linked_audio_clip_for_subtitle(clip)
        if not audio_anchor:
            self.statusBar().showMessage("연결된 오디오 클립을 찾지 못해 분할할 수 없습니다.")
            return

        processor = SubtitleProcessor()
        
        # NEW API: 통합 Fuzzy Matching으로 타임스탬프 계산
        split_indices = [char_pos]
        timestamps = processor.calculate_split_times(clip.name, split_indices, clip.words)
        
        if not timestamps:
            self.statusBar().showMessage("분할 타임스탬프 계산에 실패했습니다.")
            return
        
        source_split_time = timestamps[0]
        split_time = audio_anchor.start + (source_split_time - audio_anchor.offset)

        # Final guard: do not allow splits outside the current clip span
        clip_start = clip.start
        clip_end = clip.start + clip.duration
        if split_time <= clip_start + 0.05 or split_time >= clip_end - 0.05:
            self.statusBar().showMessage(
                "분할 위치가 현재 자막 클립 범위 밖입니다. 자막 클립을 먼저 이동/확장한 뒤 다시 시도하세요."
            )
            return
        
        # Use cursor position directly for text split (user intent)
        actual_split_pos = char_pos
        
        # Skip trailing spaces
        while actual_split_pos < len(clip.name) and clip.name[actual_split_pos] == ' ':
            actual_split_pos += 1
        
        # Create two text segments
        text1 = clip.name[:actual_split_pos].strip()
        text2 = clip.name[actual_split_pos:].strip()
        
        # Split words based on fuzzy-matched timestamp
        words1 = []
        words2 = []
        for w in clip.words:
            w_end = w.end if hasattr(w, 'end') else 0.0
            if w_end <= source_split_time:
                words1.append(w)
            else:
                words2.append(w)
        
        # Save original end time before modifying
        original_end = clip.start + clip.duration
        
        old_clip_state = copy.deepcopy(clip)

        # Update original clip (first segment ends at split_time)
        clip.name = text1
        clip.duration = split_time - clip.start
        clip.words = words1
        
        new_clip_state = copy.deepcopy(clip)

        # Create new clip (second segment starts at split_time)
        new_id = self._make_unique_clip_id(f"{clip.id}_split")
        new_duration = original_end - split_time
        
        new_clip = TimelineClip(
            id=new_id,
            name=text2,
            start=split_time,
            duration=new_duration,
            track=clip.track,
            color=clip.color,
            clip_type="subtitle",
            waveform=[],
            offset=source_split_time,
            segment_index=clip.segment_index,
            speaker=clip.speaker,
            words=words2
        )
        
        # Create Undo Commands
        modify_cmd = ModifyClipsCommand(
            self.timeline_widget.canvas,
            [(clip.id, old_clip_state, new_clip_state)],
            description="Split subtitle (modify original)"
        )

        add_cmd = AddRemoveClipsCommand(
            self.timeline_widget.canvas,
            added=[new_clip],
            removed=[],
            description="Split subtitle (add new)"
        )

        # Execute actions manually first since we already modified 'clip' in place above,
        # but we haven't added 'new_clip' yet.
        self.timeline_widget.canvas.clips.append(new_clip)

        # Push composite command
        macro_cmd = MacroCommand([modify_cmd, add_cmd], description="Split subtitle")
        self.undo_stack.push(macro_cmd)
        self._update_undo_redo_actions()

        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage("자막이 나눠졌습니다.")

    def _find_linked_audio_clip_for_subtitle(self, subtitle_clip):
        """Find the most likely linked audio clip for a subtitle clip.

        Uses segment_index as primary key; falls back to ID conventions.
        """
        clips = getattr(self.timeline_widget.canvas, 'clips', [])
        audio_clips = [c for c in clips if getattr(c, 'clip_type', None) == 'audio']
        if not audio_clips:
            return None

        # Strong match: same segment_index
        if getattr(subtitle_clip, 'segment_index', -1) is not None and subtitle_clip.segment_index >= 0:
            candidates = [
                c for c in audio_clips
                if getattr(c, 'segment_index', -1) == subtitle_clip.segment_index
                and (not getattr(subtitle_clip, 'speaker', '') or getattr(c, 'speaker', '') == subtitle_clip.speaker)
            ]
            if candidates:
                return min(candidates, key=lambda c: abs(c.start - subtitle_clip.start))

        # Fallback: sub_{i} <-> audio_{i}
        try:
            if isinstance(subtitle_clip.id, str) and subtitle_clip.id.startswith('sub_'):
                base = subtitle_clip.id.split('_', 2)[1]
                expected_audio_id = f"audio_{base}"
                for c in audio_clips:
                    if c.id == expected_audio_id:
                        return c
        except Exception:
            pass

        return None

    def _is_audio_anchor_usable(self, subtitle_clip, audio_clip) -> bool:
        """Heuristic: determine whether audio anchor is reliable enough.

        We intentionally allow subtitle clips to be source-trimmed (offset changes)
        and/or moved on the timeline; as long as we have a plausible linked audio
        clip, we can anchor split boundaries to audio.

        We only fall back when the subtitle and audio are *too* far apart on the
        timeline (likely different content / bad link).
        """
        if not subtitle_clip or not audio_clip:
            return False

        sub_start = getattr(subtitle_clip, 'start', 0.0)
        sub_end = sub_start + getattr(subtitle_clip, 'duration', 0.0)
        aud_start = getattr(audio_clip, 'start', 0.0)
        aud_end = aud_start + getattr(audio_clip, 'duration', 0.0)

        # Too far apart (timeline desync)
        if max(abs(sub_start - aud_start), abs(sub_end - aud_end)) > 2.0:
            return False

        return True
    
    def _merge_subtitle_clips(self, clip1, clip2):
        """Merge two adjacent subtitle clips"""
        from core.subtitle_processor import SubtitleProcessor

        if not clip1 or not clip2:
            return

        # Only allow merges that preserve a single audio anchor.
        # Merging across different segments or with timeline/source gaps produces
        # a subtitle clip that cannot be reliably split/anchored to audio later.
        if getattr(clip1, 'track', None) != getattr(clip2, 'track', None):
            self.statusBar().showMessage("같은 트랙의 자막만 병합할 수 있습니다.")
            return

        clip1_end = getattr(clip1, 'start', 0.0) + getattr(clip1, 'duration', 0.0)
        clip2_start = getattr(clip2, 'start', 0.0)
        if abs(clip1_end - clip2_start) > 0.1:
            self.statusBar().showMessage("서로 인접한 자막만 병합할 수 있습니다.")
            return

        if getattr(clip1, 'segment_index', -1) != getattr(clip2, 'segment_index', -1):
            self.statusBar().showMessage("서로 다른 오디오 세그먼트의 자막은 병합할 수 없습니다.")
            return

        if getattr(clip1, 'speaker', '') != getattr(clip2, 'speaker', ''):
            self.statusBar().showMessage("서로 다른 화자의 자막은 병합할 수 없습니다.")
            return

        # Source continuity guard (offset-based). Allow tiny jitter.
        clip1_source_end = getattr(clip1, 'offset', 0.0) + getattr(clip1, 'duration', 0.0)
        clip2_source_start = getattr(clip2, 'offset', 0.0)
        if abs(clip1_source_end - clip2_source_start) > 0.2:
            self.statusBar().showMessage("원본 오디오 범위가 연속적이지 않아 병합할 수 없습니다.")
            return

        a1 = self._find_linked_audio_clip_for_subtitle(clip1)
        a2 = self._find_linked_audio_clip_for_subtitle(clip2)
        if not a1 or not a2 or getattr(a1, 'id', None) != getattr(a2, 'id', None):
            self.statusBar().showMessage("연결된 오디오가 달라 병합할 수 없습니다.")
            return
        
        processor = SubtitleProcessor()
        # Use source audio coordinates for merge_segments
        # (though it doesn't do coordinate transformation, using offset maintains consistency)
        merged = processor.merge_segments(
            {'text': clip1.name, 'start_time': clip1.offset, 
             'end_time': clip1.offset + clip1.duration, 'words': clip1.words},
            {'text': clip2.name, 'start_time': clip2.offset,
             'end_time': clip2.offset + clip2.duration, 'words': clip2.words}
        )
        
        old_clip1_state = copy.deepcopy(clip1)

        # Update first clip - keep timeline start, update duration, preserve offset
        clip1.name = merged['text']
        # Calculate new timeline duration: from clip1.start to clip2's end
        new_timeline_end = clip2.start + clip2.duration
        clip1.duration = new_timeline_end - clip1.start
        clip1.words = merged['words']
        # clip1.offset stays the same (it's the original start time)
        
        new_clip1_state = copy.deepcopy(clip1)

        # Remove second clip
        self.timeline_widget.canvas.clips.remove(clip2)

        # Undo Commands
        modify_cmd = ModifyClipsCommand(
            self.timeline_widget.canvas,
            [(clip1.id, old_clip1_state, new_clip1_state)],
            description="Merge subtitles (modify first)"
        )

        remove_cmd = AddRemoveClipsCommand(
            self.timeline_widget.canvas,
            added=[],
            removed=[clip2],
            description="Merge subtitles (remove second)"
        )

        macro_cmd = MacroCommand([modify_cmd, remove_cmd], description="Merge subtitles")
        self.undo_stack.push(macro_cmd)
        self._update_undo_redo_actions()

        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage("자막이 병합되었습니다.")
    
    def _auto_format_subtitles(self):
        """Apply automatic formatting to all subtitle clips (NEW API)"""
        from core.subtitle_processor import SubtitleProcessor
        
        # Use runtime config for subtitle settings
        config = self.runtime_config
        
        # Collect subtitle clips
        subtitle_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "subtitle"]
        
        if not subtitle_clips:
            self.statusBar().showMessage("자막 클립이 없습니다.")
            return
        
        # Create processors for each language (cached)
        processor_cache = {}
        
        def get_processor(language: str) -> SubtitleProcessor:
            if language not in processor_cache:
                if config.subtitle_auto_params:
                    params = config.get_subtitle_params(language)
                else:
                    params = {
                        'line_soft_cap': config.subtitle_line_soft_cap,
                        'line_hard_cap': config.subtitle_line_hard_cap,
                        'max_lines': config.subtitle_max_lines,
                    }
                processor_cache[language] = SubtitleProcessor(
                    line_soft_cap=params['line_soft_cap'],
                    line_hard_cap=params['line_hard_cap'],
                    max_lines=params['max_lines'],
                    split_on_conjunctions=config.subtitle_split_on_conjunctions
                )
            return processor_cache[language]
        
        new_clips = []
        existing_non_subtitle_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type != "subtitle"]
        used_ids = {c.id for c in existing_non_subtitle_clips}

        def reserve_id(base_id: str) -> str:
            """Reserve and return a unique clip id for this rebuild pass."""
            candidate = base_id
            suffix = 1
            while candidate in used_ids:
                candidate = f"{base_id}_{suffix}"
                suffix += 1
            used_ids.add(candidate)
            return candidate
        
        split_count = 0
        format_count = 0
        
        for clip in subtitle_clips:
            original_text = clip.name
            audio_anchor = self._find_linked_audio_clip_for_subtitle(clip)

            base_id = (
                f"subseg_{clip.segment_index}" if getattr(clip, 'segment_index', -1) is not None and clip.segment_index >= 0
                else "subseg"
            )
            
            # Detect language and get appropriate processor
            if '_detector' not in processor_cache:
                processor_cache['_detector'] = SubtitleProcessor()
            language = processor_cache['_detector'].detect_language(original_text)
            processor = get_processor(language)
            
            # NEW API: 1. 텍스트만으로 분할 포인트 찾기
            segment_splits = processor.find_split_points(original_text, is_segment=True)
            
            if not segment_splits or not clip.words or not audio_anchor:
                # 분할 불필요 또는 불가능 - 줄바꿈만 적용
                line_breaks = processor.find_split_points(original_text, is_segment=False)
                
                if line_breaks:
                    # 줄바꿈 적용
                    lines = []
                    prev = 0
                    for lb in line_breaks:
                        lines.append(original_text[prev:lb].strip())
                        prev = lb + 1
                    lines.append(original_text[prev:].strip())
                    formatted_text = '\n'.join(lines)
                else:
                    formatted_text = original_text
                
                new_clips.append(TimelineClip(
                    id=reserve_id(f"{base_id}_0"),
                    name=formatted_text,
                    start=clip.start,
                    duration=clip.duration,
                    track=clip.track,
                    color=clip.color,
                    clip_type="subtitle",
                    waveform=[],
                    offset=clip.offset,
                    segment_index=clip.segment_index,
                    speaker=clip.speaker,
                    words=clip.words,
                ))
                
                if formatted_text != original_text:
                    format_count += 1
                continue

            # NEW API: 2. Fuzzy Matching으로 분할 타임스탬프 계산
            split_timestamps = processor.calculate_split_times(original_text, segment_splits, clip.words)
            
            if not split_timestamps or len(split_timestamps) != len(segment_splits):
                # 타임스탬프 계산 실패 - 줄바꿈만 적용
                line_breaks = processor.find_split_points(original_text, is_segment=False)
                formatted_text = original_text
                if line_breaks:
                    lines = []
                    prev = 0
                    for lb in line_breaks:
                        lines.append(original_text[prev:lb].strip())
                        prev = lb + (1 if original_text[lb] == ' ' else 0)
                    lines.append(original_text[prev:].strip())
                    formatted_text = '\n'.join(lines)
                
                new_clips.append(TimelineClip(
                    id=reserve_id(f"{base_id}_0"),
                    name=formatted_text,
                    start=clip.start,
                    duration=clip.duration,
                    track=clip.track,
                    color=clip.color,
                    clip_type="subtitle",
                    waveform=[],
                    offset=clip.offset,
                    segment_index=clip.segment_index,
                    speaker=clip.speaker,
                    words=clip.words,
                ))
                if formatted_text != original_text:
                    format_count += 1
                continue

            # 3. 타임라인 좌표로 변환 (오디오 앵커 기준)
            boundaries_timeline = []
            for source_time in split_timestamps:
                timeline_time = audio_anchor.start + (source_time - audio_anchor.offset)
                boundaries_timeline.append(timeline_time)
            
            # Validate boundaries are within the current subtitle clip range
            clip_start = clip.start
            clip_end = clip.start + clip.duration
            valid_boundaries = True
            for b in boundaries_timeline:
                if b <= clip_start + 0.05 or b >= clip_end - 0.05:
                    valid_boundaries = False
                    break
            
            if not valid_boundaries:
                # 경계가 클립 범위 밖 - 분할 불가능, 줄바꿈만 적용
                line_breaks = processor.find_split_points(original_text, is_segment=False)
                formatted_text = original_text
                if line_breaks:
                    lines = []
                    prev = 0
                    for lb in line_breaks:
                        lines.append(original_text[prev:lb].strip())
                        prev = lb + (1 if original_text[lb] == ' ' else 0)
                    lines.append(original_text[prev:].strip())
                    formatted_text = '\n'.join(lines)
                
                new_clips.append(TimelineClip(
                    id=reserve_id(f"{base_id}_0"),
                    name=formatted_text,
                    start=clip.start,
                    duration=clip.duration,
                    track=clip.track,
                    color=clip.color,
                    clip_type="subtitle",
                    waveform=[],
                    offset=clip.offset,
                    segment_index=clip.segment_index,
                    speaker=clip.speaker,
                    words=clip.words,
                ))
                if formatted_text != original_text:
                    format_count += 1
                continue

            # 4. 텍스트와 타임스탬프로 세그먼트 생성
            segments_text = []
            segments_time = []
            segments_words = []
            
            prev_idx = 0
            prev_time = clip.start
            prev_source_time = clip.offset
            
            for split_idx, split_source_time, split_timeline_time in zip(
                segment_splits, split_timestamps, boundaries_timeline
            ):
                seg_text = original_text[prev_idx:split_idx].strip()
                segments_text.append(seg_text)
                segments_time.append((prev_time, split_timeline_time))
                
                # 단어 분할
                seg_words = [w for w in clip.words 
                            if hasattr(w, 'start') and hasattr(w, 'end') 
                            and w.start >= prev_source_time and w.end <= split_source_time]
                segments_words.append(seg_words)
                
                prev_idx = split_idx
                prev_time = split_timeline_time
                prev_source_time = split_source_time
            
            # 마지막 세그먼트
            segments_text.append(original_text[prev_idx:].strip())
            segments_time.append((prev_time, clip_end))
            seg_words = [w for w in clip.words 
                        if hasattr(w, 'start') and w.start >= prev_source_time]
            segments_words.append(seg_words)
            
            # 5. 각 세그먼트에 줄바꿈 적용 및 클립 생성
            for i, (seg_text, (seg_start, seg_end), seg_words) in enumerate(
                zip(segments_text, segments_time, segments_words)
            ):
                # 줄바꿈 처리
                line_breaks = processor.find_split_points(seg_text, is_segment=False)
                
                if line_breaks:
                    lines = []
                    prev = 0
                    for lb in line_breaks:
                        lines.append(seg_text[prev:lb].strip())
                        prev = lb + (1 if seg_text[lb] == ' ' else 0)
                    lines.append(seg_text[prev:].strip())
                    formatted_text = '\n'.join(lines)
                else:
                    formatted_text = seg_text
                
                timeline_duration = seg_end - seg_start
                
                if timeline_duration <= 0.05:
                    # 너무 짧은 세그먼트 건너뛰기
                    continue
                
                # offset은 source audio 좌표
                segment_offset = clip.offset if i == 0 else split_timestamps[i-1]
                
                new_clip = TimelineClip(
                    id=reserve_id(f"{base_id}_{i}"),
                    name=formatted_text,
                    start=seg_start,
                    duration=timeline_duration,
                    track=clip.track,
                    color=clip.color,
                    clip_type="subtitle",
                    waveform=[],
                    offset=segment_offset,
                    segment_index=clip.segment_index,
                    speaker=clip.speaker,
                    words=seg_words
                )
                new_clips.append(new_clip)
            
            if len(segments_text) > 1:
                split_count += 1
            if any('\n' in seg for seg in segments_text):
                format_count += 1
        
        # Keep old clips for undo
        old_clips_list = copy.deepcopy(self.timeline_widget.canvas.clips)

        # Replace subtitle clips
        new_clips_list = existing_non_subtitle_clips + new_clips
        self.timeline_widget.canvas.clips = new_clips_list

        # Undo Command
        cmd = ReplaceAllClipsCommand(
            self.timeline_widget.canvas,
            old_clips=old_clips_list,
            new_clips=copy.deepcopy(new_clips_list),
            description="Auto format subtitles",
            callback=self._on_undo_redo_callback
        )
        self.undo_stack.push(cmd)
        self._update_undo_redo_actions()

        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        
        self.statusBar().showMessage(f"자막 정리 완료: {split_count}개 분할, {format_count}개 줄바꿈 적용")
    
    def _realign_images_from(self, start_clip):
        """Realign image clips to match audio clips starting from the given image
        
        This takes all images from start_clip onwards and aligns them 1:1 with
        audio clips that start at or after start_clip's position.
        """
        # Get all image clips sorted by start time
        all_image_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "image"]
        all_image_clips.sort(key=lambda c: c.start)
        
        # Find index of start_clip
        try:
            start_idx = next(i for i, c in enumerate(all_image_clips) if c.id == start_clip.id)
        except StopIteration:
            return
        
        # Get images to realign (from start_clip onwards)
        images_to_realign = all_image_clips[start_idx:]
        
        # Get audio clips sorted by start time
        audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
        audio_clips.sort(key=lambda c: c.start)
        
        # Find the audio clip that contains the image's current position
        # (the audio clip where start <= image_pos < start + duration)
        current_pos = start_clip.start
        start_audio_idx = 0
        
        for i, audio in enumerate(audio_clips):
            if audio.start <= current_pos < audio.start + audio.duration:
                start_audio_idx = i
                break
            elif audio.start > current_pos:
                # Image is before any audio clip, use the first available
                start_audio_idx = i
                break
        
        available_audio = audio_clips[start_audio_idx:]
        
        if not available_audio:
            self.statusBar().showMessage("정렬할 오디오 클립이 없습니다.")
            return
        
        # Realign: each image matches an audio clip
        realigned_count = 0
        modifications = []

        for i, img_clip in enumerate(images_to_realign):
            if i >= len(available_audio):
                break
            
            audio_clip = available_audio[i]
            
            # Find next audio clip's start for duration calculation
            if i + 1 < len(available_audio):
                next_audio_start = available_audio[i + 1].start
            else:
                next_audio_start = audio_clip.start + audio_clip.duration
            
            # Record state
            old_state = copy.deepcopy(img_clip)

            # Update image clip
            img_clip.start = audio_clip.start
            img_clip.duration = next_audio_start - audio_clip.start

            new_state = copy.deepcopy(img_clip)
            modifications.append((img_clip.id, old_state, new_state))

            realigned_count += 1
        
        if modifications:
            cmd = ModifyClipsCommand(
                self.timeline_widget.canvas,
                modifications,
                description="Realign images",
                callback=self._on_undo_redo_callback
            )
            self.undo_stack.push(cmd)
            self._update_undo_redo_actions()

        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage(f"이미지 {realigned_count}개가 다시 정렬되었습니다.")
    
    def _change_clip_image(self, clip):
        """Change the image of an image clip"""
        # Check if there's a selected image in the list
        selected_items = self.image_list.selectedItems()
        path = None
        
        if selected_items:
            # Use selected image from list
            selected_path = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if selected_path:
                path = selected_path
        
        if not path:
            # Otherwise, open file dialog
            path_sel, _ = QFileDialog.getOpenFileName(
                self, "이미지 선택", "",
                "Image Files (*.png *.jpg *.jpeg *.webp);;All Files (*)"
            )
            if path_sel:
                path = path_sel

        if path:
            old_state = copy.deepcopy(clip)
            clip.image_path = path
            clip.name = Path(path).name
            new_state = copy.deepcopy(clip)

            self.timeline_widget.canvas.pixmap_cache.pop(path, None)  # Clear cache

            # Undo command
            cmd = ModifyClipsCommand(
                self.timeline_widget.canvas,
                [(clip.id, old_state, new_state)],
                description=f"Change image to {clip.name}",
                callback=self._on_undo_redo_callback
            )
            self.undo_stack.push(cmd)
            self._update_undo_redo_actions()

            self.timeline_widget.canvas._background_dirty = True
            self.timeline_widget.canvas.update()
            
            playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
            self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
            self.statusBar().showMessage(f"이미지가 변경되었습니다: {clip.name}")
    
    def _insert_image_at_clip(self, audio_clip):
        """Insert a new image clip at the position of an audio clip"""
        
        # Check if there's a selected image in the list
        selected_items = self.image_list.selectedItems()
        image_path = None
        
        if selected_items:
            image_path = selected_items[0].data(Qt.ItemDataRole.UserRole)
        
        if not image_path:
            # Open file dialog
            path, _ = QFileDialog.getOpenFileName(
                self, "이미지 선택", "",
                "Image Files (*.png *.jpg *.jpeg *.webp);;All Files (*)"
            )
            if path:
                image_path = path
        
        if not image_path:
            return
        
        # Find next audio clip to determine duration
        audio_clips = sorted(
            [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"],
            key=lambda c: c.start
        )
        
        img_start = audio_clip.start
        img_end = audio_clip.start + audio_clip.duration
        
        for i, ac in enumerate(audio_clips):
            if ac.id == audio_clip.id and i + 1 < len(audio_clips):
                img_end = audio_clips[i + 1].start
                break
        
        # Generate unique ID
        new_id = self._make_unique_clip_id(f"img_inserted_{len(audio_clips)}")
        
        # Create new image clip
        new_clip = TimelineClip(
            id=new_id,
            name=Path(image_path).name,
            start=img_start,
            duration=img_end - img_start,
            track=2,
            color=QColor("#9E9E9E"),
            clip_type="image",
            waveform=[],
            image_path=image_path
        )
        
        # Undo command
        cmd = AddRemoveClipsCommand(
            self.timeline_widget.canvas,
            added=[new_clip],
            removed=[],
            description=f"Insert image {new_clip.name}",
            callback=self._on_undo_redo_callback
        )
        self.undo_stack.push(cmd)
        cmd.redo()
        self._update_undo_redo_actions()

        self.timeline_widget.canvas._update_total_duration()
        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        
        # Load image into cache for thumbnail display
        from .image_cache import get_image_cache
        cache = get_image_cache()
        if not cache.is_loaded(image_path):
            cache.load_images([image_path])
        
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage(f"이미지가 삽입되었습니다: {new_clip.name}")
    
    def _on_image_dropped(self, image_path: str, drop_time: float):
        """Handle image dropped onto timeline via drag and drop"""
        from pathlib import Path
        
        # Fixed duration for dropped images (3 seconds)
        duration = 3.0
        
        # Generate unique ID
        base_id = f"img_dropped_{len([c for c in self.timeline_widget.canvas.clips if c.clip_type == 'image'])}"
        new_id = self._make_unique_clip_id(base_id)
        
        # Create new image clip
        new_clip = TimelineClip(
            id=new_id,
            name=Path(image_path).name,
            start=drop_time,
            duration=duration,
            track=2,
            color=QColor("#9E9E9E"),
            clip_type="image",
            waveform=[],
            image_path=image_path
        )
        
        # Undo command
        from .undo_system import AddRemoveClipsCommand
        cmd = AddRemoveClipsCommand(
            self.timeline_widget.canvas,
            added=[new_clip],
            removed=[],
            description=f"드래그로 이미지 추가: {new_clip.name}",
            callback=self._on_undo_redo_callback
        )
        self.undo_stack.push(cmd)
        cmd.redo()
        self._update_undo_redo_actions()
        
        self.timeline_widget.canvas._update_total_duration()
        self.timeline_widget.canvas._background_dirty = True
        self.timeline_widget.canvas.update()
        
        # Load image into cache for thumbnail display
        from .image_cache import get_image_cache
        cache = get_image_cache()
        if not cache.is_loaded(image_path):
            cache.load_images([image_path])
        
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage(f"이미지가 추가되었습니다: {new_clip.name}")

    
    def _delete_clip(self, clip):
        """Delete a clip from the timeline"""
        if clip in self.timeline_widget.canvas.clips:
            # Undo command
            cmd = AddRemoveClipsCommand(
                self.timeline_widget.canvas,
                added=[],
                removed=[clip],
                description=f"Delete {clip.name}",
                callback=self._on_undo_redo_callback
            )
            self.undo_stack.push(cmd)
            cmd.redo()
            self._update_undo_redo_actions()

            # Remove from AudioMixer if audio clip (handled by _on_undo_redo_callback mostly, but ensure)
            if clip.clip_type == "audio":
                self.preview_widget.remove_audio_clip(clip.id)
            
            self.timeline_widget.canvas._background_dirty = True
            self.timeline_widget.canvas.update()
            playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
            self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
            
            # Update total duration
            all_clips = self.timeline_widget.canvas.clips
            if all_clips:
                total_duration = max((c.start + c.duration for c in all_clips), default=0.0)
                self.preview_widget.set_total_duration(total_duration)
            
            self.statusBar().showMessage(f"클립이 삭제되었습니다: {clip.name}")
    
    def _on_clip_delete_requested(self, clip_id: str):
        """Handle delete key press on timeline - delete the selected clip"""
        # Find the clip by ID
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        if clip:
            self._delete_clip(clip)
            # Clear selection after delete
            self.timeline_widget.canvas.selected_clip = None
    
    def _regenerate_preview_from_clips(self):
        """Rebuild AudioMixer with all current clips"""
        try:
            from .audio_mixer import ScheduledClip
            
            # Use self.speaker_audio_map directly (works for both fresh and loaded projects)
            speaker_audio_map = self.speaker_audio_map or {}
            
            # Get audio clips sorted by start time
            audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
            audio_clips.sort(key=lambda c: c.start)
            
            if not audio_clips:
                return
            
            # Build scheduled clips for AudioMixer
            scheduled_clips = []
            for clip in audio_clips:
                scheduled_clips.append(ScheduledClip(
                    clip_id=clip.id,
                    speaker=clip.speaker,
                    timeline_start=clip.start,
                    timeline_end=clip.start + clip.duration,
                    source_offset=clip.offset,
                    source_path=speaker_audio_map.get(clip.speaker, ""),
                    duration=clip.duration
                ))
            
            # Calculate total timeline duration from ALL clips (audio, subtitle, image)
            all_clips = self.timeline_widget.canvas.clips
            total_timeline_duration = max((c.start + c.duration for c in all_clips), default=0.0)
            
            # Save current playhead position to restore after update
            current_playhead = self.timeline_widget.canvas.playhead_time
            
            # Set up the AudioMixer with new clips
            self.preview_widget.set_audio_clips(scheduled_clips, speaker_audio_map)
            
            # Update preview widget total duration to match timeline
            self.preview_widget.set_total_duration(total_timeline_duration)
            
            # Restore playhead position
            self.preview_widget.audio_mixer.seek(current_playhead)
            
            # Also ensure timeline playhead stays in sync (UI side)
            self.timeline_widget.set_playhead(current_playhead)
            
            self.statusBar().showMessage("미리보기 오디오 업데이트됨")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"오디오 재생성 실패: {str(e)}")
    
    def _export_srt(self):
        """Export SRT file"""
        # Allow export from loaded projects (timeline clips are the source of truth)
        sub_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "subtitle"]
        if not sub_clips:
            QMessageBox.warning(self, "오류", "내보낼 자막 클립이 없습니다.")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self, "SRT 저장", "subtitles.srt", "SRT Files (*.srt)"
        )
        if path:
            try:
                from exporters.srt_generator import SRTGenerator
                
                # Get subtitle clips from timeline
                sub_clips.sort(key=lambda c: c.start)
                
                texts = []
                timestamps = []
                
                for clip in sub_clips:
                    texts.append(clip.name)
                    timestamps.append((clip.start, clip.start + clip.duration))
                
                generator = SRTGenerator()
                entries = generator.generate_entries(texts, timestamps)
                generator.save(entries, path)
                
                QMessageBox.information(self, "저장 완료", f"SRT 파일이 저장되었습니다:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"SRT 저장 실패: {str(e)}")
    
    def _export_xml(self):
        """Export XML file"""
        # Allow export from loaded projects: export current timeline state.
        if not self.timeline_widget.canvas.clips:
            QMessageBox.warning(self, "오류", "내보낼 타임라인 클립이 없습니다.")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self, "XML 저장", "project.xml", "XML Files (*.xml)"
        )
        if path:
            try:
                from exporters.xml_exporter import XMLExporter, TimelineClip

                speaker_audio_map = (self.result_data.get('speaker_audio_map', {}) if self.result_data else None) or self.speaker_audio_map

                clips: list[TimelineClip] = []
                for clip in self.timeline_widget.canvas.clips:
                    if clip.clip_type == "audio":
                        speaker = clip.speaker
                        if not speaker and ":" in (clip.name or ""):
                            speaker = clip.name.split(":")[0].strip()

                        audio_path = speaker_audio_map.get(speaker, "") if speaker else ""
                        if not audio_path:
                            # Skip clips we can't resolve to a source file
                            continue

                        clips.append(
                            TimelineClip(
                                name=clip.name,
                                file_path=audio_path,
                                start_time=clip.start,
                                end_time=clip.start + clip.duration,
                                track=clip.track,
                                clip_type="audio",
                                source_in=clip.offset,  # Source file offset
                                source_out=clip.offset + clip.duration,  # Source file end
                            )
                        )

                    elif clip.clip_type == "image":
                        if not clip.image_path:
                            continue
                        clips.append(
                            TimelineClip(
                                name=clip.name,
                                file_path=clip.image_path,
                                start_time=clip.start,
                                end_time=clip.start + clip.duration,
                                track=clip.track,
                                clip_type="video",
                            )
                        )

                if not clips:
                    QMessageBox.warning(self, "오류", "XML로 내보낼 수 있는 클립(오디오/이미지)을 찾지 못했습니다.")
                    return
                
                exporter = XMLExporter()
                exporter.save(clips, path)
                
                QMessageBox.information(self, "저장 완료", f"XML 파일이 저장되었습니다:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"XML 저장 실패: {str(e)}")
    
    def _render_video(self):
        """Render final video by launching background thread"""
        # Allow rendering from loaded projects (timeline clips are the source of truth)
        audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
        if not audio_clips:
            QMessageBox.warning(self, "오류", "렌더링할 오디오 클립이 없습니다.")
            return
        
        # Show render settings dialog first
        dialog = RenderSettingsDialog(self, clips=self.timeline_widget.canvas.clips, speaker_audio_map=self.speaker_audio_map)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        render_settings = dialog.get_settings()

        path, _ = QFileDialog.getSaveFileName(
            self, "영상 저장", "output.mp4", "Video Files (*.mp4);;Audio Files (*.wav)"
        )
        if not path:
            return
        
        # For .wav, just merge audio synchronously
        if path.endswith('.wav'):
            self._export_audio_only(path)
            return
        
        # For .mp4, launch render thread
        self.action_render.setEnabled(False)
        self.statusBar().showMessage("렌더링 준비 중...")
        
        # Collect image and subtitle clips
        image_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "image"]
        subtitle_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "subtitle"]
        
        # Filter subtitles if disabled in settings
        if not render_settings.get('subtitle_enabled', True):
            subtitle_clips = []

        # Generate temp audio file for rendering
        import tempfile
        import os
        try:
            fd, temp_audio_path = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            self._create_merged_audio(temp_audio_path)
            self._temp_render_audio = temp_audio_path
        except Exception as e:
            QMessageBox.critical(self, "오류", f"임시 오디오 생성 실패: {str(e)}")
            return

        # Create and start render thread
        self.render_thread = RenderThread(
            image_clips=image_clips,
            audio_path=self._temp_render_audio,
            subtitle_clips=subtitle_clips,
            output_path=path,
            render_settings=render_settings
        )
        self.render_thread.progress.connect(self._on_render_progress)
        self.render_thread.finished.connect(self._on_render_finished)
        self.render_thread.start()
    
    def _create_merged_audio(self, output_path: str):
        """Create a merged WAV file from timeline audio clips"""
        from pydub import AudioSegment
        
        speaker_audio_map = (self.result_data.get('speaker_audio_map', {}) if self.result_data else None) or self.speaker_audio_map

        # Load speaker audio files
        speaker_audio: dict[str, AudioSegment] = {}
        for speaker, audio_path in speaker_audio_map.items():
            if audio_path:
                speaker_audio[speaker] = AudioSegment.from_file(audio_path)
        
        # Get audio clips from timeline
        audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
        audio_clips.sort(key=lambda c: c.start)
        
        # Create merged audio
        result_audio = AudioSegment.empty()
        current_pos = 0.0
        
        for clip in audio_clips:
            speaker = clip.speaker
            if speaker in speaker_audio:
                audio = speaker_audio[speaker]
                
                # Add silence gap if needed
                gap_duration = int((clip.start - current_pos) * 1000)
                if gap_duration > 0:
                    result_audio += AudioSegment.silent(duration=gap_duration)
    
                # Calculate original segment duration
                padded_duration_ms = int(clip.duration * 1000)
                segment_duration_ms = padded_duration_ms
                segment_end = clip.offset + (segment_duration_ms / 1000.0)
                
                # Extract segment from source audio
                start_ms = max(0, int(clip.offset * 1000))
                end_ms = min(len(audio), int(segment_end * 1000))
                audio_clip = audio[start_ms:end_ms]
                
                result_audio += audio_clip
                current_pos = clip.start + len(audio_clip) / 1000.0
        
        # Pad with silence to match total timeline duration (e.g. if images extend beyond audio)
        total_timeline_duration = max((c.start + c.duration for c in self.timeline_widget.canvas.clips), default=0.0)
        current_audio_duration = len(result_audio) / 1000.0
        
        if total_timeline_duration > current_audio_duration:
            silence_gap = int((total_timeline_duration - current_audio_duration) * 1000)
            if silence_gap > 0:
                result_audio += AudioSegment.silent(duration=silence_gap)
        
        result_audio.export(output_path, format='wav')

    def _export_audio_only(self, path: str):
        """Export audio-only .wav file"""
        try:
            self._create_merged_audio(path)
            QMessageBox.information(self, "저장 완료", f"파일이 저장되었습니다:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"오디오 내보내기 실패: {str(e)}")
    
    def _on_render_progress(self, progress: int, message: str):
        """Handle render thread progress update"""
        self.statusBar().showMessage(f"{message} ({progress}%)")
    
    def _on_render_finished(self, success: bool, message: str):
        """Handle render thread completion"""
        self.action_render.setEnabled(True)
        if success:
            self.statusBar().showMessage("렌더링 완료")
            QMessageBox.information(self, "완료", message)
        else:
            self.statusBar().showMessage("렌더링 실패")
            QMessageBox.critical(self, "오류", message)
            
        # Cleanup temp audio
        if hasattr(self, '_temp_render_audio') and self._temp_render_audio:
            import os
            try:
                if os.path.exists(self._temp_render_audio):
                    os.remove(self._temp_render_audio)
            except:
                pass
            self._temp_render_audio = None
    def _export_audio_dialog(self):
        """Show dialog to export audio only"""
        path, _ = QFileDialog.getSaveFileName(
            self, "오디오 저장", "output.wav", "Audio Files (*.wav)"
        )
        if path:
            self._export_audio_only(path)

    def _new_project(self):
        """Create a new project"""
        from PyQt6.QtWidgets import QMessageBox
        
        # Ask to save current project if modified
        if self.timeline_widget.canvas.clips:
            reply = QMessageBox.question(
                self, "새 프로젝트",
                "현재 프로젝트를 저장하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Yes:
                self._save_project()
        
        # Reset state
        self.script_path = None
        self.image_folder = None
        self.speakers = []
        self.speaker_audio_map = {}
        self.audio_files = []
        self.result_data = None
        self.project_path = None
        
        # Reset timeline
        self.timeline_widget.set_clips([])
        self.preview_widget.set_timeline_clips([])
        
        # Reset UI
        self.script_text.setPlainText("")
        self.image_list.clear()
        
        # Reset mapping table
        self.mapping_table.setRowCount(0)
        self.mapping_info.setText("스크립트를 불러오면 화자 목록이 표시됩니다.")
        
        # Reset buttons
        self.action_process.setEnabled(False)
        self.action_format_subs.setEnabled(False)
        self.action_export_srt.setEnabled(False)
        self.action_export_xml.setEnabled(False)
        self.action_render.setEnabled(False)
        self.action_apply_images.setEnabled(False)
        self.action_export_audio.setEnabled(False)
        self.reload_images_action.setEnabled(False)
        
        # Reset preview
        self.preview_widget.clear_preview()
        
        self.setWindowTitle("PictureBookBuilder")
        self.statusBar().showMessage("새 프로젝트가 생성되었습니다.")
    
    def _open_project(self):
        """Open an existing project file"""
        from PyQt6.QtWidgets import QFileDialog
        import json
        
        path, _ = QFileDialog.getOpenFileName(
            self, "프로젝트 열기", "",
            "PictureBookBuilder Project (*.pbb);;All Files (*)"
        )
        
        if not path:
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._load_project_data(data)
            self.project_path = path
            self.setWindowTitle(f"PictureBookBuilder - {Path(path).name}")
            self.statusBar().showMessage(f"프로젝트를 불러왔습니다: {path}")
            
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "오류", f"프로젝트를 열 수 없습니다:\n{str(e)}")
    
    def _save_project(self):
        """Save current project"""
        if self.project_path:
            self._save_to_file(self.project_path)
        else:
            self._save_project_as()
    
    def _save_project_as(self):
        """Save project as a new file"""
        path, _ = QFileDialog.getSaveFileName(
            self, "프로젝트 저장", "", "PictureBookBuilder Files (*.pbb);;All Files (*)"
        )
        if path:
            if not path.endswith('.pbb'):
                path += '.pbb'
            self.project_path = path
            self._save_to_file(path)
            self.setWindowTitle(f"PictureBookBuilder - {Path(path).name}")
            
    def _show_settings(self):
        """Show the settings dialog"""
        dialog = SettingsDialog(self)
        dialog.set_config(self.runtime_config)
        dialog.exec()

    
    def _save_to_file(self, path: str):
        """Save project data to file"""
        import json
        from datetime import datetime
        
        # Serialize clips
        clips_data = []
        for clip in self.timeline_widget.canvas.clips:
            clip_dict = {
                'id': clip.id,
                'name': clip.name,
                'start': clip.start,
                'duration': clip.duration,
                'track': clip.track,
                'color': clip.color.name(),
                'clip_type': clip.clip_type,
                'offset': clip.offset,  # Simplified: single offset field
                'segment_index': clip.segment_index,
                'speaker': clip.speaker,  # Save speaker for audio clips
            }
            
            # Add type-specific data
            if clip.clip_type == "image":
                clip_dict['image_path'] = clip.image_path
            
            # Serialize words if available
            if clip.words:
                clip_dict['words'] = [
                    {'text': w.text if hasattr(w, 'text') else str(w),
                     'start': w.start if hasattr(w, 'start') else 0.0,
                     'end': w.end if hasattr(w, 'end') else 0.0}
                    for w in clip.words
                ]
            
            clips_data.append(clip_dict)
        
        # Save script content directly (in case file is moved)
        script_content = self.script_text.toPlainText()
        
        project_data = {
            'version': '1.1',  # Bumped for settings support
            'saved_at': datetime.now().isoformat(),
            'script_path': self.script_path,
            'script_content': script_content,  # Save script content
            'image_folder': self.image_folder,
            'speaker_audio_map': self.speaker_audio_map,
            'clips': clips_data,
            'settings': self.runtime_config.to_dict(),  # Save settings
        }
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"프로젝트가 저장되었습니다: {path}")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "오류", f"프로젝트를 저장할 수 없습니다:\n{str(e)}")
    
    def _load_project_data(self, data: dict):
        """Load project data from dictionary"""
        from ui.timeline_widget import TimelineClip
        from PyQt6.QtGui import QColor
        
        # Load basic info
        self.script_path = data.get('script_path')
        self.image_folder = data.get('image_folder')
        self.speaker_audio_map = data.get('speaker_audio_map', {})
        
        # Load settings if present
        if 'settings' in data:
            self.runtime_config = RuntimeConfig.from_dict(data['settings'])
            set_config(self.runtime_config)
        
        # Load script - prefer from file, fallback to saved content
        if self.script_path and Path(self.script_path).exists():
            with open(self.script_path, 'r', encoding='utf-8') as f:
                self.script_text.setPlainText(f.read())
        elif 'script_content' in data and data['script_content']:
            # Fallback to saved content if file doesn't exist
            self.script_text.setPlainText(data['script_content'])
            print(f"Script file not found: {self.script_path}, using saved content")
        
        # Validate audio files exist
        missing_audio = []
        for speaker, audio_path in self.speaker_audio_map.items():
            if audio_path and not Path(audio_path).exists():
                missing_audio.append(f"  - {speaker}: {Path(audio_path).name}")
        
        # Track missing images
        missing_images = []
        
        # Rebuild clips
        clips = []
        for clip_data in data.get('clips', []):
            # Reconstruct words
            words = []
            if 'words' in clip_data:
                from core.subtitle_processor import WordSegment
                for w in clip_data['words']:
                    words.append(WordSegment(
                        text=w['text'],
                        start=w['start'],
                        end=w['end']
                    ))
            
            # Validate image path exists for image clips
            image_path = clip_data.get('image_path')
            if image_path and not Path(image_path).exists():
                missing_images.append(f"  - {Path(image_path).name}")
            
            clip = TimelineClip(
                id=clip_data['id'],
                name=clip_data['name'],
                start=clip_data['start'],
                duration=clip_data['duration'],
                track=clip_data['track'],
                color=QColor(clip_data['color']),
                clip_type=clip_data['clip_type'],
                waveform=[],  # Will regenerate if needed
                offset=clip_data.get('offset', 0.0),
                segment_index=clip_data.get('segment_index', -1),
                image_path=image_path,
                speaker=clip_data.get('speaker', ''),  # Restore speaker
                words=words
            )
            clips.append(clip)
        
        self.timeline_widget.set_clips(clips)
        self.preview_widget.set_timeline_clips(clips)
        
        # Restore speaker-audio mapping table
        self.speakers = list(self.speaker_audio_map.keys())
        self.mapping_table.setRowCount(len(self.speakers))
        for row, speaker in enumerate(self.speakers):
            # Speaker name
            speaker_item = QTableWidgetItem(speaker)
            speaker_item.setFlags(speaker_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.mapping_table.setItem(row, 0, speaker_item)
            
            # Audio file
            audio_path = self.speaker_audio_map.get(speaker, "")
            if audio_path:
                audio_item = QTableWidgetItem(Path(audio_path).name)
                audio_item.setForeground(QColor(100, 200, 100))
            else:
                audio_item = QTableWidgetItem("(클릭하여 선택)")
                audio_item.setForeground(QColor(150, 150, 150))
            self.mapping_table.setItem(row, 1, audio_item)
        
        # Update mapping status
        self._update_mapping_status()
        
        # Restore image folder and list with thumbnails
        self.image_list.clear()
        print(f"Loading image folder: {self.image_folder}")
        if self.image_folder and Path(self.image_folder).exists():
            self._populate_image_list(self.image_folder)
            self.reload_images_action.setEnabled(True)
            print(f"  Loaded images with thumbnails")
        else:
            self.reload_images_action.setEnabled(False)
            print(f"  Image folder not found or empty: {self.image_folder}")
        
        # Enable buttons if we have clips
        if clips:
            self.action_format_subs.setEnabled(True)
            self.action_export_srt.setEnabled(True)
            self.action_export_xml.setEnabled(True)
            self.action_render.setEnabled(True)
            self.action_export_audio.setEnabled(True)
            
            # Enable image apply button if we have images
            if self.image_list.count() > 0:
                self.action_apply_images.setEnabled(True)
            
            # Load speaker audio cache for waveform regeneration
            self._load_speaker_audio_cache()
            
            # Regenerate waveforms for audio clips
            self._regenerate_waveforms()
            
            # Generate preview audio from clips
            self._regenerate_preview_from_clips()
        
        # Show warning if files are missing
        if missing_audio or missing_images:
            from PyQt6.QtWidgets import QMessageBox
            
            warning_msg = "일부 파일을 찾을 수 없습니다:\n\n"
            
            if missing_audio:
                warning_msg += "📀 누락된 오디오 파일:\n"
                warning_msg += "\n".join(missing_audio[:5])  # Show first 5
                if len(missing_audio) > 5:
                    warning_msg += f"\n  ... 외 {len(missing_audio) - 5}개"
                warning_msg += "\n\n"
            
            if missing_images:
                # Remove duplicates
                unique_missing_images = list(set(missing_images))
                warning_msg += "🖼️ 누락된 이미지 파일:\n"
                warning_msg += "\n".join(unique_missing_images[:5])  # Show first 5
                if len(unique_missing_images) > 5:
                    warning_msg += f"\n  ... 외 {len(unique_missing_images) - 5}개"
            
            warning_msg += "\n\n파일을 원래 위치로 복원하거나 다시 지정해주세요."
            
            QMessageBox.warning(self, "파일 누락 경고", warning_msg)
    
    def _load_speaker_audio_cache(self):
        """Load speaker audio files into cache"""
        from pydub import AudioSegment
        
        self.speaker_audio_cache = {}
        print(f"Loading speaker audio cache. speaker_audio_map: {self.speaker_audio_map}")
        for speaker, audio_path in self.speaker_audio_map.items():
            if audio_path and Path(audio_path).exists():
                try:
                    self.speaker_audio_cache[speaker] = AudioSegment.from_file(audio_path)
                    print(f"  Loaded audio for speaker: {speaker}")
                except Exception as e:
                    print(f"  Failed to load audio for {speaker}: {e}")
            else:
                print(f"  Audio path not found for speaker {speaker}: {audio_path}")
        
        # Pass audio cache to timeline canvas for real-time waveform updates
        self.timeline_widget.canvas.speaker_audio_cache = self.speaker_audio_cache
        self.timeline_widget.canvas.waveform_extractor = self._extract_waveform_from_audio

    
    def _regenerate_waveforms(self):
        """Regenerate waveforms for audio clips from cached audio"""
        regenerated_count = 0
        for clip in self.timeline_widget.canvas.clips:
            if clip.clip_type == "audio":
                # Use clip.speaker attribute directly, fallback to parsing from name
                speaker = clip.speaker if clip.speaker else None
                if not speaker and ":" in clip.name:
                    speaker = clip.name.split(":")[0].strip()
                
                if speaker and speaker in self.speaker_audio_cache:
                    audio = self.speaker_audio_cache[speaker]

                    # Calculate original segment duration
                    padded_duration_ms = int(clip.duration * 1000)
                    segment_duration_ms = padded_duration_ms
                    segment_end = clip.offset + (segment_duration_ms / 1000.0)
                    
                    start_ms = max(0, int(clip.offset * 1000))
                    end_ms = min(len(audio), int(segment_end * 1000))
                    segment = audio[start_ms:end_ms]
                    
                    # Generate waveform
                    clip.waveform = self._extract_waveform_from_audio(segment)
                    regenerated_count += 1
                else:
                    print(f"Could not regenerate waveform for clip: {clip.name}, speaker: {speaker}")
                    print(f"  Available speakers in cache: {list(self.speaker_audio_cache.keys())}")
        
        print(f"Regenerated {regenerated_count} waveforms")
        self.timeline_widget.canvas.update()


def main():
    """Application entry point"""
    app = QApplication(sys.argv)
    
    # Apply Modern Dark Theme
    ModernDarkTheme.apply(app)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

