"""
Video Renderer - Render final video with images, audio, and subtitles
"""
from pathlib import Path
from dataclasses import dataclass

from moviepy import (
    ImageClip, AudioFileClip, CompositeVideoClip, 
    concatenate_videoclips, TextClip
)
from PIL import Image
import numpy as np

from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


@dataclass
class ImageSegment:
    """An image segment for the timeline"""
    image_path: str
    start_time: float
    end_time: float


@dataclass
class SubtitleSegment:
    """A subtitle overlay"""
    text: str
    start_time: float
    end_time: float


class VideoRenderer:
    """Render final video from images, audio, and subtitles"""
    
    def __init__(
        self,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        fps: int = VIDEO_FPS
    ):
        self.width = width
        self.height = height
        self.fps = fps
    
    def load_and_resize_image(self, image_path: str) -> np.ndarray:
        """Load and resize image to video dimensions"""
        img = Image.open(image_path)
        
        # Calculate scaling to fit while maintaining aspect ratio
        img_ratio = img.width / img.height
        video_ratio = self.width / self.height
        
        if img_ratio > video_ratio:
            # Image is wider, fit to width
            new_width = self.width
            new_height = int(self.width / img_ratio)
        else:
            # Image is taller, fit to height
            new_height = self.height
            new_width = int(self.height * img_ratio)
        
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Create black background and paste image centered
        background = Image.new('RGB', (self.width, self.height), (0, 0, 0))
        x = (self.width - new_width) // 2
        y = (self.height - new_height) // 2
        
        # Handle RGBA images
        if img.mode == 'RGBA':
            background.paste(img, (x, y), img)
        else:
            background.paste(img, (x, y))
        
        return np.array(background)
    
    def create_image_clip(self, segment: ImageSegment) -> ImageClip:
        """Create an ImageClip from an image segment"""
        img_array = self.load_and_resize_image(segment.image_path)
        duration = segment.end_time - segment.start_time
        
        clip = ImageClip(img_array).with_duration(duration)
        clip = clip.with_start(segment.start_time)
        
        return clip
    
    def create_subtitle_clip(
        self,
        segment: SubtitleSegment,
        font_size: int = 40,
        color: str = 'white',
        stroke_color: str = 'black',
        stroke_width: int = 2
    ) -> TextClip:
        """Create a subtitle TextClip"""
        duration = segment.end_time - segment.start_time
        
        clip = TextClip(
            text=segment.text,
            font_size=font_size,
            color=color,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            method='caption',
            size=(self.width - 100, None),
            text_align='center'
        )
        
        clip = clip.with_duration(duration)
        clip = clip.with_start(segment.start_time)
        clip = clip.with_position(('center', self.height - 120))
        
        return clip
    
    def render(
        self,
        images: list[ImageSegment],
        audio_path: str | Path,
        subtitles: list[SubtitleSegment] | None = None,
        output_path: str | Path = "output.mp4"
    ) -> None:
        """Render final video
        
        Args:
            images: List of ImageSegment objects
            audio_path: Path to merged audio file
            subtitles: List of SubtitleSegment objects (optional)
            output_path: Output video file path
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create image clips
        image_clips = [self.create_image_clip(img) for img in images]
        
        # Create composite from images
        if image_clips:
            video = CompositeVideoClip(image_clips, size=(self.width, self.height))
        else:
            # Black background if no images
            video = ImageClip(
                np.zeros((self.height, self.width, 3), dtype=np.uint8)
            ).with_duration(10)
        
        # Add subtitles if provided
        if subtitles:
            subtitle_clips = [self.create_subtitle_clip(sub) for sub in subtitles]
            video = CompositeVideoClip([video] + subtitle_clips)
        
        # Add audio
        audio = AudioFileClip(str(audio_path))
        video = video.with_duration(audio.duration)
        video = video.with_audio(audio)
        
        # Export
        video.write_videofile(
            str(output_path),
            fps=self.fps,
            codec='libx264',
            audio_codec='aac'
        )
        
        # Cleanup
        video.close()
        audio.close()
