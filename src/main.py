"""
PictureBookBuilder - Entry Point
"""
import sys
from pathlib import Path

# Add src to path for running as script
sys.path.insert(0, str(Path(__file__).parent))

# Import torch BEFORE PyQt6 to avoid DLL conflict on Windows.
# PyQt6's Qt DLLs interfere with PyTorch's c10.dll if loaded first.
import torch  # noqa: F401

from PyQt6.QtWidgets import QApplication, QDialog
from ui.main_window import MainWindow, main as original_main
from ui.start_screen import StartScreen
from ui.theme import ModernDarkTheme


def main():
    """Application entry point with start screen"""
    app = QApplication(sys.argv)
    
    # Apply Modern Dark Theme
    ModernDarkTheme.apply(app)
    
    # Show start screen
    start_screen = StartScreen()
    result = start_screen.exec()
    
    if result != QDialog.DialogCode.Accepted:
        # User closed start screen without selecting anything
        sys.exit(0)
    
    # Create main window
    window = MainWindow()
    
    # Open selected project if any
    if start_screen.selected_path:
        window.open_project_file(start_screen.selected_path)
    
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
