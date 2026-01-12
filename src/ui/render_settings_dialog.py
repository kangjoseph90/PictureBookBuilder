"""
Render Settings Dialog - UI for configuring video and subtitle rendering settings
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QPushButton,
    QFontComboBox, QColorDialog, QTabWidget, QWidget, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QEvent, QObject
from PyQt6.QtGui import QColor, QFont

from .preview_widget import PreviewWidget
from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS
from runtime_config import get_config
from exporters.video_renderer import SUBTITLE_PADDING_H, SUBTITLE_PADDING_V, SUBTITLE_RADIUS

class RenderSettingsDialog(QDialog):
    """
    Dialog for configuring render settings before video generation.
    Supports resolution, frame rate, and detailed subtitle styling.
    """

    def __init__(self, parent=None, clips=None, speaker_audio_map=None):
        super().__init__(parent)
        self.setWindowTitle("영상 렌더링 설정")
        self.setMinimumSize(900, 700)
        self.setModal(True)

        # Store clips for preview
        self.clips = clips or []
        self.speaker_audio_map = speaker_audio_map or {}
        
        # We need ScheduledClip for audio setup
        from .audio_mixer import ScheduledClip

        # Load settings from persistent runtime config
        config = get_config()
        self.settings = {
            'width': config.render_width,
            'height': config.render_height,
            'fps': config.render_fps,
            'subtitle_enabled': config.render_subtitle_enabled,
            'font_family': config.render_font_family,
            'font_size': config.render_font_size,
            'line_spacing': config.render_line_spacing,
            'font_color': config.render_font_color,
            'outline_enabled': config.render_outline_enabled,
            'outline_width': config.render_outline_width,
            'outline_color': config.render_outline_color,
            'bg_enabled': config.render_bg_enabled,
            'bg_color': config.render_bg_color,
            'bg_alpha': config.render_bg_alpha,
            'position': config.render_position,
            'margin_v': config.render_margin_v,
            'use_hw_accel': config.render_use_hw_accel
        }

        self._setup_ui()
        self._setup_audio()

    def showEvent(self, event):
        super().showEvent(event)
        # Updates preview position after layout is complete
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._update_preview)

    def _setup_ui(self):
        layout = QHBoxLayout(self)

        # --- Left Panel: Settings ---
        settings_panel = QWidget()
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 10, 0)

        # Video Settings Group
        video_group = QGroupBox("비디오 설정")
        video_form = QFormLayout()
        video_form.setSpacing(10)
        video_form.setContentsMargins(10, 10, 10, 10)

        # Resolution
        res_layout = QHBoxLayout()
        res_layout.setSpacing(10)
        
        self.spin_width = QSpinBox()
        self.spin_width.setRange(100, 7680)
        self.spin_width.setValue(self.settings['width'])
        self.spin_width.setSuffix(" px")
        self.spin_width.valueChanged.connect(self._on_setting_changed)

        self.spin_height = QSpinBox()
        self.spin_height.setRange(100, 4320)
        self.spin_height.setValue(self.settings['height'])
        self.spin_height.setSuffix(" px")
        self.spin_height.valueChanged.connect(self._on_setting_changed)

        lbl_x = QLabel("×") # Using multiplication symbol
        lbl_x.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_x.setFixedWidth(20)
        
        res_layout.addWidget(self.spin_width)
        res_layout.addWidget(lbl_x)
        res_layout.addWidget(self.spin_height)
        video_form.addRow("해상도:", res_layout)

        # FPS
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 144)
        self.spin_fps.setValue(self.settings['fps'])
        self.spin_fps.valueChanged.connect(self._on_setting_changed)
        video_form.addRow("프레임 레이트:", self.spin_fps)

        # Hardware Acceleration
        self.chk_hw_accel = QCheckBox("GPU 가속 사용")
        self.chk_hw_accel.setChecked(self.settings['use_hw_accel'])
        self.chk_hw_accel.setToolTip("NVIDIA/Intel/AMD GPU 하드웨어 인코더를 사용합니다. 지원되지 않으면 자동으로 CPU 인코딩으로 전환됩니다.")
        self.chk_hw_accel.toggled.connect(self._on_setting_changed)
        video_form.addRow("", self.chk_hw_accel)

        video_group.setLayout(video_form)
        settings_layout.addWidget(video_group)

        # Subtitle Settings Group
        sub_group = QGroupBox("자막 설정")
        sub_layout = QVBoxLayout()
        sub_layout.setSpacing(10)

        # 1. Master Toggle: Subtitle + Color
        sub_enable_layout = QHBoxLayout()
        self.chk_sub_enable = QCheckBox("자막")
        self.chk_sub_enable.setChecked(self.settings['subtitle_enabled'])
        self.chk_sub_enable.toggled.connect(self._on_sub_enable_toggled)
        
        self.btn_font_color = QPushButton()
        self.btn_font_color.setFixedSize(20, 20)
        self.btn_font_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_font_color.clicked.connect(lambda: self._pick_color('font_color', self.btn_font_color))
        self._set_btn_color(self.btn_font_color, self.settings['font_color'])
        
        sub_enable_layout.addWidget(self.chk_sub_enable)
        sub_enable_layout.addSpacing(4)
        sub_enable_layout.addWidget(self.btn_font_color)
        sub_enable_layout.addStretch()
        sub_layout.addLayout(sub_enable_layout)

        self.sub_settings_widget = QWidget()
        sub_form = QFormLayout(self.sub_settings_widget)
        sub_form.setContentsMargins(0, 0, 0, 0)
        sub_form.setSpacing(10)

        # 2. Outline Toggle + Color + Width
        outline_layout = QHBoxLayout()
        self.chk_outline = QCheckBox("테두리")
        self.chk_outline.setChecked(self.settings['outline_enabled'])
        self.chk_outline.toggled.connect(self._on_outline_toggled)

        self.btn_outline_color = QPushButton()
        self.btn_outline_color.setFixedSize(20, 20)
        self.btn_outline_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_outline_color.clicked.connect(lambda: self._pick_color('outline_color', self.btn_outline_color))
        self._set_btn_color(self.btn_outline_color, self.settings['outline_color'])

        self.spin_outline_width = QSpinBox()
        self.spin_outline_width.setRange(0, 20)
        self.spin_outline_width.setValue(self.settings['outline_width'])
        self.spin_outline_width.valueChanged.connect(self._on_setting_changed)

        outline_layout.addWidget(self.chk_outline)
        outline_layout.addSpacing(4)
        outline_layout.addWidget(self.btn_outline_color)
        outline_layout.addStretch()
        outline_layout.addWidget(QLabel("두께:"))
        outline_layout.addWidget(self.spin_outline_width)
        sub_form.addRow(outline_layout)

        # 3. Background Toggle + Color + Alpha
        bg_layout = QHBoxLayout()
        self.chk_bg = QCheckBox("배경")
        self.chk_bg.setChecked(self.settings['bg_enabled'])
        self.chk_bg.toggled.connect(self._on_bg_toggled)

        self.btn_bg_color = QPushButton()
        self.btn_bg_color.setFixedSize(20, 20)
        self.btn_bg_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bg_color.clicked.connect(lambda: self._pick_color('bg_color', self.btn_bg_color))
        self._set_btn_color(self.btn_bg_color, self.settings['bg_color'])

        self.spin_bg_alpha = QSpinBox()
        self.spin_bg_alpha.setRange(0, 255)
        self.spin_bg_alpha.setValue(self.settings['bg_alpha'])
        self.spin_bg_alpha.setToolTip("투명도 (0-255)")
        self.spin_bg_alpha.valueChanged.connect(self._on_setting_changed)

        bg_layout.addWidget(self.chk_bg)
        bg_layout.addSpacing(4)
        bg_layout.addWidget(self.btn_bg_color)
        bg_layout.addStretch()
        bg_layout.addWidget(QLabel("투명도:"))
        bg_layout.addWidget(self.spin_bg_alpha)
        sub_form.addRow(bg_layout)
        
        # 4. Font Property
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.settings['font_family']))
        self.font_combo.currentFontChanged.connect(self._on_setting_changed)
        sub_form.addRow("폰트:", self.font_combo)

        # 5. Size Property
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(8, 200)
        self.spin_font_size.setValue(self.settings['font_size'])
        self.spin_font_size.valueChanged.connect(self._on_setting_changed)
        sub_form.addRow("크기:", self.spin_font_size)

        # 5.1 Line Spacing
        self.spin_line_spacing = QDoubleSpinBox()
        self.spin_line_spacing.setRange(0.5, 3.0)
        self.spin_line_spacing.setSingleStep(0.1)
        self.spin_line_spacing.setValue(self.settings['line_spacing'])
        self.spin_line_spacing.valueChanged.connect(self._on_setting_changed)
        sub_form.addRow("줄간격:", self.spin_line_spacing)

        # 6. Position
        self.combo_position = QComboBox()
        self.combo_position.addItems(["Bottom", "Top", "Center"])
        self.combo_position.currentTextChanged.connect(self._on_setting_changed)
        sub_form.addRow("위치:", self.combo_position)

        # 7. Margin
        self.spin_margin_v = QSpinBox()
        self.spin_margin_v.setRange(0, 500)
        self.spin_margin_v.setValue(self.settings['margin_v'])
        self.spin_margin_v.valueChanged.connect(self._on_setting_changed)
        sub_form.addRow("여백:", self.spin_margin_v)

        sub_layout.addWidget(self.sub_settings_widget)
        sub_group.setLayout(sub_layout)
        settings_layout.addWidget(sub_group)

        # Default Reset Button
        btn_reset = QPushButton("기본값 복원")
        btn_reset.setStyleSheet("height: 32px;")
        btn_reset.clicked.connect(self._reset_to_defaults)
        settings_layout.addWidget(btn_reset)

        settings_layout.addStretch()

        # Render Buttons
        btn_layout = QHBoxLayout()
        common_btn_style = "height: 32px; font-weight: bold; border-radius: 4px;"
        
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setStyleSheet(common_btn_style + "background-color: #555; color: white;")
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_render = QPushButton("렌더링 시작")
        self.btn_render.setStyleSheet(common_btn_style + "background-color: #4CAF50; color: white;")
        self.btn_render.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_render)
        settings_layout.addLayout(btn_layout)

        settings_panel.setFixedWidth(350)

        # --- Right Panel: Preview ---
        preview_panel = QGroupBox("미리보기")
        preview_layout = QVBoxLayout(preview_panel)

        self.preview_widget = PreviewWidget()
        # Install event filter to handle resize updates
        self.preview_widget.image_label.installEventFilter(self)
        
        # Initial load of timeline clips
        self.preview_widget.set_timeline_clips(self.clips)

        preview_layout.addWidget(self.preview_widget)

        layout.addWidget(settings_panel)
        layout.addWidget(preview_panel)

    def _on_sub_enable_toggled(self, checked):
        self.sub_settings_widget.setEnabled(checked)
        self._on_setting_changed()

    def _set_btn_color(self, btn, color_hex):
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color_hex};
                border: 1px solid #555;
                border-radius: 10px;
                padding: 0px;
                margin: 0px;
                min-width: 20px;
                min-height: 20px;
                max-width: 20px;
                max-height: 20px;
            }}
        """)

    def _pick_color(self, key, btn):
        current = QColor(self.settings[key])
        color = QColorDialog.getColor(current, self, "색상 선택")
        if color.isValid():
            self.settings[key] = color.name()
            self._set_btn_color(btn, color.name())
            self._on_setting_changed()

    def accept(self):
        """Save settings to RuntimeConfig and accept dialog"""
        config = get_config()
        config.render_width = self.settings['width']
        config.render_height = self.settings['height']
        config.render_fps = self.settings['fps']
        config.render_subtitle_enabled = self.settings['subtitle_enabled']
        config.render_font_family = self.settings['font_family']
        config.render_font_size = self.settings['font_size']
        config.render_line_spacing = self.settings['line_spacing']
        config.render_font_color = self.settings['font_color']
        config.render_outline_enabled = self.settings['outline_enabled']
        config.render_outline_width = self.settings['outline_width']
        config.render_outline_color = self.settings['outline_color']
        config.render_bg_enabled = self.settings['bg_enabled']
        config.render_bg_color = self.settings['bg_color']
        config.render_bg_alpha = self.settings['bg_alpha']
        config.render_position = self.settings['position']
        config.render_margin_v = self.settings['margin_v']
        config.render_use_hw_accel = self.settings['use_hw_accel']
        
        super().accept()

    def done(self, r):
        """Handle dialog close - cleanup resources"""
        if hasattr(self, 'preview_widget') and self.preview_widget:
            self.preview_widget.cleanup()
        super().done(r)

    def _on_outline_toggled(self, checked):
        if checked and self.chk_bg.isChecked():
             self.chk_bg.setChecked(False)
        self._on_setting_changed()

    def _on_bg_toggled(self, checked):
        if checked and self.chk_outline.isChecked():
            self.chk_outline.setChecked(False)
        self._on_setting_changed()

    def _on_setting_changed(self):
        # Update settings dict
        self.settings['width'] = self.spin_width.value()
        self.settings['height'] = self.spin_height.value()
        self.settings['fps'] = self.spin_fps.value()
        self.settings['subtitle_enabled'] = self.chk_sub_enable.isChecked()
        self.settings['font_family'] = self.font_combo.currentFont().family()
        self.settings['font_size'] = self.spin_font_size.value()
        self.settings['line_spacing'] = self.spin_line_spacing.value()
        self.settings['outline_enabled'] = self.chk_outline.isChecked()
        self.settings['outline_width'] = self.spin_outline_width.value()
        self.settings['bg_enabled'] = self.chk_bg.isChecked()
        self.settings['bg_alpha'] = self.spin_bg_alpha.value()
        self.settings['position'] = self.combo_position.currentText()
        self.settings['margin_v'] = self.spin_margin_v.value()
        self.settings['use_hw_accel'] = self.chk_hw_accel.isChecked()

        self._update_preview()

    def eventFilter(self, obj, event):
        if obj == self.preview_widget.image_label and event.type() == QEvent.Type.Resize:
            self._update_preview()
        return super().eventFilter(obj, event)

    def _update_preview(self):
        """Update the preview widget styling based on current settings"""
        if not self.settings['subtitle_enabled']:
            self.preview_widget.subtitles_enabled = False
            self.preview_widget.subtitle_label.hide()
            return

        self.preview_widget.subtitles_enabled = True

        # Calculate scale factor based on render resolution vs preview size
        render_w, render_h = self.settings['width'] or 1920, self.settings['height'] or 1080
        preview_size = self.preview_widget.image_label.size()
        scale = min(preview_size.width() / render_w, preview_size.height() / render_h)

        # Scale font size proportionally to preview size
        scaled_font_size = max(8, int(self.settings['font_size'] * scale))
        
        # Scale padding and radius for preview matching
        scaled_pad_h = int(SUBTITLE_PADDING_H * scale)
        scaled_pad_v = int(SUBTITLE_PADDING_V * scale)
        scaled_radius = int(SUBTITLE_RADIUS * scale)

        # Note: color is set via set_text_color for StrokedLabel support
        style = f"""
            QLabel {{
                font-family: "{self.settings['font_family']}";
                font-size: {scaled_font_size}px;
                padding: {scaled_pad_v}px {scaled_pad_h}px;
                border-radius: {scaled_radius}px;
                border: none;
            }}
        """

        # Background
        if self.settings['bg_enabled']:
            c = QColor(self.settings['bg_color'])
            style += f"QLabel {{ background-color: rgba({c.red()}, {c.green()}, {c.blue()}, {self.settings['bg_alpha']}); }}"
        else:
            style += "QLabel { background-color: transparent; }"

        # Set style sheet for font and background
        self.preview_widget.subtitle_label.setStyleSheet(style)

        # Set Text Color directly
        self.preview_widget.subtitle_label.set_text_color(self.settings['font_color'])

        # Set Line Spacing directly
        self.preview_widget.subtitle_label.set_line_spacing(self.settings['line_spacing'])

        # Set Outline directly
        if self.settings['outline_enabled'] and self.settings['outline_width'] > 0:
             # Scale outline width
             scaled_outline = max(1, self.settings['outline_width'] * scale)
             self.preview_widget.subtitle_label.set_outline(scaled_outline, self.settings['outline_color'])
        else:
             self.preview_widget.subtitle_label.set_outline(0, None)

        self._apply_preview_position()

        # Force subtitle to show if there is one at current position
        current_pos = self.preview_widget.media_player.position()
        
        # Force refresh by resetting state tracker
        self.preview_widget.current_subtitle = None
        self.preview_widget._update_preview_content(current_pos)

    def _apply_preview_position(self):
        label = self.preview_widget.subtitle_label
        container = self.preview_widget.image_label

        if not label.isVisible():
            return

        pos_setting = self.settings['position']
        margin_v_setting = self.settings['margin_v']
        render_w = self.settings['width']
        render_h = self.settings['height']
        if render_w <= 0: render_w = 1920
        if render_h <= 0: render_h = 1080

        def custom_reposition():
            if not label.isVisible(): return

            img_w = container.width()
            img_h = container.height()

            # Calculate content rect
            scale = min(img_w / render_w, img_h / render_h)
            content_w = render_w * scale
            content_h = render_h * scale
            
            # Offsets (centering)
            off_x = (img_w - content_w) / 2
            off_y = (img_h - content_h) / 2

            # Limit subtitle width to content width
            max_w = int(content_w * 0.9)
            label.setMaximumWidth(max_w)
            label.adjustSize()

            sub_w = label.width()
            sub_h = label.height()

            # Scaled margin and padding relative to CONTENT
            margin_v = margin_v_setting * scale
            # Use the exact integer padding value to match the stylesheet
            pad_v = int(SUBTITLE_PADDING_V * scale)

            # X is always centered on content
            x = off_x + (content_w - sub_w) / 2

            if pos_setting == "Bottom":
                # Start from bottom of TEXT anchor, go up by margin
                # Widget bottom = y + sub_h
                # Text bottom = Widget bottom - pad_v = y + sub_h - pad_v
                # We want Text bottom to be at (content_h - margin_v)
                # So: y + sub_h - pad_v = content_h - margin_v
                # => y = content_h - margin_v - sub_h + pad_v
                y = off_y + content_h - margin_v - sub_h + pad_v
                
            elif pos_setting == "Top":
                # Start from top of TEXT anchor
                # Widget top = y
                # Text top = Widget top + pad_v = y + pad_v
                # We want Text top to be at margin_v
                # So: y + pad_v = margin_v
                # => y = margin_v - pad_v
                y = off_y + margin_v - pad_v
                
            else: # Center
                # Center of text block
                # Widget center = y + sub_h / 2
                # Text center = (Widget center) (assuming symmetrical padding)
                # We want Text center to be at content_h / 2
                y = off_y + (content_h - sub_h) / 2

            label.move(int(x), int(y))

        self.preview_widget._reposition_subtitle = custom_reposition
        custom_reposition()

    def _reset_to_defaults(self):
        # Reset runtime config to defaults
        config = get_config()
        config.reset_to_defaults()
        
        # Reload settings dict from config
        self.settings.update({
            'width': config.render_width,
            'height': config.render_height,
            'fps': config.render_fps,
            'subtitle_enabled': config.render_subtitle_enabled,
            'font_family': config.render_font_family,
            'font_size': config.render_font_size,
            'line_spacing': config.render_line_spacing,
            'font_color': config.render_font_color,
            'outline_enabled': config.render_outline_enabled,
            'outline_width': config.render_outline_width,
            'outline_color': config.render_outline_color,
            'bg_enabled': config.render_bg_enabled,
            'bg_color': config.render_bg_color,
            'bg_alpha': config.render_bg_alpha,
            'position': config.render_position,
            'margin_v': config.render_margin_v,
            'use_hw_accel': config.render_use_hw_accel
        })

        # Update UI components
        self.spin_width.setValue(self.settings['width'])
        self.spin_height.setValue(self.settings['height'])
        self.spin_fps.setValue(self.settings['fps'])
        self.chk_sub_enable.setChecked(self.settings['subtitle_enabled'])
        self.font_combo.setCurrentFont(QFont(self.settings['font_family']))
        self.spin_font_size.setValue(self.settings['font_size'])
        self.spin_line_spacing.setValue(self.settings['line_spacing'])
        self.chk_outline.setChecked(self.settings['outline_enabled'])
        self.spin_outline_width.setValue(self.settings['outline_width'])
        self.chk_bg.setChecked(self.settings['bg_enabled'])
        self.spin_bg_alpha.setValue(self.settings['bg_alpha'])
        self.combo_position.setCurrentText(self.settings['position'])
        self.spin_margin_v.setValue(self.settings['margin_v'])

        self._set_btn_color(self.btn_font_color, self.settings['font_color'])
        self._set_btn_color(self.btn_outline_color, self.settings['outline_color'])
        self._set_btn_color(self.btn_bg_color, self.settings['bg_color'])
        self.chk_hw_accel.setChecked(self.settings['use_hw_accel'])

        self._on_setting_changed()

    def get_settings(self):
        return self.settings

    def _setup_audio(self):
        """Setup audio clips for preview widget"""
        from .audio_mixer import ScheduledClip
        
        audio_clips = []
        for clip in self.clips:
            if hasattr(clip, 'clip_type') and clip.clip_type == "audio":
                # Use clip.speaker attribute or parsing
                speaker = getattr(clip, 'speaker', None)
                if not speaker and ":" in clip.name:
                    speaker = clip.name.split(":")[0].strip()
                
                if speaker:
                    # Get source path
                    source_path = self.speaker_audio_map.get(speaker, "")
                    
                    sc = ScheduledClip(
                        clip_id=getattr(clip, 'id', str(id(clip))),
                        speaker=speaker,
                        timeline_start=clip.start,
                        timeline_end=clip.start + clip.duration,
                        source_offset=getattr(clip, 'offset', 0.0),
                        source_path=source_path,
                        duration=clip.duration
                    )
                    audio_clips.append(sc)
        
        # Prepare valid speaker map
        valid_map = {k: str(v) for k, v in self.speaker_audio_map.items() if v}
        
        self.preview_widget.set_audio_clips(audio_clips, valid_map)
        
        # Calculate total duration from all clips (audio, image, subtitle)
        # This mirrors the logic in VideoRenderer to ensure consistency
        max_duration = 0.0
        
        # 1. Check audio clips
        for clip in audio_clips:
            # clip is ScheduledClip(duration=..., timeline_end=...)
            max_duration = max(max_duration, clip.timeline_end)
            
        # 2. Check other clips (image, subtitle) from self.clips
        for clip in self.clips:
            if hasattr(clip, 'duration') and hasattr(clip, 'start'):
                end_time = clip.start + clip.duration
                max_duration = max(max_duration, end_time)
        
        # Update preview widget duration
        if max_duration > 0:
            self.preview_widget.set_total_duration(max_duration)
