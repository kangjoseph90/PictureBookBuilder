## 2024-05-23 - Infinite Thumbnail Cache
**Learning:** The `ImageCache` class was storing all generated preview thumbnails (640x360) in an unbounded dictionary. For a project with thousands of images, this leads to GBs of memory usage (approx 1MB per image).
**Action:** Implemented LRU eviction for preview thumbnails with a capacity limit. Always verify that caches have a bounded capacity or eviction policy, especially when dealing with image data in UI applications.
