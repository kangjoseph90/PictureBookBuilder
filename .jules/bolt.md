## 2024-05-23 - QPixmap Reuse Optimization
**Learning:** In Qt's `paintEvent`, creating a new `QPixmap` (even if transparent) for double-buffering is a relatively expensive operation involving memory allocation. Reusing the existing `QPixmap` instance if dimensions and DPR match can significantly improve scrolling performance by reducing allocation overhead and GC pressure.
**Action:** When implementing double-buffering in Qt widgets, always check if the cached buffer (`QPixmap` or `QImage`) can be reused before allocating a new one. Use `.fill()` to clear the buffer.
