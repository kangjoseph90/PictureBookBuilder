"""
Image Cache - Simplified single-source cache

Loads original images once and generates thumbnails upfront.
All components share the same cache.
"""
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import threading

from PyQt6.QtCore import QObject, pyqtSignal, QSize, Qt
from PyQt6.QtGui import QPixmap, QImage


# Standard thumbnail sizes
THUMBNAIL_SIZE_SMALL = QSize(48, 48)   # For image list
THUMBNAIL_SIZE_TIMELINE = QSize(120, 60)  # For timeline
THUMBNAIL_SIZE_PREVIEW = QSize(640, 360)  # For fast scrubbing (640x360 is ~0.23MP)


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
    
    # args: path, original_qimage, small_qimage, timeline_qimage, preview_qimage
    _image_processed = pyqtSignal(str, QImage, QImage, QImage, QImage)
    
    def __init__(self, max_workers: int = 4, capacity: int = 20, capacity_preview: int = 200):
        super().__init__()
        self._originals: OrderedDict[str, QPixmap] = OrderedDict()  # path -> original pixmap (LRU)
        self._capacity = capacity
        self._thumbnails_small: dict[str, QPixmap] = {}  # path -> 48x48 thumbnail
        self._thumbnails_timeline: dict[str, QPixmap] = {}  # path -> timeline thumbnail
        self._thumbnails_preview: OrderedDict[str, QPixmap] = OrderedDict()  # path -> 640x360 preview thumbnail (LRU)
        self._capacity_preview = capacity_preview
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._is_running = True
        
        # Connect internal signal to main thread handler
        self._image_processed.connect(self._on_image_processed)
    
    def load_images(self, paths: list[str]):
        """
        Load thumbnails only in background (to save memory).
        Does NOT load original image into cache.
        """
        self._batch_load(paths, load_original=False)

    def prefetch_images(self, paths: list[str]):
        """
        Prefetch full original images in background.
        Useful for playback lookahead.
        """
        self._batch_load(paths, load_original=True)

    def _batch_load(self, paths: list[str], load_original: bool):
        if not self._is_running:
            return

        for path in paths:
            if not path:
                continue
            
            with self._lock:
                # If we need original and it's already there, skip
                if load_original and path in self._originals:
                    self._originals.move_to_end(path)
                    continue

                # If we don't need original, check if thumbnails exist
                if not load_original and path in self._thumbnails_small:
                    continue

                if path in self._pending:
                    continue

                self._pending.add(path)
            
            self._executor.submit(self._load_image, path, load_original)

    def _load_image(self, path: str, load_original: bool):
        """Load image and generate thumbnails in background"""
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

            # Generate thumbnails (Cascaded scaling for performance)
            # 1. Largest thumbnail (Preview) from original
            thumb_preview = image.scaled(
                THUMBNAIL_SIZE_PREVIEW,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            # 2. Timeline thumbnail from Preview (faster than from original)
            thumb_timeline = thumb_preview.scaled(
                THUMBNAIL_SIZE_TIMELINE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            # 3. Small thumbnail from Timeline (fastest)
            thumb_small = thumb_timeline.scaled(
                THUMBNAIL_SIZE_SMALL,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            if not self._is_running:
                return

            # Decide what to send back
            # If load_original is False, send QImage() as the original
            final_original = image if load_original else QImage()

            try:
                self._image_processed.emit(path, final_original, thumb_small, thumb_timeline, thumb_preview)
            except RuntimeError:
                pass
            
        except Exception as e:
            # Ignore errors during shutdown
            if self._is_running:
                print(f"Error loading image {path}: {e}")
                with self._lock:
                    self._pending.discard(path)

    def _enforce_capacity(self):
        """Enforce LRU capacity on originals"""
        while len(self._originals) > self._capacity:
            path, _ = self._originals.popitem(last=False)  # Pop oldest (first) item
            #print(f"[Cache] Evicted Original: {Path(path).name}")

    def _enforce_capacity_preview(self):
        """Enforce LRU capacity on preview thumbnails"""
        while len(self._thumbnails_preview) > self._capacity_preview:
            path, _ = self._thumbnails_preview.popitem(last=False)  # Pop oldest (first) item
            #print(f"[Cache] Evicted Preview: {Path(path).name}")

    def _on_image_processed(self, path: str, original: QImage, small: QImage, timeline: QImage, preview: QImage):
        """Handle processed images on the main thread"""
        if not self._is_running:
            return
            
        try:
            # Convert to QPixmap (Must be done on main thread)
            # Only create pixmap if original is valid (it might be null if we only wanted thumbnails)
            pix_original = QPixmap.fromImage(original) if not original.isNull() else None
            pix_small = QPixmap.fromImage(small)
            pix_timeline = QPixmap.fromImage(timeline)
            pix_preview = QPixmap.fromImage(preview)
            
            with self._lock:
                if pix_original:
                    self._originals[path] = pix_original
                    self._originals.move_to_end(path)
                    #print(f"[Cache] Registered Original: {Path(path).name} (Current: {len(self._originals)}/{self._capacity})")
                    self._enforce_capacity()

                self._thumbnails_small[path] = pix_small
                self._thumbnails_timeline[path] = pix_timeline

                self._thumbnails_preview[path] = pix_preview
                self._thumbnails_preview.move_to_end(path)
                self._enforce_capacity_preview()

                self._pending.discard(path)
            
            self.image_loaded.emit(path)
            
        except Exception as e:
            print(f"Error converting converted images for {path}: {e}")
            with self._lock:
                self._pending.discard(path)
    
    def get_original(self, path: str) -> Optional[QPixmap]:
        """Get original pixmap for preview display"""
        with self._lock:
            if path in self._originals:
                self._originals.move_to_end(path)
                #print(f"[Cache] Hit Original: {Path(path).name}")
                return self._originals[path]
            #print(f"[Cache] Miss Original: {Path(path).name}")
            return None
    
    def get_thumbnail_small(self, path: str) -> Optional[QPixmap]:
        """Get small thumbnail (48x48) for image list"""
        with self._lock:
            return self._thumbnails_small.get(path)
    
    def get_thumbnail_timeline(self, path: str) -> Optional[QPixmap]:
        """Get timeline thumbnail for timeline clips"""
        with self._lock:
            return self._thumbnails_timeline.get(path)
            
    def get_thumbnail_preview(self, path: str) -> Optional[QPixmap]:
        """Get medium-res preview thumbnail (640x360)"""
        with self._lock:
            if path in self._thumbnails_preview:
                self._thumbnails_preview.move_to_end(path)
                return self._thumbnails_preview[path]
            return None
    
    def has_original(self, path: str) -> bool:
        """Check if the full original image is in cache"""
        with self._lock:
            return path in self._originals

    def has_thumbnail(self, path: str) -> bool:
        """Check if thumbnails are in cache"""
        with self._lock:
            return path in self._thumbnails_small

    def is_loaded(self, path: str) -> bool:
        """Check if an image (at least thumbnails) is in cache"""
        with self._lock:
            return path in self._thumbnails_small or path in self._originals
    
    def clear(self):
        """Clear all cached images"""
        with self._lock:
            self._originals.clear()
            self._thumbnails_small.clear()
            self._thumbnails_timeline.clear()
            self._thumbnails_preview.clear()
            self._pending.clear()
    
    def cleanup(self):
        """Cleanup resources"""
        self._is_running = False
        
        # Cancel any pending futures and shutdown
        # IMPORTANT: wait=True to ensure all background threads complete before returning
        # This prevents thread leaks when the application closes
        try:
            # Python 3.9+ supports cancel_futures
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            # Fallback for older python
            self._executor.shutdown(wait=True)
        
        self.clear()


# Global shared instance
_global_cache: Optional[ImageCache] = None


def get_image_cache() -> ImageCache:
    """Get the global image cache instance"""
    global _global_cache
    if _global_cache is None:
        _global_cache = ImageCache()
    return _global_cache

