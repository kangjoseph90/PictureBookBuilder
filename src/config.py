"""
PictureBookBuilder Configuration
"""
from pathlib import Path

import torch

# Whisper settings
WHISPER_MODEL = "medium" #"large-v3"
USE_STABLE_TS = True  # True: stable-ts (정확한 타이밍), False: faster-whisper (빠른 속도)
USE_INITIAL_PROMPT = True  # True: 스크립트 기반 initial prompt 사용, False: prompt 없이 인식
WHISPER_LANGUAGE = "auto"  # 'ko', 'en', or 'auto'

if torch.cuda.is_available():
    WHISPER_DEVICE = "cuda"
    WHISPER_COMPUTE_TYPE = "float16"
    print(f"Using GPU: {torch.cuda.get_device_name(0)}")
else:
    WHISPER_DEVICE = "cpu"
    WHISPER_COMPUTE_TYPE = "int8"
    print("Using CPU")

# Audio settings
DEFAULT_GAP_SECONDS = 0.5  # Gap between clips
VAD_PADDING_MS = 200  # Padding for VAD trimming (increased for better margins)

# Subtitle settings - Language-specific defaults
SUBTITLE_DEFAULTS = {
    'ko': {'line_soft_cap': 18, 'line_hard_cap': 25, 'max_lines': 2},
    'en': {'line_soft_cap': 35, 'line_hard_cap': 42, 'max_lines': 2},
}
# Fallback for manual mode or unknown language
SUBTITLE_LINE_SOFT_CAP = 18
SUBTITLE_LINE_HARD_CAP = 25
SUBTITLE_MAX_LINES = 2
SUBTITLE_SPLIT_ON_CONJUNCTIONS = True

# Video settings
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
RENDER_USE_HW_ACCEL = True  # GPU 가속 사용 (NVIDIA/Intel/AMD)

# Render Default Styles
RENDER_SUBTITLE_ENABLED = True
RENDER_FONT_FAMILY = "Malgun Gothic"
RENDER_FONT_SIZE = 60
RENDER_LINE_SPACING = 1.4
RENDER_FONT_COLOR = "#FFFFFF"
RENDER_OUTLINE_ENABLED = True
RENDER_OUTLINE_WIDTH = 4
RENDER_OUTLINE_COLOR = "#000000"
RENDER_BG_ENABLED = False
RENDER_BG_COLOR = "#000000"
RENDER_BG_ALPHA = 160
RENDER_POSITION = "Bottom"
RENDER_MARGIN_V = 100

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
