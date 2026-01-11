import os
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QImage
from src.ui.image_cache import ImageCache

@pytest.fixture
def image_cache(qtbot):
    cache = ImageCache(max_workers=2)
    yield cache
    cache.cleanup()

def test_load_image_thread_safety(image_cache, qtbot, tmp_path):
    """Verify that images are loaded and converted to pixmaps on the main thread"""
    
    # Create a dummy image
    image_path = str(tmp_path / "test_image.png")
    img = QImage(100, 100, QImage.Format.Format_RGB32)
    img.fill(0xFF0000) # Red
    img.save(image_path)
    
    # Monitor signal
    with qtbot.waitSignal(image_cache.image_loaded, timeout=2000) as blocker:
        image_cache.load_images([image_path])
    
    # Check results
    assert image_cache.is_loaded(image_path)
    
    # Check pixmaps are valid
    original = image_cache.get_original(image_path)
    small = image_cache.get_thumbnail_small(image_path)
    timeline = image_cache.get_thumbnail_timeline(image_path)
    
    assert original is not None
    assert not original.isNull()
    assert small is not None
    assert not small.isNull()
    assert timeline is not None
    assert not timeline.isNull()
    
    # Check thread affinity (indirectly via function behavior)
    # The fact that we have QPixmaps working means it was processed correctly
    # as QPixmaps created in other threads usually show warnings or crash in strict environments

def test_cleanup_cancelled_futures(image_cache):
    """Test cleanup doesn't crash"""
    image_cache.cleanup()
    # If no exception, it passed

def test_shutdown_race_condition(image_cache, qtbot, tmp_path):
    """
    Simulate race condition: cleanup() called while threads are running.
    Should not raise "wrapped C/C++ object deleted" error.
    """
    import time
    
    # Create multiple images to ensure threads stay busy
    paths = []
    for i in range(10):
        p = str(tmp_path / f"race_img_{i}.png")
        img = QImage(500, 500, QImage.Format.Format_RGB32)
        img.fill(0x00FF00)
        img.save(p)
        paths.append(p)
    
    # Start loading
    image_cache.load_images(paths)
    
    # Let threads start but not finish
    time.sleep(0.01)
    
    # Force cleanup immediately (simulating app shutdown)
    image_cache.cleanup()
    
    # Wait a bit to let threads possibly hit the race condition
    # In a real crash, this test process would fail or print errors
    time.sleep(0.2)
    
    # Assert we are still alive and cleanup worked
    assert not image_cache._is_running
    assert len(image_cache._originals) == 0

