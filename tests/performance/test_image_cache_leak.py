import pytest
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import QSize
from src.ui.image_cache import ImageCache, THUMBNAIL_SIZE_SMALL, THUMBNAIL_SIZE_TIMELINE
import tempfile
import shutil
import os

# Create a temporary directory for dummy images
@pytest.fixture
def image_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)

@pytest.fixture
def cache():
    return ImageCache(max_workers=1)

def create_dummy_image(path):
    img = QImage(100, 100, QImage.Format.Format_RGB32)
    img.fill(0)
    img.save(path)

def test_cache_eviction(image_dir, cache, qtbot):
    """
    Verify that the cache respects the MAX_ORIGINALS_CACHE limit
    and evicts the oldest items.
    """
    # Create 30 dummy images
    image_paths = []
    for i in range(30):
        path = os.path.join(image_dir, f"img_{i}.png")
        create_dummy_image(path)
        image_paths.append(path)

    # Load all images
    for path in image_paths:
        # Mock the QImages that would come from the thread
        img = QImage(path)
        thumb_s = img.scaled(THUMBNAIL_SIZE_SMALL)
        thumb_t = img.scaled(THUMBNAIL_SIZE_TIMELINE)

        cache._on_image_processed(path, img, thumb_s, thumb_t)

    # Check cache size
    # With eviction policy, size should be 20
    assert len(cache._originals) == 20, f"Expected 20, got {len(cache._originals)}"

    # Check that the OLDEST images (img_0 to img_9) are evicted
    # and NEWEST (img_10 to img_29) are present
    for i in range(10):
        path = image_paths[i]
        assert path not in cache._originals, f"Image {i} should be evicted"

    for i in range(10, 30):
        path = image_paths[i]
        assert path in cache._originals, f"Image {i} should be in cache"

    # Check if thumbnail caches also grew (these should stay, LRU usually applies to large originals)
    assert len(cache._thumbnails_small) == 30
    assert len(cache._thumbnails_timeline) == 30

def test_cache_access_update(image_dir, cache, qtbot):
    """
    Verify that accessing an item moves it to the end (MRU) preventing eviction.
    """
    image_paths = []
    for i in range(25):
        path = os.path.join(image_dir, f"img_{i}.png")
        create_dummy_image(path)
        image_paths.append(path)

    # Load 20 images (filling the cache)
    for i in range(20):
        path = image_paths[i]
        img = QImage(path)
        thumb_s = img.scaled(THUMBNAIL_SIZE_SMALL)
        thumb_t = img.scaled(THUMBNAIL_SIZE_TIMELINE)
        cache._on_image_processed(path, img, thumb_s, thumb_t)

    assert len(cache._originals) == 20
    assert image_paths[0] in cache._originals

    # Access the first image (img_0) to mark it as recently used
    assert cache.get_original(image_paths[0]) is not None

    # Load one more image (img_20)
    # This should evict the LEAST recently used.
    # Since we just accessed img_0, it should be MRU.
    # The LRU should be img_1 (index 1).

    path = image_paths[20]
    img = QImage(path)
    thumb_s = img.scaled(THUMBNAIL_SIZE_SMALL)
    thumb_t = img.scaled(THUMBNAIL_SIZE_TIMELINE)
    cache._on_image_processed(path, img, thumb_s, thumb_t)

    # img_0 should STILL be in cache
    assert image_paths[0] in cache._originals, "img_0 should have been preserved by access"

    # img_1 should be evicted
    assert image_paths[1] not in cache._originals, "img_1 should be evicted"

    # img_20 should be in cache
    assert image_paths[20] in cache._originals
