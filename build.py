import PyInstaller.__main__
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

def build():
    BASE_DIR = Path(__file__).parent.absolute()
    SRC_DIR = BASE_DIR / "src"
    
    # Collect data and hidden imports for complex libraries
    # faster_whisper, moviepy, etc.
    datas = []
    binaries = []
    hiddenimports = [
        "moviepy",
        "moviepy.audio.fx.all",
        "faster_whisper",
        "pyqtgraph", 
        "PIL",
        "PIL.Image",
        "PIL.ImageQt",
        "soundfile",
    ]

    # Collect package data/binaries for faster_whisper and others
    for package in ["faster_whisper", "tokenizers", "ctranslate2"]:
        try:
            tmp_datas, tmp_binaries, tmp_hidden = collect_all(package)
            datas.extend(tmp_datas)
            binaries.extend(tmp_binaries)
            hiddenimports.extend(tmp_hidden)
        except Exception as e:
            print(f"Warning: Could not collect info for {package}: {e}")

    args = [
        str(SRC_DIR / "main.py"), # Entry point
        "--name=PictureBookBuilder",
        "--noconfirm",
        "--clean",
        "--windowed", # GUI mode
        f"--paths={SRC_DIR}",
        "--icon=NONE", # Default icon for now
    ]

    # Add collected data/binaries/imports
    for d in datas:
        if d[1] != ".": # Avoid collecting root recursively if it happens
             args.append(f"--add-data={d[0]};{d[1]}")
    
    for b in binaries:
        args.append(f"--add-binary={b[0]};{b[1]}")

    for h in set(hiddenimports): # Deduplicate
        args.append(f"--hidden-import={h}")

    print("Running PyInstaller with args:")
    # print(args)
    
    PyInstaller.__main__.run(args)

if __name__ == "__main__":
    build()
