"""
Image Cache - Simplified single-source cache

Loads original images once and generates thumbnails upfront.
All components share the same cache.
"""
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import threading

from PyQt6.QtCore import QObject, pyqtSignal, QSize, Qt
from PyQt6.QtGui import QPixmap, QImage


# Standard thumbnail sizes
THUMBNAIL_SIZE_SMALL = QSize(48, 48)   # For image list
THUMBNAIL_SIZE_TIMELINE = QSize(120, 60)  # For timeline


class ImageCache(QObject):
    """
    Single-source image cache.
    
    Stores:
    - Original pixmaps (for preview)
    - Pre-generated thumbnails (for list and timeline)
    
    All loading happens in background threads.
    """
    
    # Signal when an image is fully loaded (path)
    image_loaded = pyqtSignal(str)
    
    # Internal signal to transfer data from thread to main thread
    # args: path, original_qimage, small_qimage, timeline_qimage
    _image_processed = pyqtSignal(str, QImage, QImage, QImage)
    
    def __init__(self, max_workers: int = 4):
        super().__init__()
        self._originals: dict[str, QPixmap] = {}  # path -> original pixmap
        self._thumbnails_small: dict[str, QPixmap] = {}  # path -> 48x48 thumbnail
        self._thumbnails_timeline: dict[str, QPixmap] = {}  # path -> timeline thumbnail
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._is_running = True
        
        # Connect internal signal to main thread handler
        self._image_processed.connect(self._on_image_processed)
    
    def load_images(self, paths: list[str]):
        """
        Load multiple images in background.
        
        Loads originals and generates all thumbnail sizes upfront.
        Emits image_loaded signal for each completed image.
        """
        if not self._is_running:
            return

        for path in paths:
            if not path or not Path(path).exists():
                continue
            
            with self._lock:
                if path in self._originals or path in self._pending:
                    continue
                self._pending.add(path)
            
            self._executor.submit(self._load_image, path)
    
    def _load_image(self, path: str):
        """Load image and generate thumbnails in background (Thread-Safe QImage ops only)"""
        if not self._is_running:
            return
            
        try:
            # Load original (QImage is thread-safe)
            image = QImage(path)
            if image.isNull():
                with self._lock:
                    self._pending.discard(path)
                return
            
            if not self._is_running:
                return

            # Generate thumbnails using QImage (thread-safe)
            thumb_small = image.scaled(
                THUMBNAIL_SIZE_SMALL,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            if not self._is_running:
                return

            thumb_timeline = image.scaled(
                THUMBNAIL_SIZE_TIMELINE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            if not self._is_running:
                return

            # Send QImages to main thread for QPixmap conversion
            # Wrap in try-except to handle case where object is deleted during shutdown
            try:
                self._image_processed.emit(path, image, thumb_small, thumb_timeline)
            except RuntimeError:
                # Occurs if the C++ object is deleted while thread is running (app shutdown)
                pass
            
        except Exception as e:
            # Ignore errors during shutdown
            if self._is_running:
                print(f"Error loading image {path}: {e}")
                with self._lock:
                    self._pending.discard(path)

    def _on_image_processed(self, path: str, original: QImage, small: QImage, timeline: QImage):
        """Handle processed images on the main thread"""
        if not self._is_running:
            return
            
        try:
            # Convert to QPixmap (Must be done on main thread)
            pix_original = QPixmap.fromImage(original)
            pix_small = QPixmap.fromImage(small)
            pix_timeline = QPixmap.fromImage(timeline)
            
            with self._lock:
                self._originals[path] = pix_original
                self._thumbnails_small[path] = pix_small
                self._thumbnails_timeline[path] = pix_timeline
                self._pending.discard(path)
            
            self.image_loaded.emit(path)
            
        except Exception as e:
            print(f"Error converting converted images for {path}: {e}")
            with self._lock:
                self._pending.discard(path)
    
    def get_original(self, path: str) -> Optional[QPixmap]:
        """Get original pixmap for preview display"""
        with self._lock:
            return self._originals.get(path)
    
    def get_thumbnail_small(self, path: str) -> Optional[QPixmap]:
        """Get small thumbnail (48x48) for image list"""
        with self._lock:
            return self._thumbnails_small.get(path)
    
    def get_thumbnail_timeline(self, path: str) -> Optional[QPixmap]:
        """Get timeline thumbnail for timeline clips"""
        with self._lock:
            return self._thumbnails_timeline.get(path)
    
    def is_loaded(self, path: str) -> bool:
        """Check if an image is fully loaded"""
        with self._lock:
            return path in self._originals
    
    def clear(self):
        """Clear all cached images"""
        with self._lock:
            self._originals.clear()
            self._thumbnails_small.clear()
            self._thumbnails_timeline.clear()
            self._pending.clear()
    
    def cleanup(self):
        """Cleanup resources"""
        self._is_running = False
        
        # Cancel any pending futures and shutdown
        try:
            # Python 3.9+ supports cancel_futures
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            # Fallback for older python
            self._executor.shutdown(wait=False)
        
        self.clear()


# Global shared instance
_global_cache: Optional[ImageCache] = None


def get_image_cache() -> ImageCache:
    """Get the global image cache instance"""
    global _global_cache
    if _global_cache is None:
        _global_cache = ImageCache()
    return _global_cache

