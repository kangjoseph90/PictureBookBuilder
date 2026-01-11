import sys
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtCore import Qt, QPointF
from src.ui.timeline_widget import TimelineCanvas
from src.ui.clip import TimelineClip

# Tests for TimelineCanvas optimization

@pytest.fixture
def canvas(qtbot):
    widget = TimelineCanvas()
    qtbot.addWidget(widget)
    widget.resize(800, 200)
    widget.show()
    return widget

def test_initial_state(canvas):
    assert canvas.clips == []
    assert canvas.scroll_offset == 0.0
    assert canvas.zoom == 100.0

def test_add_clips_invalidates_cache(canvas):
    clip = TimelineClip(
        id="c1", name="Clip 1", start=0.0, duration=5.0, track=0,
        color=QColor("red"), clip_type="audio"
    )
    canvas.set_clips([clip])
    assert len(canvas.clips) == 1
    assert canvas._background_dirty == True

def test_cache_creation(canvas, qtbot):
    clip = TimelineClip(
        id="c1", name="Clip 1", start=0.0, duration=5.0, track=0,
        color=QColor("red"), clip_type="audio"
    )
    canvas.set_clips([clip])

    # Force paint
    canvas.repaint()

    assert canvas._cached_background is not None
    assert canvas._background_dirty == False
    assert canvas._cached_background.size() == canvas.size()

def test_cache_invalidation_on_resize(canvas):
    # Initial paint
    canvas.repaint()
    assert canvas._background_dirty == False

    # Resize
    canvas.resize(900, 200)
    assert canvas._background_dirty == True

def test_cache_invalidation_on_scroll(canvas):
    # Initial paint
    canvas.repaint()
    assert canvas._background_dirty == False

    # Set playhead with auto_scroll causing scroll change
    canvas.zoom = 100
    canvas.set_playhead(100.0, auto_scroll=True)
    assert canvas.scroll_offset > 0
    assert canvas._background_dirty == True

def test_playhead_update_does_not_invalidate_if_no_scroll(canvas):
    # Initial paint
    canvas.repaint()
    assert canvas._background_dirty == False

    old_scroll = canvas.scroll_offset
    canvas.set_playhead(1.0, auto_scroll=False)

    assert canvas.scroll_offset == old_scroll
    assert canvas._background_dirty == False

def test_selection_invalidates_cache(canvas, qtbot):
    clip = TimelineClip(
        id="c1", name="Clip 1", start=1.0, duration=2.0, track=0,
        color=QColor("red"), clip_type="audio"
    )
    canvas.set_clips([clip])
    canvas.repaint()
    assert canvas._background_dirty == False

    # Simulate click on clip to select
    x = canvas.time_to_x(1.5) # Middle of clip
    y = canvas.get_track_y(0) + 10

    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPointF(x, y).toPoint())

    assert canvas.selected_clip == "c1"
    assert canvas._background_dirty == True

def test_selection_clear_invalidates_cache(canvas, qtbot):
    clip = TimelineClip(
        id="c1", name="Clip 1", start=1.0, duration=2.0, track=0,
        color=QColor("red"), clip_type="audio"
    )
    canvas.set_clips([clip])
    canvas.selected_clip = "c1"
    canvas.repaint()
    assert canvas._background_dirty == False # Should be false after repaint

    # Simulate click on empty area (track 1)
    x = canvas.time_to_x(1.5)
    y = canvas.get_track_y(1) + 10

    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPointF(x, y).toPoint())

    assert canvas.selected_clip is None
    assert canvas._background_dirty == True
