import sys
import os
import tempfile
import shutil
import time
import unittest
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QApplication
from src.ui.image_cache import ImageCache

# Need app for QPixmap
if not QApplication.instance():
    app = QApplication(sys.argv)
else:
    app = QApplication.instance()

class TestImageCache(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cache = None

    def tearDown(self):
        if self.cache:
            self.cache.cleanup()
        shutil.rmtree(self.tmp_dir)

    def create_dummy_image(self, path):
        img = QImage(100, 100, QImage.Format.Format_RGB32)
        img.fill(QColor("red"))
        img.save(path)

    def test_preview_cache_limit(self):
        """Verify that preview thumbnails are evicted when capacity is reached"""
        limit = 30
        self.cache = ImageCache(capacity=10, capacity_preview=limit)

        # Create 50 dummy images
        paths = []
        for i in range(50):
            p = os.path.join(self.tmp_dir, f"img_{i}.jpg")
            self.create_dummy_image(p)
            paths.append(p)

        # Load images
        self.cache.prefetch_images(paths)

        # Wait for loading to complete
        start = time.time()
        while True:
            app.processEvents()
            time.sleep(0.01)
            # If we processed all files (check pending)
            if not self.cache._pending:
                app.processEvents()
                break
            if time.time() - start > 10:
                self.fail("Timeout waiting for images")

        count = len(self.cache._thumbnails_preview)

        # Check that we are within the limit
        self.assertLessEqual(count, limit, f"Cache size {count} exceeds limit {limit}")
        self.assertEqual(count, limit, f"Cache size {count} should be equal to limit {limit} given we loaded more")

        # Verify LRU behavior (optional but good): the last images loaded should be present
        # Since prefetch_images launches threads, order isn't strictly guaranteed,
        # but generally the later ones should be there.
        # We won't assert strict LRU here due to threading indeterminism in test,
        # but the size bound is the critical part.

if __name__ == "__main__":
    unittest.main()
