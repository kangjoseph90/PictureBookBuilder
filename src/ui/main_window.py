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
    QSplitter, QTextEdit, QSlider, QSpinBox, QProgressBar,
    QGroupBox, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

from .timeline_widget import TimelineWidget
from .preview_widget import PreviewWidget


class ProcessingThread(QThread):
    """Background thread for audio processing"""
    progress = pyqtSignal(int, str)  # progress %, status message
    finished = pyqtSignal(bool, str, object)  # success, message, result data
    
    def __init__(self, script_path: str, speaker_audio_map: dict, image_folder: str):
        super().__init__()
        self.script_path = script_path
        self.speaker_audio_map = speaker_audio_map  # Now directly passed
        self.image_folder = image_folder
    
    def run(self):
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
            
            # Step 3: Transcribe audio files
            self.progress.emit(20, "오디오 변환 중 (Whisper)...")
            transcriber = Transcriber()
            transcriptions = {}
            
            total_speakers = len(self.speaker_audio_map)
            for i, (speaker, audio_path) in enumerate(self.speaker_audio_map.items()):
                if audio_path:
                    progress = 20 + int((i / total_speakers) * 25)
                    self.progress.emit(progress, f"Whisper 변환 중: {speaker}...")
                    transcriptions[speaker] = transcriber.transcribe(audio_path)
            
            # Step 4: Align dialogues
            self.progress.emit(50, "대사 정렬 중...")
            aligner = Aligner()
            aligned = aligner.align_all(dialogues, transcriptions)
            
            # Step 5: VAD Refinement - refine segment boundaries with Silero VAD
            self.progress.emit(60, "VAD로 경계 보정 중...")
            vad = VADProcessor()
            
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
                        refined_start, refined_end = vad.trim_segment_boundaries(
                            audio,
                            segment.start_time,
                            segment.end_time,
                            prev_end_time=prev_end
                        )
                        
                        # Update segment with refined boundaries
                        segment.start_time = refined_start
                        segment.end_time = refined_end
                        
                        # Store this segment's end for next iteration
                        prev_end_by_speaker[speaker] = refined_end
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


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PictureBookBuilder")
        self.setMinimumSize(1200, 800)
        
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
        
        self._setup_ui()
        self._setup_menu_bar()
    
    def _setup_menu_bar(self):
        """Setup the menu bar with File menu"""
        from PyQt6.QtGui import QAction, QKeySequence
        
        menu_bar = self.menuBar()
        
        # File menu
        file_menu = menu_bar.addMenu("파일(&F)")
        
        # New project
        new_action = QAction("새 프로젝트(&N)", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)
        
        # Open project
        open_action = QAction("프로젝트 열기(&O)...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        # Save project
        save_action = QAction("저장(&S)", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)
        
        # Save as
        save_as_action = QAction("다른 이름으로 저장(&A)...", self)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)
    
    def _setup_ui(self):
        """Setup the main UI layout"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        
        # Top toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)
        
        # Main content area (splitter)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left panel - Script and speaker mapping
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel - Preview and timeline
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        splitter.setSizes([450, 750])
        main_layout.addWidget(splitter, 1)
        
        # Bottom controls
        bottom_controls = self._create_bottom_controls()
        main_layout.addLayout(bottom_controls)
        
        # Status bar
        self.statusBar().showMessage("준비")
    
    def _create_toolbar(self) -> QHBoxLayout:
        """Create the top toolbar"""
        layout = QHBoxLayout()
        
        # Script button
        self.btn_script = QPushButton("📂 스크립트")
        self.btn_script.clicked.connect(self._load_script)
        layout.addWidget(self.btn_script)
        
        # Image folder button
        self.btn_image = QPushButton("🖼️ 이미지 폴더")
        self.btn_image.clicked.connect(self._load_image_folder)
        layout.addWidget(self.btn_image)
        
        layout.addStretch()
        
        # Process button
        self.btn_process = QPushButton("▶️ 처리 시작")
        self.btn_process.clicked.connect(self._start_processing)
        self.btn_process.setEnabled(False)
        layout.addWidget(self.btn_process)
        
        # Subtitle auto-format button
        self.btn_format_subtitles = QPushButton("🔧 자막 자동 정리")
        self.btn_format_subtitles.setToolTip("자막에 줄바꿈 적용 및 긴 자막 분할")
        self.btn_format_subtitles.clicked.connect(self._auto_format_subtitles)
        self.btn_format_subtitles.setEnabled(False)
        layout.addWidget(self.btn_format_subtitles)
        
        return layout
    
    def _create_left_panel(self) -> QWidget:
        """Create left panel with script view and speaker mapping"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # Script preview
        script_group = QGroupBox("스크립트")
        script_layout = QVBoxLayout(script_group)
        self.script_text = QTextEdit()
        self.script_text.setReadOnly(True)
        self.script_text.setPlaceholderText("스크립트 파일을 불러오세요...\n\n지원 형식:\n* 화자: 대사\n- 화자: 대사\n화자: 대사")
        self.script_text.setMaximumHeight(200)
        script_layout.addWidget(self.script_text)
        layout.addWidget(script_group)
        
        # Speaker-Audio Mapping
        mapping_group = QGroupBox("화자별 오디오 매핑")
        mapping_layout = QVBoxLayout(mapping_group)
        
        # Table for speaker-audio mapping
        self.mapping_table = QTableWidget()
        self.mapping_table.setColumnCount(3)
        self.mapping_table.setHorizontalHeaderLabels(["화자", "오디오 파일", "선택"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mapping_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mapping_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        mapping_layout.addWidget(self.mapping_table)
        
        # Info label
        self.mapping_info = QLabel("스크립트를 불러오면 화자 목록이 표시됩니다.")
        self.mapping_info.setStyleSheet("color: gray; font-style: italic;")
        mapping_layout.addWidget(self.mapping_info)
        
        layout.addWidget(mapping_group)
        
        # Image files list
        image_group = QGroupBox("이미지 파일")
        image_layout = QVBoxLayout(image_group)
        self.image_list = QListWidget()
        self.image_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        image_layout.addWidget(self.image_list)
        layout.addWidget(image_group)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """Create right panel with preview and timeline (with splitter)"""
        # Create a vertical splitter for preview and timeline
        from PyQt6.QtWidgets import QSplitter
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(5)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #444;
            }
            QSplitter::handle:hover {
                background-color: #666;
            }
        """)
        
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
        
        timeline_group_layout.addWidget(self.timeline_widget)
        timeline_layout.addWidget(timeline_group)
        
        splitter.addWidget(timeline_container)
        
        # Set initial sizes (preview smaller, timeline larger)
        splitter.setSizes([300, 200])
        
        return splitter
    
    def _create_bottom_controls(self) -> QHBoxLayout:
        """Create bottom control bar"""
        layout = QHBoxLayout()
        
        # Gap control
        layout.addWidget(QLabel("Gap 간격:"))
        self.gap_slider = QSlider(Qt.Orientation.Horizontal)
        self.gap_slider.setRange(0, 200)  # 0 to 2 seconds in 10ms steps
        self.gap_slider.setValue(50)  # Default 0.5s
        self.gap_slider.valueChanged.connect(self._on_gap_changed)
        layout.addWidget(self.gap_slider)
        
        self.gap_spinbox = QSpinBox()
        self.gap_spinbox.setRange(0, 2000)
        self.gap_spinbox.setValue(500)
        self.gap_spinbox.setSuffix(" ms")
        self.gap_spinbox.valueChanged.connect(self._on_gap_spinbox_changed)
        layout.addWidget(self.gap_spinbox)
        
        layout.addStretch()
        
        # Export buttons
        self.btn_export_srt = QPushButton("📥 SRT 내보내기")
        self.btn_export_srt.clicked.connect(self._export_srt)
        self.btn_export_srt.setEnabled(False)
        layout.addWidget(self.btn_export_srt)
        
        self.btn_export_xml = QPushButton("📥 XML 내보내기")
        self.btn_export_xml.clicked.connect(self._export_xml)
        self.btn_export_xml.setEnabled(False)
        layout.addWidget(self.btn_export_xml)
        
        self.btn_render = QPushButton("🎬 영상 렌더링")
        self.btn_render.clicked.connect(self._render_video)
        self.btn_render.setEnabled(False)
        layout.addWidget(self.btn_render)
        
        return layout
    
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
            
            self.btn_script.setText(f"📂 {Path(path).name}")
            
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
            
            # Select button
            btn = QPushButton("📂 선택")
            btn.clicked.connect(lambda checked, s=speaker, row=i: self._select_audio_for_speaker(s, row))
            self.mapping_table.setCellWidget(i, 2, btn)
            
            self.speaker_audio_map[speaker] = ""
        
        # Update info
        self.mapping_info.setText(f"{len(self.speakers)}명의 화자 감지됨. 각 화자에 오디오 파일을 지정하세요.")
        self.mapping_info.setStyleSheet("color: orange;")
    
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
            self.mapping_info.setText(f"✅ 모든 화자({total}명)에 오디오가 지정되었습니다!")
            self.mapping_info.setStyleSheet("color: green;")
        else:
            self.mapping_info.setText(f"⚠️ {mapped}/{total}명 지정됨. 모든 화자에 오디오를 지정하세요.")
            self.mapping_info.setStyleSheet("color: orange;")
    
    def _load_image_folder(self):
        """Load image folder"""
        path = QFileDialog.getExistingDirectory(self, "이미지 폴더 선택")
        if path:
            self.image_folder = path
            self.image_list.clear()
            image_path = Path(path)
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                for f in sorted(image_path.glob(ext)):
                    item = QListWidgetItem(f"🖼️ {f.name}")
                    self.image_list.addItem(item)
            self.btn_image.setText(f"🖼️ {image_path.name}")
            
            # If processing is already done, refresh timeline and preview with new images
            if self.result_data and 'aligned' in self.result_data:
                self._update_timeline(self.result_data)
    
    def _check_ready(self):
        """Check if we have all inputs to start processing"""
        # Need script and all speakers mapped
        all_mapped = all(self.speaker_audio_map.get(s) for s in self.speakers)
        ready = bool(self.script_path and self.speakers and all_mapped)
        self.btn_process.setEnabled(ready)
    
    def _start_processing(self):
        """Start the processing thread"""
        self.btn_process.setEnabled(False)
        self.statusBar().showMessage("처리 중...")
        
        self.processing_thread = ProcessingThread(
            self.script_path,
            self.speaker_audio_map.copy(),
            self.image_folder or ""
        )
        self.processing_thread.progress.connect(self._on_progress)
        self.processing_thread.finished.connect(self._on_processing_finished)
        self.processing_thread.start()
    
    def _on_progress(self, percent: int, message: str):
        """Handle progress updates"""
        self.statusBar().showMessage(f"{message} ({percent}%)")
    
    def _on_processing_finished(self, success: bool, message: str, result: Optional[dict]):
        """Handle processing completion"""
        self.btn_process.setEnabled(True)
        self.result_data = result
        
        if success:
            self.statusBar().showMessage("처리 완료")
            self.btn_export_srt.setEnabled(True)
            self.btn_export_xml.setEnabled(True)
            self.btn_render.setEnabled(True)
            
            # Update timeline with aligned clips
            if result and 'aligned' in result:
                self._update_timeline(result)
                # Generate preview audio
                self._generate_preview_audio(result)
            
            QMessageBox.information(self, "완료", message)
        else:
            self.statusBar().showMessage("오류 발생")
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
        gap = self.gap_spinbox.value() / 1000.0
        
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
            actual_duration = duration  # Will be updated to include padding
            
            if speaker in speaker_audio:
                audio = speaker_audio[speaker]
                from config import CLIP_PADDING_START_MS, CLIP_PADDING_END_MS
                
                start_ms = max(0, int(segment.start_time * 1000) - CLIP_PADDING_START_MS)
                end_ms = min(len(audio), int(segment.end_time * 1000) + CLIP_PADDING_END_MS)
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
                source_start=segment.start_time,
                source_end=segment.end_time,
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
                segment_index=i,
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
        
        self.timeline_widget.canvas.clips = clips
        self.timeline_widget.canvas.update()
        
        # Sync to preview widget
        self.preview_widget.set_timeline_clips(clips)
        
        # Enable subtitle formatting button
        self.btn_format_subtitles.setEnabled(True)
    
    def _extract_waveform_from_audio(self, audio_segment) -> list[float]:
        """Extract normalized waveform data from an audio segment"""
        import numpy as np
        
        # Convert to numpy array
        samples = np.array(audio_segment.get_array_of_samples())
        waveform = []
        
        if len(samples) > 0:
            # Downsample for display (target ~200 samples per clip)
            target_samples = 200
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
        """Generate a temporary audio file for preview playback"""
        try:
            from pydub import AudioSegment
            import tempfile
            
            aligned = result.get('aligned', [])
            speaker_audio_map = result.get('speaker_audio_map', {})
            gap = self.gap_spinbox.value() / 1000.0
            
            if not aligned:
                return
            
            self.statusBar().showMessage("미리보기 오디오 생성 중...")
            
            # Load speaker audio files
            speaker_audio: dict[str, AudioSegment] = {}
            for speaker, audio_path in speaker_audio_map.items():
                if audio_path:
                    speaker_audio[speaker] = AudioSegment.from_file(audio_path)
            
            # Create merged audio
            result_audio = AudioSegment.empty()
            silence = AudioSegment.silent(duration=int(gap * 1000))
            
            for i, segment in enumerate(aligned):
                speaker = segment.dialogue.speaker
                if speaker in speaker_audio:
                    audio = speaker_audio[speaker]
                    # Extract the specific segment with padding for better timing
                    from config import CLIP_PADDING_START_MS, CLIP_PADDING_END_MS
                    
                    start_ms = max(0, int(segment.start_time * 1000) - CLIP_PADDING_START_MS)
                    end_ms = min(len(audio), int(segment.end_time * 1000) + CLIP_PADDING_END_MS)
                    clip = audio[start_ms:end_ms]
                    
                    result_audio += clip
                    if i < len(aligned) - 1:
                        result_audio += silence
            
            # Save to temp file
            temp_dir = tempfile.gettempdir()
            self.preview_audio_path = os.path.join(temp_dir, "pbb_preview.wav")
            result_audio.export(self.preview_audio_path, format='wav')
            
            # Save position if available
            current_playhead = self.timeline_widget.canvas.playhead_time if hasattr(self, 'timeline_widget') else 0
            
            # Set preview widget audio
            self.preview_widget.set_audio(self.preview_audio_path, initial_pos_ms=int(current_playhead * 1000))
            
            # Connect preview position to timeline playhead (only once)
            if not hasattr(self, '_preview_connected') or not self._preview_connected:
                self.preview_widget.media_player.positionChanged.connect(self._on_preview_position_changed)
                self._preview_connected = True
            
            # Set images with proper timestamps synced to audio clips
            if self.image_folder:
                from pathlib import Path as P
                image_folder = P(self.image_folder)
                images = []
                for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                    images.extend(sorted(image_folder.glob(ext)))
                if images:
                    # Calculate timestamps based on clip positions
                    # Get clip start times from timeline
                    audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
                    
                    num_images_to_use = min(len(images), len(audio_clips))
                    image_timestamps = []
                    
                    for i in range(num_images_to_use):
                        image_timestamps.append(audio_clips[i].start)
                    
                    self.preview_widget.set_images(
                        [str(img) for img in images[:num_images_to_use]],
                        timestamps=image_timestamps
                    )
            
            self.statusBar().showMessage("미리보기 준비 완료")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"미리보기 오디오 생성 실패: {str(e)}")
    
    def _on_timeline_playhead_changed(self, time: float):
        """Handle playhead change from timeline - sync to preview"""
        if hasattr(self, 'preview_widget') and self.preview_widget.audio_path:
            # Convert time to milliseconds and seek preview
            position_ms = int(time * 1000)
            self.preview_widget.media_player.setPosition(position_ms)
    
    def _on_preview_position_changed(self, position_ms: int):
        """Handle position change from preview - sync to timeline"""
        if hasattr(self, 'timeline_widget'):
            time_sec = position_ms / 1000.0
            self.timeline_widget.set_playhead(time_sec)
    
    
    def _on_clip_editing(self, clip_id: str):
        """Handle real-time clip boundary change - fast waveform update only"""
        if not self.result_data:
            return
            
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

        # Update waveform only (fast path)
        try:
            from config import CLIP_PADDING_START_MS, CLIP_PADDING_END_MS
            
            # Check cache for speaker audio
            if clip.speaker not in self.speaker_audio_cache:
                speaker_audio_map = self.result_data.get('speaker_audio_map', {})
                audio_path = speaker_audio_map.get(clip.speaker)
                if audio_path:
                    from pydub import AudioSegment
                    self.speaker_audio_cache[clip.speaker] = AudioSegment.from_file(audio_path)
            
            audio = self.speaker_audio_cache.get(clip.speaker)
            if audio:
                # Extract new audio segment
                start_ms = max(0, int(clip.source_start * 1000) - CLIP_PADDING_START_MS)
                end_ms = min(len(audio), int(clip.source_end * 1000) + CLIP_PADDING_END_MS)
                
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
        """Handle final clip boundary edit - sync to result_data and regenerate audio"""
        if not self.result_data:
            return
        
        # Find the edited clip
        clip = None
        for c in self.timeline_widget.canvas.clips:
            if c.id == clip_id:
                clip = c
                break
        
        if not clip:
            return
        
        # Audio-specific source boundary update
        if clip.clip_type == "audio" and clip.segment_index >= 0:
            aligned = self.result_data.get('aligned', [])
            if clip.segment_index < len(aligned):
                segment = aligned[clip.segment_index]
                segment.start_time = clip.source_start
                segment.end_time = clip.source_end
                self.statusBar().showMessage(f"오디오 클립 수정됨: {clip.source_start:.2f}s ~ {clip.source_end:.2f}s")
        else:
            self.statusBar().showMessage(f"클립 수정됨: {clip.name}")
        
        # Sync to preview widget for ALL types
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        
        # Audio regeneration is only strictly necessary for audio clips or if playhead/timing changed
        # But for simplicity, we call it to ensure everything is in sync
        if clip.clip_type == "audio":
            self._regenerate_preview_from_clips()
        else:
            # For non-audio, just updating clips in preview is enough for images/subs
            pass
    
    def _on_clip_moved(self, clip_id: str, new_start: float):
        """Handle clip position change"""
        self.statusBar().showMessage(f"클립 이동됨: {new_start:.2f}s")
        
        # Sync to preview widget
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        
        # Note: Clip position changes only affect timeline playback timing
        # The source audio boundaries remain the same
        # Regenerate preview with new timeline positions
        self._regenerate_preview_from_clips()
    
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
            clip.name = new_text
            # Also update the actual data
            if self.result_data and clip.segment_index >= 0:
                aligned = self.result_data.get('aligned', [])
                if clip.segment_index < len(aligned):
                    aligned[clip.segment_index].dialogue.text = new_text
            
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
            edit_action = menu.addAction("✏️ 텍스트 수정")
            split_action = menu.addAction("✂️ 자막 나누기...")
            menu.addSeparator()
            
            # Find if there's a next subtitle clip
            next_clip = self._find_adjacent_subtitle(clip, direction=1)
            merge_action = None
            if next_clip:
                merge_action = menu.addAction("🔗 다음 자막과 병합")
            
            action = menu.exec(pos)
            
            if action == edit_action:
                self._on_clip_double_clicked(clip_id)
            elif action == split_action:
                self._show_subtitle_editor(clip)
            elif merge_action and action == merge_action:
                self._merge_subtitle_clips(clip, next_clip)
        
        elif clip.clip_type == "image":
            realign_action = menu.addAction("🔄 여기서 다시 정렬")
            menu.addSeparator()
            delete_action = menu.addAction("🗑️ 삭제")
            
            action = menu.exec(pos)
            
            if action == realign_action:
                self._realign_images_from(clip)
            elif action == delete_action:
                self._delete_clip(clip)
    
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
        
        layout.addWidget(QLabel("💡 커서 위치에서 자막이 나눠집니다."))
        
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
        """Split subtitle clip at character position"""
        from core.subtitle_processor import SubtitleProcessor
        
        if char_pos <= 0 or char_pos >= len(clip.name):
            self.statusBar().showMessage("나눌 위치가 올바르지 않습니다.")
            return
        
        processor = SubtitleProcessor()
        split_pos, source_split_time, word_idx = processor.find_best_split_point(
            clip.name, clip.words, char_pos
        )
        
        # Convert source audio time to timeline position
        # source_split_time is in source audio coordinates
        # We need to map it to timeline: clip.start + (source_split_time - first_word_time)
        if source_split_time > 0.0 and clip.words:
            first_word = clip.words[0] if clip.words else None
            first_word_time = first_word.start if hasattr(first_word, 'start') else 0.0
            # Calculate relative position in source and apply to timeline
            relative_time = source_split_time - first_word_time
            split_time = clip.start + relative_time
        else:
            # Fallback: estimate based on character position ratio
            ratio = char_pos / len(clip.name)
            split_time = clip.start + clip.duration * ratio
        
        # Use cursor position directly for text split (user intent)
        # But adjust to keep punctuation with the preceding text
        actual_split_pos = char_pos
        
        # If the character right before cursor is punctuation, include it in first part
        # (already handled by cursor position)
        # If the character at cursor is space, skip it
        while actual_split_pos < len(clip.name) and clip.name[actual_split_pos] == ' ':
            actual_split_pos += 1
        
        # Create two new clips
        text1 = clip.name[:actual_split_pos].strip()
        text2 = clip.name[actual_split_pos:].strip()
        words1 = clip.words[:word_idx + 1] if clip.words else []
        words2 = clip.words[word_idx + 1:] if clip.words else []
        
        # Save original end time before modifying
        original_end = clip.start + clip.duration
        
        # Update original clip
        clip.name = text1
        clip.duration = split_time - clip.start
        clip.words = words1
        
        # Create new clip with remaining duration
        from ui.timeline_widget import TimelineClip
        new_id = f"{clip.id}_split"
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
            segment_index=-1,
            words=words2
        )
        
        self.timeline_widget.canvas.clips.append(new_clip)
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage("자막이 나눠졌습니다.")
    
    def _merge_subtitle_clips(self, clip1, clip2):
        """Merge two adjacent subtitle clips"""
        from core.subtitle_processor import SubtitleProcessor
        
        processor = SubtitleProcessor()
        merged = processor.merge_segments(
            {'text': clip1.name, 'start_time': clip1.start, 
             'end_time': clip1.start + clip1.duration, 'words': clip1.words},
            {'text': clip2.name, 'start_time': clip2.start,
             'end_time': clip2.start + clip2.duration, 'words': clip2.words}
        )
        
        # Update first clip
        clip1.name = merged['text']
        clip1.duration = merged['end_time'] - merged['start_time']
        clip1.words = merged['words']
        
        # Remove second clip
        self.timeline_widget.canvas.clips.remove(clip2)
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage("자막이 병합되었습니다.")
    
    def _auto_format_subtitles(self):
        """Apply automatic formatting to all subtitle clips"""
        from core.subtitle_processor import SubtitleProcessor
        from config import (SUBTITLE_MAX_CHARS_PER_SEGMENT, SUBTITLE_MAX_CHARS_PER_LINE,
                          SUBTITLE_MAX_LINES, SUBTITLE_SPLIT_ON_CONJUNCTIONS)
        from ui.timeline_widget import TimelineClip
        
        processor = SubtitleProcessor(
            max_chars_per_segment=SUBTITLE_MAX_CHARS_PER_SEGMENT,
            max_chars_per_line=SUBTITLE_MAX_CHARS_PER_LINE,
            max_lines=SUBTITLE_MAX_LINES,
            split_on_conjunctions=SUBTITLE_SPLIT_ON_CONJUNCTIONS
        )
        
        # Collect subtitle clips
        subtitle_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "subtitle"]
        
        if not subtitle_clips:
            self.statusBar().showMessage("자막 클립이 없습니다.")
            return
        
        new_clips = []
        existing_non_subtitle_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type != "subtitle"]
        
        split_count = 0
        format_count = 0
        
        for clip in subtitle_clips:
            original_text = clip.name
            
            # 1. Check if needs splitting
            if len(original_text) > SUBTITLE_MAX_CHARS_PER_SEGMENT and clip.words:
                # Split the segment
                segments = processor.split_segment(
                    original_text,
                    clip.start,
                    clip.start + clip.duration,
                    clip.words
                )
                
                for i, seg in enumerate(segments):
                    # Apply line formatting to each segment
                    formatted_text = processor.format_lines(seg['text'])
                    
                    new_clip = TimelineClip(
                        id=f"{clip.id}_fmt_{i}" if i > 0 else clip.id,
                        name=formatted_text,
                        start=seg['start_time'],
                        duration=seg['end_time'] - seg['start_time'],
                        track=clip.track,
                        color=clip.color,
                        clip_type="subtitle",
                        waveform=[],
                        segment_index=clip.segment_index if i == 0 else -1,
                        words=seg['words']
                    )
                    new_clips.append(new_clip)
                
                if len(segments) > 1:
                    split_count += 1
            else:
                # Just apply line formatting
                formatted_text = processor.format_lines(original_text)
                clip.name = formatted_text
                new_clips.append(clip)
                
                if formatted_text != original_text:
                    format_count += 1
        
        # Replace subtitle clips
        self.timeline_widget.canvas.clips = existing_non_subtitle_clips + new_clips
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
        for i, img_clip in enumerate(images_to_realign):
            if i >= len(available_audio):
                break
            
            audio_clip = available_audio[i]
            
            # Find next audio clip's start for duration calculation
            if i + 1 < len(available_audio):
                next_audio_start = available_audio[i + 1].start
            else:
                next_audio_start = audio_clip.start + audio_clip.duration
            
            # Update image clip
            img_clip.start = audio_clip.start
            img_clip.duration = next_audio_start - audio_clip.start
            realigned_count += 1
        
        self.timeline_widget.canvas.update()
        playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
        self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
        self.statusBar().showMessage(f"이미지 {realigned_count}개가 다시 정렬되었습니다.")
    
    def _delete_clip(self, clip):
        """Delete a clip from the timeline"""
        if clip in self.timeline_widget.canvas.clips:
            self.timeline_widget.canvas.clips.remove(clip)
            self.timeline_widget.canvas.update()
            playhead_ms = int(self.timeline_widget.canvas.playhead_time * 1000)
            self.preview_widget.set_timeline_clips(self.timeline_widget.canvas.clips, playhead_ms)
            self.statusBar().showMessage(f"클립이 삭제되었습니다: {clip.name}")
    
    def _regenerate_preview_from_clips(self):
        """Regenerate preview audio based on current clip data"""
        try:
            from pydub import AudioSegment
            import tempfile
            from config import CLIP_PADDING_START_MS, CLIP_PADDING_END_MS
            
            # Use self.speaker_audio_map directly (works for both fresh and loaded projects)
            speaker_audio_map = self.speaker_audio_map or {}

            
            # Load speaker audio files
            speaker_audio: dict[str, AudioSegment] = {}
            for speaker, audio_path in speaker_audio_map.items():
                if audio_path:
                    speaker_audio[speaker] = AudioSegment.from_file(audio_path)
            
            # Get audio clips sorted by start time
            audio_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "audio"]
            audio_clips.sort(key=lambda c: c.start)
            
            if not audio_clips:
                return
            
            # Build merged audio based on clip data
            result_audio = AudioSegment.empty()
            current_pos = 0.0
            
            for clip in audio_clips:
                if clip.speaker not in speaker_audio:
                    continue
                
                audio = speaker_audio[clip.speaker]
                
                # Extract using source boundaries with padding
                start_ms = max(0, int(clip.source_start * 1000) - CLIP_PADDING_START_MS)
                end_ms = min(len(audio), int(clip.source_end * 1000) + CLIP_PADDING_END_MS)
                clip_audio = audio[start_ms:end_ms]
                
                # Add silence gap if needed
                gap_duration = int((clip.start - current_pos) * 1000)
                if gap_duration > 0:
                    result_audio += AudioSegment.silent(duration=gap_duration)
                
                result_audio += clip_audio
                current_pos = clip.start + len(clip_audio) / 1000.0
            
            # Save current playhead position to restore after update
            current_playhead = self.timeline_widget.canvas.playhead_time
            
            # Save and update preview
            temp_dir = tempfile.gettempdir()
            self.preview_audio_path = os.path.join(temp_dir, "pbb_preview.wav")
            result_audio.export(self.preview_audio_path, format='wav')
            
            # Update preview widget
            # Pass current_playhead to set_audio to restore position after loading
            self.preview_widget.set_audio(self.preview_audio_path, initial_pos_ms=int(current_playhead * 1000))
            
            # Also ensure timeline playhead stays in sync (UI side)
            self.timeline_widget.set_playhead(current_playhead)

            
            
            self.statusBar().showMessage("미리보기 오디오 업데이트됨")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"오디오 재생성 실패: {str(e)}")
    
    def _on_gap_changed(self, value: int):
        """Handle gap slider change"""
        ms = value * 10
        self.gap_spinbox.blockSignals(True)
        self.gap_spinbox.setValue(ms)
        self.gap_spinbox.blockSignals(False)
        self.timeline_widget.set_gap(ms / 1000.0)
    
    def _on_gap_spinbox_changed(self, value: int):
        """Handle gap spinbox change"""
        self.gap_slider.blockSignals(True)
        self.gap_slider.setValue(value // 10)
        self.gap_slider.blockSignals(False)
        self.timeline_widget.set_gap(value / 1000.0)
    
    def _on_timeline_playhead_changed(self, time_seconds: float):
        """Handle playhead change from timeline - sync to preview"""
        position_ms = int(time_seconds * 1000)
        self.preview_widget.media_player.setPosition(position_ms)
    
    def _on_preview_position_changed(self, position_ms: int):
        """Handle position change from preview - sync to timeline"""
        from PyQt6.QtMultimedia import QMediaPlayer
        
        time_seconds = position_ms / 1000.0
        # Only auto-scroll when playing
        is_playing = self.preview_widget.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.timeline_widget.set_playhead(time_seconds, auto_scroll=is_playing)
    
    def _export_srt(self):
        """Export SRT file"""
        if not self.result_data or 'aligned' not in self.result_data:
            QMessageBox.warning(self, "오류", "먼저 처리를 완료하세요.")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self, "SRT 저장", "subtitles.srt", "SRT Files (*.srt)"
        )
        if path:
            try:
                from exporters.srt_generator import SRTGenerator
                
                # Get subtitle clips from timeline
                sub_clips = [c for c in self.timeline_widget.canvas.clips if c.clip_type == "subtitle"]
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
        if not self.result_data or 'aligned' not in self.result_data:
            QMessageBox.warning(self, "오류", "먼저 처리를 완료하세요.")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self, "XML 저장", "project.xml", "XML Files (*.xml)"
        )
        if path:
            try:
                from exporters.xml_exporter import XMLExporter, TimelineClip
                
                aligned = self.result_data['aligned']
                speaker_audio_map = self.result_data.get('speaker_audio_map', {})
                gap = self.gap_spinbox.value() / 1000.0
                
                clips = []
                current_time = 0.0
                
                for i, segment in enumerate(aligned):
                    duration = segment.end_time - segment.start_time
                    audio_path = speaker_audio_map.get(segment.dialogue.speaker, "")
                    
                    clip = TimelineClip(
                        name=f"{segment.dialogue.speaker}: {segment.dialogue.text[:20]}",
                        file_path=audio_path,
                        start_time=current_time,
                        end_time=current_time + duration,
                        track=1,
                        clip_type="audio"
                    )
                    clips.append(clip)
                    current_time += duration + gap
                
                exporter = XMLExporter()
                exporter.save(clips, path)
                
                QMessageBox.information(self, "저장 완료", f"XML 파일이 저장되었습니다:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"XML 저장 실패: {str(e)}")
    
    def _render_video(self):
        """Render final video by merging audio clips"""
        if not self.result_data or 'aligned' not in self.result_data:
            QMessageBox.warning(self, "오류", "먼저 처리를 완료하세요.")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self, "영상 저장", "output.mp4", "Video Files (*.mp4);;Audio Files (*.wav)"
        )
        if not path:
            return
        
        # For now, just merge audio (no video/images yet)
        self.statusBar().showMessage("오디오 병합 중...")
        self.btn_render.setEnabled(False)
        
        try:
            from pydub import AudioSegment
            from pathlib import Path as P
            
            aligned = self.result_data['aligned']
            speaker_audio_map = self.result_data.get('speaker_audio_map', {})
            gap = self.gap_spinbox.value() / 1000.0
            
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
                    
                    # Extract the specific segment with padding
                    from config import CLIP_PADDING_START_MS, CLIP_PADDING_END_MS
                    start_ms = max(0, int(clip.source_start * 1000) - CLIP_PADDING_START_MS)
                    end_ms = min(len(audio), int(clip.source_end * 1000) + CLIP_PADDING_END_MS)
                    audio_clip = audio[start_ms:end_ms]
                    
                    result_audio += audio_clip
                    current_pos = clip.start + len(audio_clip) / 1000.0
            
            # Export based on file extension
            if path.endswith('.wav'):
                result_audio.export(path, format='wav')
            else:
                # For mp4, we need images. For now, just export audio
                audio_path = path.replace('.mp4', '.wav')
                result_audio.export(audio_path, format='wav')
                
                # Check if we have images
                if self.image_folder:
                    self._render_with_images(audio_path, path)
                else:
                    QMessageBox.information(
                        self, "오디오 저장 완료", 
                        f"이미지 폴더가 지정되지 않아 오디오만 저장되었습니다:\n{audio_path}\n\n"
                        "영상으로 만들려면 이미지 폴더를 지정하세요."
                    )
                    self.btn_render.setEnabled(True)
                    self.statusBar().showMessage("오디오 저장 완료")
                    return
            
            self.btn_render.setEnabled(True)
            self.statusBar().showMessage("렌더링 완료")
            QMessageBox.information(self, "렌더링 완료", f"파일이 저장되었습니다:\n{path}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.btn_render.setEnabled(True)
            self.statusBar().showMessage("렌더링 오류")
            QMessageBox.critical(self, "오류", f"렌더링 실패: {str(e)}")
    
    def _render_with_images(self, audio_path: str, output_path: str):
        """Render video with images and subtitles using timeline data"""
        try:
            from exporters.video_renderer import VideoRenderer, ImageSegment, SubtitleSegment
            
            # Collect data from timeline
            image_segments = []
            subtitle_segments = []
            
            for clip in self.timeline_widget.canvas.clips:
                if clip.clip_type == "image":
                    image_segments.append(ImageSegment(
                        image_path=clip.image_path,
                        start_time=clip.start,
                        end_time=clip.start + clip.duration
                    ))
                elif clip.clip_type == "subtitle":
                    subtitle_segments.append(SubtitleSegment(
                        text=clip.name,
                        start_time=clip.start,
                        end_time=clip.start + clip.duration
                    ))
            
            # Sort segments
            image_segments.sort(key=lambda x: x.start_time)
            subtitle_segments.sort(key=lambda x: x.start_time)
            
            # Use VideoRenderer
            renderer = VideoRenderer()
            renderer.render(
                images=image_segments,
                audio_path=audio_path,
                subtitles=subtitle_segments,
                output_path=output_path
            )
            
            QMessageBox.information(self, "렌더링 완료", f"영상이 저장되었습니다:\n{output_path}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "오류", f"영상 렌더링 실패: {str(e)}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "오류", f"영상 렌더링 실패: {str(e)}")
        finally:
            self.btn_render.setEnabled(True)
            self.statusBar().showMessage("렌더링 완료")


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
        self.timeline_widget.canvas.clips = []
        self.timeline_widget.canvas.update()
        self.preview_widget.set_timeline_clips([])
        
        # Reset script UI
        self.script_text.setPlainText("")
        self.btn_script.setText("📂 스크립트")
        
        # Reset image UI
        self.btn_image.setText("🖼️ 이미지 폴더")
        self.image_list.clear()
        
        # Reset mapping table
        self.mapping_table.setRowCount(0)
        self.mapping_info.setText("스크립트를 불러오면 화자 목록이 표시됩니다.")
        
        # Reset buttons
        self.btn_process.setEnabled(False)
        self.btn_format_subtitles.setEnabled(False)
        self.btn_export_srt.setEnabled(False)
        self.btn_export_xml.setEnabled(False)
        self.btn_render.setEnabled(False)
        
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
        """Save project with a new filename"""
        from PyQt6.QtWidgets import QFileDialog
        
        path, _ = QFileDialog.getSaveFileName(
            self, "프로젝트 저장", "",
            "PictureBookBuilder Project (*.pbb)"
        )
        
        if path:
            if not path.endswith('.pbb'):
                path += '.pbb'
            self._save_to_file(path)
            self.project_path = path
            self.setWindowTitle(f"PictureBookBuilder - {Path(path).name}")
    
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
                'source_start': clip.source_start,
                'source_end': clip.source_end,
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
            'version': '1.0',
            'saved_at': datetime.now().isoformat(),
            'script_path': self.script_path,
            'script_content': script_content,  # Save script content
            'image_folder': self.image_folder,
            'speaker_audio_map': self.speaker_audio_map,
            'clips': clips_data,
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
                source_start=clip_data.get('source_start', 0.0),
                source_end=clip_data.get('source_end', 0.0),
                segment_index=clip_data.get('segment_index', -1),
                image_path=image_path,
                speaker=clip_data.get('speaker', ''),  # Restore speaker
                words=words
            )
            clips.append(clip)
        
        self.timeline_widget.canvas.clips = clips
        self.timeline_widget.canvas.update()
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
        
        # Restore script label
        if self.script_path and Path(self.script_path).exists():
            self.btn_script.setText(f"📂 {Path(self.script_path).name}")
        else:
            self.btn_script.setText("📂 스크립트")
        
        # Restore image folder and list
        self.image_list.clear()
        print(f"Loading image folder: {self.image_folder}")
        if self.image_folder and Path(self.image_folder).exists():
            self.btn_image.setText(f"🖼️ {Path(self.image_folder).name}")
            
            image_folder = Path(self.image_folder)
            images = []
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                images.extend(sorted(image_folder.glob(ext)))
            
            print(f"  Found {len(images)} images")
            for img_path in images:
                item = QListWidgetItem(f"🖼️ {img_path.name}")
                self.image_list.addItem(item)
        else:
            print(f"  Image folder not found or empty: {self.image_folder}")
            self.btn_image.setText("🖼️ 이미지 폴더")
        
        # Enable buttons if we have clips
        if clips:
            self.btn_format_subtitles.setEnabled(True)
            self.btn_export_srt.setEnabled(True)
            self.btn_export_xml.setEnabled(True)
            self.btn_render.setEnabled(True)
            
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
                    
                    # Extract the segment
                    start_ms = int(clip.source_start * 1000)
                    end_ms = int(clip.source_end * 1000)
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
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

