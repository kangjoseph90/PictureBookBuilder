
import sys
import os
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

from ui.render_settings_dialog import RenderSettingsDialog
from exporters.video_renderer import VideoRenderer, ImageSegment, SubtitleSegment

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_render_settings_dialog_defaults(qapp):
    """Test that the dialog initializes with correct default settings"""
    dialog = RenderSettingsDialog()
    settings = dialog.get_settings()

    assert settings['width'] == 1920
    assert settings['height'] == 1080
    assert settings['fps'] == 30
    assert settings['subtitle_enabled'] is True
    assert settings['font_family'] == 'Malgun Gothic'
    assert settings['font_size'] == 32
    # Check default color logic (white font, black outline/bg)
    assert settings['font_color'] == '#FFFFFF'
    assert settings['position'] == 'Bottom'

def test_video_renderer_command_generation():
    """Test that VideoRenderer generates correct FFmpeg command with settings"""

    # Mock subprocess.Popen
    with patch('subprocess.Popen') as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout = ["progress=end"]
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        # Mock checks for audio duration
        with patch.object(VideoRenderer, '_get_audio_duration_seconds', return_value=10.0):
             with patch('pathlib.Path.exists', return_value=True):

                # Instantiate with args like production
                renderer = VideoRenderer(width=1280, height=720, fps=24)

                settings = {
                    'font_family': 'Arial',
                    'font_size': 50,
                    'font_color': '#FF0000', # Red
                    'outline_enabled': True,
                    'outline_width': 3,
                    'outline_color': '#00FF00', # Green
                    'bg_enabled': False,
                    'position': 'Top',
                    'margin_v': 100
                }

                images = [ImageSegment("img.jpg", 0, 5)]
                subtitles = [SubtitleSegment("Test", 0, 5)]

                renderer.render(
                    images=images,
                    audio_path="audio.wav",
                    subtitles=subtitles,
                    output_path="out.mp4",
                    settings=settings
                )

                # Verify call args
                args, _ = mock_popen.call_args
                cmd = args[0]

                # Check resolution and fps in command
                # It appears in color source if no image, or in scale filter
                filter_str = cmd[cmd.index('-filter_complex') + 1]
                assert "scale=1280:720" in filter_str

                fps_idx = cmd.index('-r')
                assert cmd[fps_idx + 1] == '24'

                # Check for filter complex
                # filter_complex_idx = cmd.index('-filter_complex')
                # filter_str = cmd[filter_complex_idx + 1]

                # Check style in force_style
                assert "Fontname=Arial" in filter_str
                assert "Fontsize=50" in filter_str
                # Red text: &H000000FF (BBGGRR)
                assert "PrimaryColour=&H000000FF" in filter_str
                # Green outline: &H0000FF00
                assert "OutlineColour=&H0000FF00" in filter_str
                # Top alignment: 8
                assert "Alignment=8" in filter_str
                assert "MarginV=100" in filter_str
                assert "BorderStyle=1" in filter_str # Outline

def test_video_renderer_bg_box_logic():
    """Test VideoRenderer logic for background box"""
    with patch('subprocess.Popen') as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout = ["progress=end"]
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        with patch.object(VideoRenderer, '_get_audio_duration_seconds', return_value=10.0):
             with patch('pathlib.Path.exists', return_value=True):

                renderer = VideoRenderer(width=1920, height=1080, fps=30)

                settings = {
                    'bg_enabled': True,
                    'bg_color': '#0000FF', # Blue
                    'bg_alpha': 255, # Fully opaque (Qt scale) -> 0 (ASS scale)
                }

                images = [ImageSegment("img.jpg", 0, 5)]
                subtitles = [SubtitleSegment("Test", 0, 5)]

                renderer.render(
                    images=images,
                    audio_path="audio.wav",
                    subtitles=subtitles,
                    output_path="out.mp4",
                    settings=settings
                )

                args, _ = mock_popen.call_args
                cmd = args[0]
                filter_str = cmd[cmd.index('-filter_complex') + 1]

                # BorderStyle=3 (Box)
                assert "BorderStyle=3" in filter_str
                # Blue BG: &H00FF0000 (Alpha 00, BBGGRR)
                # settings['bg_alpha'] = 255 (opaque in Qt) -> 0 (opaque in ASS)
                assert "OutlineColour=&H00FF0000" in filter_str
