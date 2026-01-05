"""
PictureBookBuilder Configuration
"""
from pathlib import Path

import torch

# Whisper settings
WHISPER_MODEL = "medium" #"large-v3"
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
VAD_PADDING_MS = 100  # Padding for VAD trimming (increased for better margins)
CLIP_PADDING_START_MS = 50  # Extra padding before each clip starts
CLIP_PADDING_END_MS = 150  # Extra padding after each clip ends (captures trailing sounds)

# Subtitle settings
SUBTITLE_MAX_CHARS_PER_LINE = 20
SUBTITLE_MAX_LINES = 2

# Video settings
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
