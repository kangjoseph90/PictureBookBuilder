"""
Settings Widget - UI for configuring runtime settings
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QLabel, QDialog
)
from PyQt6.QtCore import pyqtSignal, Qt

from runtime_config import RuntimeConfig, get_config


class SettingsWidget(QWidget):
    """
    Widget for editing runtime configuration settings.
    
    Provides UI controls for all configurable parameters.
    """
    
    # Emitted when any setting is changed
    settings_changed = pyqtSignal()
    
    # Available Whisper models
    WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = get_config()
        self._setup_ui()
        self._load_from_config()
    
    def _setup_ui(self):
        """Setup the settings UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        
        # === Processing Settings (Whisper + Audio) ===
        processing_group = QGroupBox("🔧 처리 설정")
        processing_group.setToolTip("처음 '처리 시작' 버튼을 누를 때 적용됩니다")
        processing_layout = QVBoxLayout(processing_group)
        
        # Info label
        processing_info = QLabel("▶️ 처리 시작 시 적용")
        processing_info.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        processing_layout.addWidget(processing_info)
        
        # Whisper section
        whisper_layout = QFormLayout()
        whisper_layout.setSpacing(8)
        
        # Model selection
        self.combo_model = QComboBox()
        self.combo_model.addItems(self.WHISPER_MODELS)
        self.combo_model.setToolTip("모델 크기: tiny(빠름) → large(정확)")
        self.combo_model.currentTextChanged.connect(self._on_setting_changed)
        whisper_layout.addRow("Whisper 모델:", self.combo_model)
        
        # Stable-TS checkbox
        self.check_stable_ts = QCheckBox("Stable-TS 사용 (정확한 타이밍, 느림)")
        self.check_stable_ts.stateChanged.connect(self._on_setting_changed)
        whisper_layout.addRow("", self.check_stable_ts)
        
        processing_layout.addLayout(whisper_layout)
        
        # Separator
        sep1 = QLabel("")
        sep1.setFixedHeight(5)
        processing_layout.addWidget(sep1)
        
        # Audio section
        audio_layout = QFormLayout()
        audio_layout.setSpacing(8)
        
        # VAD padding
        self.spin_vad_padding = QSpinBox()
        self.spin_vad_padding.setRange(0, 500)
        self.spin_vad_padding.setSuffix(" ms")
        self.spin_vad_padding.setToolTip("음성 경계 패딩 (VAD 트리밍)")
        self.spin_vad_padding.valueChanged.connect(self._on_setting_changed)
        audio_layout.addRow("VAD 패딩:", self.spin_vad_padding)
        
        # Gap between clips
        self.spin_gap = QDoubleSpinBox()
        self.spin_gap.setRange(0.0, 2.0)
        self.spin_gap.setSingleStep(0.1)
        self.spin_gap.setSuffix(" 초")
        self.spin_gap.setToolTip("클립 간 기본 간격")
        self.spin_gap.valueChanged.connect(self._on_setting_changed)
        audio_layout.addRow("클립 간격:", self.spin_gap)
        
        processing_layout.addLayout(audio_layout)
        layout.addWidget(processing_group)
        
        # === Subtitle Settings ===
        subtitle_group = QGroupBox("📝 자막 설정")
        subtitle_group.setToolTip("자막 자동 정리 시 적용됩니다")
        subtitle_layout = QVBoxLayout(subtitle_group)
        
        # Info label
        subtitle_info = QLabel("🔧 자막 자동 정리 시 적용")
        subtitle_info.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        subtitle_layout.addWidget(subtitle_info)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(8)
        
        # Max chars per segment
        self.spin_segment_chars = QSpinBox()
        self.spin_segment_chars.setRange(20, 100)
        self.spin_segment_chars.setToolTip("세그먼트 최대 글자수 (초과 시 분할)")
        self.spin_segment_chars.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("세그먼트 최대:", self.spin_segment_chars)
        
        # Max chars per line
        self.spin_line_chars = QSpinBox()
        self.spin_line_chars.setRange(10, 50)
        self.spin_line_chars.setToolTip("라인당 최대 글자수 (초과 시 줄바꿈)")
        self.spin_line_chars.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("라인 최대:", self.spin_line_chars)
        
        # Max lines
        self.spin_max_lines = QSpinBox()
        self.spin_max_lines.setRange(1, 4)
        self.spin_max_lines.setToolTip("자막당 최대 줄 수")
        self.spin_max_lines.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("최대 줄 수:", self.spin_max_lines)
        
        # Split on conjunctions
        self.check_split_conj = QCheckBox("접속사 기준 분할 (~고, ~며)")
        self.check_split_conj.stateChanged.connect(self._on_setting_changed)
        form_layout.addRow("", self.check_split_conj)
        
        # Auto split
        self.check_auto_split = QCheckBox("긴 자막 자동 분할")
        self.check_auto_split.stateChanged.connect(self._on_setting_changed)
        form_layout.addRow("", self.check_auto_split)
        
        subtitle_layout.addLayout(form_layout)
        layout.addWidget(subtitle_group)
        
        # === Reset Button ===
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_reset = QPushButton("기본값 복원")
        self.btn_reset.setToolTip("모든 설정을 기본값으로 초기화")
        self.btn_reset.clicked.connect(self._reset_to_defaults)
        btn_layout.addWidget(self.btn_reset)
        
        layout.addLayout(btn_layout)
    
    def _load_from_config(self):
        """Load current config values into UI controls."""
        # Block signals to prevent triggering changes
        self._block_signals(True)
        
        # Whisper
        idx = self.combo_model.findText(self._config.whisper_model)
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)
        self.check_stable_ts.setChecked(self._config.use_stable_ts)
        
        # Audio
        self.spin_vad_padding.setValue(self._config.vad_padding_ms)
        self.spin_gap.setValue(self._config.default_gap_seconds)
        
        # Subtitle
        self.spin_segment_chars.setValue(self._config.subtitle_max_chars_per_segment)
        self.spin_line_chars.setValue(self._config.subtitle_max_chars_per_line)
        self.spin_max_lines.setValue(self._config.subtitle_max_lines)
        self.check_split_conj.setChecked(self._config.subtitle_split_on_conjunctions)
        self.check_auto_split.setChecked(self._config.subtitle_auto_split)
        
        self._block_signals(False)
    
    def _block_signals(self, block: bool):
        """Block or unblock signals from all controls."""
        controls = [
            self.combo_model, self.check_stable_ts,
            self.spin_vad_padding, self.spin_gap,
            self.spin_segment_chars, self.spin_line_chars, self.spin_max_lines,
            self.check_split_conj, self.check_auto_split
        ]
        for ctrl in controls:
            ctrl.blockSignals(block)
    
    def _on_setting_changed(self):
        """Handle any setting change - update config immediately."""
        self._save_to_config()
        self.settings_changed.emit()
    
    def _save_to_config(self):
        """Save current UI values to config."""
        # Whisper
        self._config.whisper_model = self.combo_model.currentText()
        self._config.use_stable_ts = self.check_stable_ts.isChecked()
        
        # Audio
        self._config.vad_padding_ms = self.spin_vad_padding.value()
        self._config.default_gap_seconds = self.spin_gap.value()
        
        # Subtitle
        self._config.subtitle_max_chars_per_segment = self.spin_segment_chars.value()
        self._config.subtitle_max_chars_per_line = self.spin_line_chars.value()
        self._config.subtitle_max_lines = self.spin_max_lines.value()
        self._config.subtitle_split_on_conjunctions = self.check_split_conj.isChecked()
        self._config.subtitle_auto_split = self.check_auto_split.isChecked()
    
    def _reset_to_defaults(self):
        """Reset all settings to defaults."""
        self._config.reset_to_defaults()
        self._load_from_config()
        self.settings_changed.emit()
    
    def set_config(self, config: RuntimeConfig):
        """Set a new config instance (e.g., after project load)."""
        self._config = config
        self._load_from_config()


class SettingsDialog(QDialog):
    """Dialog wrapper for SettingsWidget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(350)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.settings_widget = SettingsWidget()
        layout.addWidget(self.settings_widget)
        
        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(10, 0, 10, 10)
        btn_layout.addStretch()
        
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def set_config(self, config: RuntimeConfig):
        """Set config on the embedded widget."""
        self.settings_widget.set_config(config)
