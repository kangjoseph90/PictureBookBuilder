import sys
import time
import os
from PyQt6.QtGui import QImage, QImageReader
from PyQt6.QtCore import QSize, QByteArray, QBuffer, QIODevice

def generate_large_image(filename, width, height):
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(0xFF0000) # Red
    img.save(filename, "JPG", 80)

def benchmark_qimage(filename, target_size):
    start = time.time()
    img = QImage(filename)
    scaled = img.scaled(target_size,  input.AspectRatioMode.KeepAspectRatio, input.TransformationMode.SmoothTransformation) if 'input' in globals() else img.scaled(target_size)
    # Note: scaled() syntax in PyQt6 might need enums.
    # QImage::scaled(const QSize &s, Qt::AspectRatioMode aspectMode = Qt::IgnoreAspectRatio, Qt::TransformationMode mode = Qt::FastTransformation)

    # We will just strictly test load + scale vs reader scale
    end = time.time()
    return end - start

def benchmark_qimagereader(filename, target_size):
    start = time.time()
    reader = QImageReader(filename)
    reader.setScaledSize(target_size)
    img = reader.read()
    end = time.time()
    return end - start

if __name__ == "__main__":
    from PyQt6.QtCore import Qt
    filename = "large_test_image.jpg"
    if not os.path.exists(filename):
        print("Generating image...")
        generate_large_image(filename, 4000, 3000)

    target_size = QSize(640, 360)

    # Warmup
    QImage(filename)

    print("Benchmarking QImage(path).scaled()...")
    start = time.time()
    img = QImage(filename)
    scaled = img.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    duration_qimage = time.time() - start
    print(f"QImage load+scale: {duration_qimage:.4f}s")

    print("Benchmarking QImageReader.setScaledSize()...")
    start = time.time()
    reader = QImageReader(filename)

    # QImageReader::setScaledSize sets the size of the *resulting* image.
    # It doesn't preserve aspect ratio automatically if we just pass 640x360 and the image is different AR?
    # Actually setScaledSize acts as the target size.
    # If we want to keep aspect ratio, we need to calculate the scaled size ourselves first?
    # But QImageReader might optimize if we ask for a smaller size.

    # Let's assume we want to fit in 640x360.
    # We need to read the size first to calculate aspect ratio.

    reader_size_check = QImageReader(filename)
    orig_size = reader_size_check.size()
    scale_ratio = min(target_size.width() / orig_size.width(), target_size.height() / orig_size.height())
    new_size = QSize(int(orig_size.width() * scale_ratio), int(orig_size.height() * scale_ratio))

    reader.setScaledSize(new_size)
    img2 = reader.read()
    duration_reader = time.time() - start
    print(f"QImageReader load scaled: {duration_reader:.4f}s")

    print(f"Speedup: {duration_qimage / duration_reader:.2f}x")

    os.remove(filename)
