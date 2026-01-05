"""
PictureBookBuilder - Entry Point
"""
import sys
from pathlib import Path

# Add src to path for running as script
sys.path.insert(0, str(Path(__file__).parent))

from ui.main_window import main

if __name__ == "__main__":
    main()
