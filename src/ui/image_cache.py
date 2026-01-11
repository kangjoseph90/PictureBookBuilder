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
    
    def __init__(self, max_workers: int = 4):
        super().__init__()
        self._originals: dict[str, QPixmap] = {}  # path -> original pixmap
        self._thumbnails_small: dict[str, QPixmap] = {}  # path -> 48x48 thumbnail
        self._thumbnails_timeline: dict[str, QPixmap] = {}  # path -> timeline thumbnail
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
    
    def load_images(self, paths: list[str]):
        """
        Load multiple images in background.
        
        Loads originals and generates all thumbnail sizes upfront.
        Emits image_loaded signal for each completed image.
        """
        for path in paths:
            if not path or not Path(path).exists():
                continue
            
            with self._lock:
                if path in self._originals or path in self._pending:
                    continue
                self._pending.add(path)
            
            self._executor.submit(self._load_image, path)
    
    def _load_image(self, path: str):
        """Load image and generate thumbnails in background"""
        try:
            # Load original (QImage is thread-safe)
            image = QImage(path)
            if image.isNull():
                with self._lock:
                    self._pending.discard(path)
                return
            
            # Convert to pixmap
            original = QPixmap.fromImage(image)
            
            # Generate thumbnails
            thumb_small = original.scaled(
                THUMBNAIL_SIZE_SMALL,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            thumb_timeline = original.scaled(
                THUMBNAIL_SIZE_TIMELINE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            # Store all variants
            with self._lock:
                self._originals[path] = original
                self._thumbnails_small[path] = thumb_small
                self._thumbnails_timeline[path] = thumb_timeline
                self._pending.discard(path)
            
            # Notify completion
            self.image_loaded.emit(path)
            
        except Exception as e:
            print(f"Error loading image {path}: {e}")
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
    
    def cleanup(self):
        """Cleanup resources"""
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
