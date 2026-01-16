## 2024-05-23 - QImageReader Optimization
**Learning:** `QImage(path)` loads the full image into memory even if only a thumbnail is needed. `QImageReader` with `setScaledSize()` is ~10x faster for large images.
**Action:** Use `QImageReader` for thumbnail generation pipelines. Verify code matches documentation claims about performance optimizations.
