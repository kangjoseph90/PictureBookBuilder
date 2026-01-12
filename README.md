# PictureBookBuilder

PictureBookBuilder is a desktop application that automatically constructs a timeline using a script and audio files, combining images and subtitles to create a video (MP4).

## Key Features

*   **Script-Based Auto Alignment**: Automatically aligns audio to text using Whisper and VAD (Voice Activity Detection) technology, given a script and speaker-specific audio files.
*   **Timeline Editing**: Intuitively edit audio, subtitle, and image clips via drag-and-drop on the timeline.
*   **Subtitle Auto-Completion**: Automatically generates subtitle clips based on aligned audio. Utilizes the Korean morphological analyzer (Kiwi) to intelligently split or wrap text based on context.
*   **Batch Image Application**: Load an image folder and automatically place images in sequence matching the audio clips.
*   **Video Rendering**: Export the completed project as an MP4 video. (Uses FFmpeg)
*   **Project Save/Load**: Save your work as a project file (`.pbb`) and resume anytime.

## Script File Format

The script file **must** be a text file (`.txt`) and follow the specific format below to correctly detect speakers and dialogue:

**Format:**
```text
Speaker Name: Dialogue content...
Speaker Name: Dialogue content...
```

**Example:**
```text
Narrator: Once upon a time, there was a brave rabbit.
Rabbit: Hello, world! I am going on an adventure.
Fox: Wait for me!
```

*   Each line should start with the speaker's name followed by a colon (`:`).
*   The text after the colon is treated as the dialogue.
*   Lines not following this format may be ignored or cause parsing errors.

## Tech Stack

This project is built upon the following open-source technologies:

*   **Language & Framework**: Python 3.10+, PyQt6
*   **Speech Recognition & Processing**:
    *   [faster-whisper](https://github.com/SYSTRAN/faster-whisper): Fast Speech-to-Text (STT)
    *   [Silero VAD](https://github.com/snakers4/silero-vad): Voice Activity Detection
    *   [Pydub](https://github.com/jiaaro/pydub): Audio processing
*   **Natural Language Processing (NLP)**:
    *   [KiwiPiePy](https://github.com/bab2min/kiwipiepy): Korean Morphological Analysis (LGPL v3)
*   **Video Processing**:
    *   FFmpeg (Must be installed on the system)

## Installation & Usage

### Prerequisites

*   Python 3.10 or higher
*   FFmpeg (Must be added to the system PATH)

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/your-username/PictureBookBuilder.git
    cd PictureBookBuilder
    ```

2.  Install required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

### Running the Application

```bash
python src/main.py
```

## How to Use

1.  **New Project**: Select `File` > `New Project`.
2.  **Load Script**: Load your script text file (`.txt`) from the left panel. Ensure it follows the **Script File Format** described above.
3.  **Link Audio**: Once the speaker list appears, link an audio file to each speaker.
4.  **Start Processing**: Press `Tools` > `Start Processing` (F5) to analyze and align the audio.
5.  **Add Images**: Load an image folder and use `Tools` > `Batch Apply Images` or drag images to the timeline.
6.  **Edit & Subtitles**: Adjust clip positions on the timeline or edit subtitle text. Use `Tools` > `Auto Format Subtitles` to refine line breaks.
7.  **Export**: Use `Export` > `Render Video` (F9) to save as an MP4 file.

## License

This project is distributed under the **GPL v3 (GNU General Public License v3.0)**. See the `LICENSE` file for details.

This software includes or uses the following open-source libraries:
*   **PyQt6**: GPL v3 (Riverbank Computing)
*   **KiwiPiePy**: LGPL v3 (bab2min)

Since PyQt6 is licensed under GPL, this project also follows the GPL v3 license.
