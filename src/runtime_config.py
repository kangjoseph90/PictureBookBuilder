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
    VAD_PADDING_MS,
    DEFAULT_GAP_SECONDS,
    SUBTITLE_DEFAULTS,
    SUBTITLE_LINE_SOFT_CAP,
    SUBTITLE_LINE_HARD_CAP,
    SUBTITLE_MAX_LINES,
    SUBTITLE_SPLIT_ON_CONJUNCTIONS,
)


@dataclass
class RuntimeConfig:
    """
    Runtime configuration that can be modified via Settings UI.
    
    These settings are saved/loaded with the project file.
    """
    # Whisper settings
    whisper_model: str = WHISPER_MODEL
    use_stable_ts: bool = USE_STABLE_TS
    
    # Audio settings
    vad_padding_ms: int = VAD_PADDING_MS
    default_gap_seconds: float = DEFAULT_GAP_SECONDS
    
    # Subtitle settings
    subtitle_auto_params: bool = True  # True: use language-specific defaults, False: use manual values
    subtitle_line_soft_cap: int = SUBTITLE_LINE_SOFT_CAP  # Used when auto_params=False
    subtitle_line_hard_cap: int = SUBTITLE_LINE_HARD_CAP
    subtitle_max_lines: int = SUBTITLE_MAX_LINES
    subtitle_split_on_conjunctions: bool = SUBTITLE_SPLIT_ON_CONJUNCTIONS
    
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
        self.use_stable_ts = USE_STABLE_TS
        self.vad_padding_ms = VAD_PADDING_MS
        self.default_gap_seconds = DEFAULT_GAP_SECONDS
        self.subtitle_auto_params = True
        self.subtitle_line_soft_cap = SUBTITLE_LINE_SOFT_CAP
        self.subtitle_line_hard_cap = SUBTITLE_LINE_HARD_CAP
        self.subtitle_max_lines = SUBTITLE_MAX_LINES
        self.subtitle_split_on_conjunctions = SUBTITLE_SPLIT_ON_CONJUNCTIONS


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
