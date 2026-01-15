## 2024-05-22 - QImageReader Scaled Loading
**Learning:** `QImageReader.setScaledSize()` is >10x faster than `QImage(path).scaled()` for large images (e.g. 4K) because it avoids decoding the full resolution image.
**Action:** Always prefer `QImageReader` with `setScaledSize` when loading thumbnails or previews from disk if the full resolution image is not immediately needed. Verify support with `supportsOption(QImageIOHandler.ImageOption.ScaledSize)`.
