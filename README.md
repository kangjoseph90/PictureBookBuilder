# PictureBookBuilder

A desktop application that automatically constructs a timeline using a script and audio files, combining images and subtitles to create a video (MP4).

## Key Features

-   **Script-Based Auto Alignment**: Aligns audio to text using Whisper and VAD.
-   **Timeline Editing**: Edit audio, subtitle, and image clips via drag-and-drop.
-   **Subtitle Auto-Completion**: Generates subtitle clips with intelligent line breaks using morphological analysis.
-   **Batch Image Application**: Automatically place images matching audio clips.
-   **Video Rendering**: Export as MP4 using FFmpeg.
-   **Project Save/Load**: Save and resume work via `.pbb` project files.

## Script File Format

```text
Narrator: Once upon a time, there was a brave rabbit.
Rabbit: Hello, world! I am going on an adventure.
Fox: Wait for me!
```

Each line follows `Speaker Name: Dialogue`. Lines not matching this format may be ignored.

## Tech Stack

-   **Python 3.10+**, **PyQt6**
-   [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Silero VAD](https://github.com/snakers4/silero-vad), [Pydub](https://github.com/jiaaro/pydub)
-   [KiwiPiePy](https://github.com/bab2min/kiwipiepy) (Korean NLP, LGPL v3)
-   FFmpeg (system install required)

## Installation

**Prerequisites**: Python 3.10+, FFmpeg in PATH

```bash
git clone https://github.com/kangjoseph90/PictureBookBuilder.git
cd PictureBookBuilder
pip install -r requirements.txt
python src/main.py
```

## How to Use

1. **New Project**: `File` > `New Project`
2. **Load Script**: Load a `.txt` script file from the left panel.
3. **Link Audio**: Assign audio files to each speaker.
4. **Process**: `Tools` > `Start Processing` (F5)
5. **Add Images**: Load folder and use `Tools` > `Batch Apply Images`
6. **Edit**: Adjust clips and subtitles on the timeline.
7. **Export**: `Export` > `Render Video` (F9)

## License

GPL v3. See `LICENSE` for details.

Includes: **PyQt6** (GPL v3), **KiwiPiePy** (LGPL v3)
