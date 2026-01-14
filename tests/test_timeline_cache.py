import sys
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPainter, QImage, QColor
from PyQt6.QtCore import Qt
from src.ui.timeline_widget import TimelineCanvas, TimelineClip

# Need app for GUI widgets
if not QApplication.instance():
    app = QApplication(sys.argv)
else:
    app = QApplication.instance()

class TestWaveformCacheLeak(unittest.TestCase):
    def test_cache_growth(self):
        canvas = TimelineCanvas()

        # Override capacity for testing to make it faster
        canvas._waveform_cache_capacity = 100

        # Create a dummy audio clip with some waveform data
        clip = TimelineClip(
            id="clip1",
            name="Test Clip",
            start=0.0,
            duration=10.0,
            track=0,
            color=QColor("blue"),
            clip_type="audio",
            waveform=[0.1, 0.5, 0.8, 0.3, 0.2] * 20 # 100 samples
        )
        canvas.clips = [clip]

        # Simulate rendering at many different widths (zooming)

        img = QImage(1000, 100, QImage.Format.Format_ARGB32)
        painter = QPainter(img)

        try:
            # Simulate 2000 different zoom levels/widths
            for width in range(100, 2100):
                canvas._draw_waveform(
                    painter,
                    clip,
                    x=0,
                    y=0,
                    width=float(width),
                    height=50
                )
        finally:
            painter.end()

        final_size = len(canvas._waveform_path_cache)
        print(f"Final cache size: {final_size}")

        # It should be bounded to capacity
        self.assertLessEqual(final_size, canvas._waveform_cache_capacity, "Cache exceeded capacity")
        self.assertEqual(final_size, canvas._waveform_cache_capacity, "Cache should be full")

if __name__ == "__main__":
    unittest.main()
