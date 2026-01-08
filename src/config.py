"""
PictureBookBuilder Configuration
"""
from pathlib import Path

import torch

# Whisper settings
WHISPER_MODEL = "medium" #"large-v3"
USE_STABLE_TS = False  # True: stable-ts (정확한 타이밍), False: faster-whisper (빠른 속도)

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
VAD_PADDING_MS = 150  # Padding for VAD trimming (increased for better margins)

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

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
