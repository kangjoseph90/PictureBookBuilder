## 2024-05-23 - Redundant Image Scaling During Playback
**Learning:** When prefetching high-res images for playback, the `ImageCache` was redundantly re-generating all thumbnails (Preview, Timeline, Small) even if they already existed. This wasted CPU cycles on the worker thread during performance-critical playback.
**Action:** Implemented a check in `_batch_load` to detect if thumbnails exist and pass a `skip_thumbnails` flag to `_load_image`. This ensures `scaled()` is only called when absolutely necessary. Always check for redundant work in "hot paths" like media playback loops.
