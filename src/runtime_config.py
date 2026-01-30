"""
Runtime Configuration Module

Manages runtime-configurable settings that can be modified via the Settings UI.
Loads default values from config.py and allows runtime modifications.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional

# Import defaults from config.py
from config import (
    WHISPER_MODEL,
    USE_STABLE_TS,
    USE_INITIAL_PROMPT,
    WHISPER_LANGUAGE,
    USE_QWEN3_FORCED_ALIGNER,
    QWEN3_MAX_AUDIO_SECONDS,
    VAD_PADDING_MS,
    DEFAULT_GAP_SECONDS,
    SUBTITLE_DEFAULTS,
    SUBTITLE_LINE_SOFT_CAP,
    SUBTITLE_LINE_HARD_CAP,
    SUBTITLE_MAX_LINES,
    SUBTITLE_SPLIT_ON_CONJUNCTIONS,
    SUBTITLE_LEAD_TIME_MS,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    RENDER_SUBTITLE_ENABLED,
    RENDER_FONT_FAMILY,
    RENDER_FONT_SIZE,
    RENDER_LINE_SPACING,
    RENDER_FONT_COLOR,
    RENDER_OUTLINE_ENABLED,
    RENDER_OUTLINE_WIDTH,
    RENDER_OUTLINE_COLOR,
    RENDER_BG_ENABLED,
    RENDER_BG_COLOR,
    RENDER_BG_ALPHA,
    RENDER_POSITION,
    RENDER_MARGIN_V,
    RENDER_USE_HW_ACCEL,
)


@dataclass
class RuntimeConfig:
    """
    Runtime configuration that can be modified via Settings UI.
    
    These settings are saved/loaded with the project file.
    """
    # Whisper settings
    whisper_model: str = WHISPER_MODEL
    whisper_language: str = WHISPER_LANGUAGE  # 'ko', 'en', or 'auto'
    use_stable_ts: bool = USE_STABLE_TS
    use_initial_prompt: bool = USE_INITIAL_PROMPT  # 스크립트 기반 initial prompt 사용 여부
    use_qwen3_forced_aligner: bool = USE_QWEN3_FORCED_ALIGNER  # Experimental
    qwen3_max_audio_seconds: float = QWEN3_MAX_AUDIO_SECONDS
    
    # Audio settings
    vad_padding_ms: int = VAD_PADDING_MS
    default_gap_seconds: float = DEFAULT_GAP_SECONDS
    
    # Subtitle settings
    subtitle_auto_params: bool = True  # True: use language-specific defaults, False: use manual values
    subtitle_line_soft_cap: int = SUBTITLE_LINE_SOFT_CAP  # Used when auto_params=False
    subtitle_line_hard_cap: int = SUBTITLE_LINE_HARD_CAP
    subtitle_max_lines: int = SUBTITLE_MAX_LINES
    subtitle_split_on_conjunctions: bool = SUBTITLE_SPLIT_ON_CONJUNCTIONS
    subtitle_lead_time_ms: int = SUBTITLE_LEAD_TIME_MS
    
    # Render Settings (Persisted per project)
    render_width: int = VIDEO_WIDTH
    render_height: int = VIDEO_HEIGHT
    render_fps: int = VIDEO_FPS
    render_subtitle_enabled: bool = RENDER_SUBTITLE_ENABLED
    render_font_family: str = RENDER_FONT_FAMILY
    render_font_size: int = RENDER_FONT_SIZE
    render_line_spacing: float = RENDER_LINE_SPACING
    render_font_color: str = RENDER_FONT_COLOR
    render_outline_enabled: bool = RENDER_OUTLINE_ENABLED
    render_outline_width: int = RENDER_OUTLINE_WIDTH
    render_outline_color: str = RENDER_OUTLINE_COLOR
    render_bg_enabled: bool = RENDER_BG_ENABLED
    render_bg_color: str = RENDER_BG_COLOR
    render_bg_alpha: int = RENDER_BG_ALPHA
    render_position: str = RENDER_POSITION
    render_margin_v: int = RENDER_MARGIN_V
    render_use_hw_accel: bool = RENDER_USE_HW_ACCEL
    
    def get_subtitle_params(self, language: str = 'ko') -> dict:
        """Get subtitle parameters - auto (language-based) or manual.
        
        Args:
            language: 'ko' or 'en'
            
        Returns:
            dict with line_soft_cap, line_hard_cap, max_lines
        """
        if self.subtitle_auto_params:
            return SUBTITLE_DEFAULTS.get(language, SUBTITLE_DEFAULTS['ko'])
        else:
            return {
                'line_soft_cap': self.subtitle_line_soft_cap,
                'line_hard_cap': self.subtitle_line_hard_cap,
                'max_lines': self.subtitle_max_lines,
            }
    
    def to_dict(self) -> dict:
        """Convert to dictionary for project save."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeConfig":
        """Create from dictionary for project load."""
        # Filter only known fields to avoid errors with old/new config versions
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered_data)
    
    def reset_to_defaults(self):
        """Reset all settings to default values from config.py."""
        self.whisper_model = WHISPER_MODEL
        self.whisper_language = WHISPER_LANGUAGE
        self.use_stable_ts = USE_STABLE_TS
        self.use_initial_prompt = USE_INITIAL_PROMPT
        self.use_qwen3_forced_aligner = USE_QWEN3_FORCED_ALIGNER
        self.qwen3_max_audio_seconds = QWEN3_MAX_AUDIO_SECONDS
        self.vad_padding_ms = VAD_PADDING_MS
        self.default_gap_seconds = DEFAULT_GAP_SECONDS
        self.subtitle_auto_params = True
        self.subtitle_line_soft_cap = SUBTITLE_LINE_SOFT_CAP
        self.subtitle_line_hard_cap = SUBTITLE_LINE_HARD_CAP
        self.subtitle_max_lines = SUBTITLE_MAX_LINES
        self.subtitle_split_on_conjunctions = SUBTITLE_SPLIT_ON_CONJUNCTIONS
        self.subtitle_lead_time_ms = SUBTITLE_LEAD_TIME_MS
        
        # Reset Render Settings
        self.render_width = VIDEO_WIDTH
        self.render_height = VIDEO_HEIGHT
        self.render_fps = VIDEO_FPS
        self.render_subtitle_enabled = RENDER_SUBTITLE_ENABLED
        self.render_font_family = RENDER_FONT_FAMILY
        self.render_font_size = RENDER_FONT_SIZE
        self.render_line_spacing = RENDER_LINE_SPACING
        self.render_font_color = RENDER_FONT_COLOR
        self.render_outline_enabled = RENDER_OUTLINE_ENABLED
        self.render_outline_width = RENDER_OUTLINE_WIDTH
        self.render_outline_color = RENDER_OUTLINE_COLOR
        self.render_bg_enabled = RENDER_BG_ENABLED
        self.render_bg_color = RENDER_BG_COLOR
        self.render_bg_alpha = RENDER_BG_ALPHA
        self.render_position = RENDER_POSITION
        self.render_margin_v = RENDER_MARGIN_V
        self.render_use_hw_accel = RENDER_USE_HW_ACCEL


# Global singleton instance
_runtime_config: Optional[RuntimeConfig] = None


def get_config() -> RuntimeConfig:
    """Get the global runtime configuration instance."""
    global _runtime_config
    if _runtime_config is None:
        _runtime_config = RuntimeConfig()
    return _runtime_config


def set_config(config: RuntimeConfig):
    """Set the global runtime configuration instance."""
    global _runtime_config
    _runtime_config = config
