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
    
    # Language options
    LANGUAGE_MAP = {
        "한국어": "ko",
        "영어": "en",
        "자동 감지": "auto"
    }
    LANGUAGE_MAP_REV = {v: k for k, v in LANGUAGE_MAP.items()}
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = get_config()
        self._setup_ui()
        self._load_from_config()
        self._install_immediate_tooltips()
    
    def _install_immediate_tooltips(self):
        """Install event filter for immediate tooltips on group boxes."""
        self.processing_group.installEventFilter(self)
        self.subtitle_group.installEventFilter(self)
            
    def eventFilter(self, obj, event):
        """Show tooltips immediately only when hovering the header of group boxes."""
        from PyQt6.QtWidgets import QToolTip, QGroupBox
        from PyQt6.QtGui import QCursor
        from PyQt6.QtCore import QEvent
        
        if event.type() == QEvent.Type.Enter or event.type() == QEvent.Type.MouseMove:
            if isinstance(obj, QGroupBox):
                # Only show if in the top area (header)
                local_pos = obj.mapFromGlobal(QCursor.pos())
                if local_pos.y() < 25:
                    if obj == self.processing_group:
                        QToolTip.showText(QCursor.pos(), "처음 '처리 시작' 버튼을 누를 때 적용됩니다", obj)
                    elif obj == self.subtitle_group:
                        QToolTip.showText(QCursor.pos(), "자막 자동 정리 시 적용됩니다", obj)
                    return True
                else:
                    # Hide if moved out of header but still in box
                    QToolTip.hideText()
        
        # Hide on leave
        if event.type() == QEvent.Type.Leave:
            QToolTip.hideText()
            
        return super().eventFilter(obj, event)
    
    def _setup_ui(self):
        """Setup the settings UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # === Processing Settings (Whisper + Audio) ===
        self.processing_group = QGroupBox("처리 설정")
        # Tooltip handled in eventFilter
        processing_layout = QVBoxLayout(self.processing_group)
        
        # Whisper section
        whisper_layout = QFormLayout()
        whisper_layout.setSpacing(8)
        
        # Model selection
        self.combo_model = QComboBox()
        self.combo_model.addItems(self.WHISPER_MODELS)
        self.combo_model.currentTextChanged.connect(self._on_setting_changed)
        whisper_layout.addRow("Whisper 모델:", self.combo_model)
        
        # Language selection
        self.combo_language = QComboBox()
        self.combo_language.addItems(list(self.LANGUAGE_MAP.keys()))
        self.combo_language.currentTextChanged.connect(self._on_setting_changed)
        whisper_layout.addRow("언어:", self.combo_language)
        
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
        self.spin_vad_padding.valueChanged.connect(self._on_setting_changed)
        audio_layout.addRow("VAD 패딩:", self.spin_vad_padding)
        
        # Gap between clips
        self.spin_gap = QDoubleSpinBox()
        self.spin_gap.setRange(0.0, 2.0)
        self.spin_gap.setSingleStep(0.1)
        self.spin_gap.setSuffix(" 초")
        self.spin_gap.valueChanged.connect(self._on_setting_changed)
        audio_layout.addRow("클립 간격:", self.spin_gap)
        
        processing_layout.addLayout(audio_layout)
        layout.addWidget(self.processing_group)
        
        # === Subtitle Settings ===
        self.subtitle_group = QGroupBox("자막 설정")
        # Tooltip handled in eventFilter
        subtitle_layout = QVBoxLayout(self.subtitle_group)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(8)
        
        # Line Soft Cap
        self.spin_line_soft = QSpinBox()
        self.spin_line_soft.setRange(10, 30)
        self.spin_line_soft.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("줄 목표(Soft):", self.spin_line_soft)
        
        # Line Hard Cap
        self.spin_line_hard = QSpinBox()
        self.spin_line_hard.setRange(15, 40)
        self.spin_line_hard.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("줄 한계(Hard):", self.spin_line_hard)
        
        # Max Lines
        self.spin_max_lines = QSpinBox()
        self.spin_max_lines.setRange(1, 4)
        self.spin_max_lines.valueChanged.connect(self._on_setting_changed)
        form_layout.addRow("최대 줄 수:", self.spin_max_lines)
        
        # Split on conjunctions
        self.check_split_conj = QCheckBox("형태소 분석 사용 (~고, ~며)")
        self.check_split_conj.stateChanged.connect(self._on_setting_changed)
        form_layout.addRow("", self.check_split_conj)
        
        # Auto params (language-specific defaults)
        self.check_auto_params = QCheckBox("언어별 기본값 사용 (자동)")
        self.check_auto_params.setToolTip("체크 시: 한국어/영어 기본값 자동 적용\n해제 시: 위 설정값 사용")
        self.check_auto_params.stateChanged.connect(self._on_auto_params_changed)
        form_layout.addRow("", self.check_auto_params)
        
        subtitle_layout.addLayout(form_layout)
        layout.addWidget(self.subtitle_group)
        
        layout.addStretch()
    
    def _load_from_config(self):
        """Load current config values into UI controls."""
        # Block signals to prevent triggering changes
        self._block_signals(True)
        
        # Whisper
        idx = self.combo_model.findText(self._config.whisper_model)
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)
        
        # Language
        lang_text = self.LANGUAGE_MAP_REV.get(self._config.whisper_language, "한국어")
        self.combo_language.setCurrentText(lang_text)
            
        self.check_stable_ts.setChecked(self._config.use_stable_ts)
        
        # Audio
        self.spin_vad_padding.setValue(self._config.vad_padding_ms)
        self.spin_gap.setValue(self._config.default_gap_seconds)
        
        # Subtitle
        self.spin_line_soft.setValue(self._config.subtitle_line_soft_cap)
        self.spin_line_hard.setValue(self._config.subtitle_line_hard_cap)
        self.spin_max_lines.setValue(self._config.subtitle_max_lines)
        self.check_split_conj.setChecked(self._config.subtitle_split_on_conjunctions)
        self.check_auto_params.setChecked(self._config.subtitle_auto_params)
        
        # Update enabled state based on auto_params
        self._update_manual_controls_enabled()
        
        self._block_signals(False)
    
    def _block_signals(self, block: bool):
        """Block or unblock signals from all controls."""
        controls = [
            self.combo_model, self.combo_language, self.check_stable_ts,
            self.spin_vad_padding, self.spin_gap,
            self.spin_line_soft, self.spin_line_hard, self.spin_max_lines,
            self.check_split_conj, self.check_auto_params
        ]
        for ctrl in controls:
            ctrl.blockSignals(block)
    
    def _on_auto_params_changed(self):
        """Handle auto_params toggle - enable/disable manual controls."""
        self._update_manual_controls_enabled()
        self._on_setting_changed()
    
    def _update_manual_controls_enabled(self):
        """Enable/disable manual subtitle controls based on auto_params."""
        is_manual = not self.check_auto_params.isChecked()
        self.spin_line_soft.setEnabled(is_manual)
        self.spin_line_hard.setEnabled(is_manual)
        self.spin_max_lines.setEnabled(is_manual)
    
    def _on_setting_changed(self):
        """Handle any setting change - update config immediately."""
        self._save_to_config()
        self.settings_changed.emit()
    
    def _save_to_config(self):
        """Save current UI values to config."""
        # Whisper
        self._config.whisper_model = self.combo_model.currentText()
        self._config.whisper_language = self.LANGUAGE_MAP.get(self.combo_language.currentText(), "ko")
        self._config.use_stable_ts = self.check_stable_ts.isChecked()
        
        # Audio
        self._config.vad_padding_ms = self.spin_vad_padding.value()
        self._config.default_gap_seconds = self.spin_gap.value()
        
        # Subtitle
        self._config.subtitle_line_soft_cap = self.spin_line_soft.value()
        self._config.subtitle_line_hard_cap = self.spin_line_hard.value()
        self._config.subtitle_max_lines = self.spin_max_lines.value()
        self._config.subtitle_split_on_conjunctions = self.check_split_conj.isChecked()
        self._config.subtitle_auto_params = self.check_auto_params.isChecked()
    
    def reset_to_defaults(self):
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
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        self.settings_widget = SettingsWidget()
        layout.addWidget(self.settings_widget)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(10, 0, 10, 10)
        
        reset_btn = QPushButton("기본값 복원")
        reset_btn.setToolTip("모든 설정을 기본값으로 초기화")
        reset_btn.clicked.connect(self.settings_widget.reset_to_defaults)
        btn_layout.addWidget(reset_btn)
        
        btn_layout.addStretch()
        
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        close_btn.setFixedWidth(80)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def set_config(self, config: RuntimeConfig):
        """Set config on the embedded widget."""
        self.settings_widget.set_config(config)
