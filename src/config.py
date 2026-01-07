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

# Subtitle settings
SUBTITLE_MAX_CHARS_PER_SEGMENT = 40  # Split segments longer than this
SUBTITLE_MAX_CHARS_PER_LINE = 20     # Line break after this many chars
SUBTITLE_MAX_LINES = 2               # Max lines per subtitle
SUBTITLE_SPLIT_ON_CONJUNCTIONS = True
SUBTITLE_AUTO_SPLIT = True           # Auto-split long segments on processing

# Video settings
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
