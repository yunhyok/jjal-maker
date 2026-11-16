# v0.1.0 validation

Verified on Windows 11 x64 on 2026-11-17 with Python 3.12.12, Qt/PySide6 6.8.3 and the bundled TAS-FFMPEG 8.1.2 build. Personal source footage and generated media are excluded from the repository.

## Automated checks

`python -m pytest -q`: **39 passed**.

- Upright rotation and sample aspect ratio, odd crop sizes, Unicode and punctuation in paths, LUT application, tagged PQ/HLG conversion.
- GIF/WebP/PNG/APNG dimensions, transitions, timing and loop metadata; VFR transitions retain their source time.
- Visible timestamp glyphs match literal reference text for both placement modes, including milliseconds, leap-day midnight and clock rollover.
- Result scrubbing and resumed playback follow the export's sampling grid; PNG uses the playhead even outside the trim selection.
- Size-cap retries, an impossible cap, cancellation, late file collisions and a simulated disk-full publish error preserve existing output.
- Audio-leading streams, MPEG-TS seek fallback and FFmpeg-authored Matroska track duration.
- Input manifest rejection and UNC rejection before filesystem access.
- Crop/timeline gestures, language switching with unchanged settings, stale background results, streaming process replacement, shutdown and export jobs.

See [media tests](../tests/test_media.py), [export tests](../tests/test_export.py) and [UI tests](../tests/test_ui.py).

## Actual media and native UI

Two DJI Osmo Nano HEVC Main 10, 3840 × 2160, 23.976 FPS inputs were processed. One was a short clip; the other was an approximately 17 GB recording, tested at the 600-second position. Both exported a one-second center crop to GIF, WebP, PNG and APNG. Reopened results had the requested 320 × 352 dimensions including the timestamp band; all animated formats had 20 frames and 1,000 ms total duration. The checks also confirmed visible, advancing timestamp glyphs. Source color tags were BT.709; this does not establish the capture's Log profile or calibrated color accuracy.

The official [FFmpeg APV 4:2:2 10-bit fixture](https://fate-suite.ffmpeg.org/apv/profile_422-10.apv) was decoded and remuxed to MP4, then probed and rendered with PQ tone mapping. Fixture SHA-256: `abdf4b5e2bf10a6ad8435a5df31f96dde19e0c7f771522db484860966cb9cb77`.

Native Windows checks covered mouse crop selection, Result preview, millisecond timestamp playback across midnight, Korean/English switching, and the Save dialog. A six-second H.264 demonstration exported a 1280 × 720 animated WebP with 120 frames, loop=0, and the matching on-screen result summary.

## Installer

The per-user Korean installer, reinstall/repair, installed offline self-test, and silent uninstall passed. The installed self-test ran with `PATH` limited to Windows/System32 and used `_internal/bin/ffmpeg.exe`; external Python and FFmpeg were not used. All four formats and processed preview passed. Uninstall retained the external test report and the language setting. This is isolation of application dependencies on a development PC, not a claim of testing on every clean PC.

The tag-triggered [Windows workflow](../.github/workflows/windows.yml) repeats tests, the build, installed self-test, reinstall and uninstall before creating a draft release. The release's `SHA256SUMS` identifies its exact downloadable assets; independently built installers can differ in binary metadata.

## Limits

- Actual Galaxy S26 Ultra camera files and real-camera Log color fidelity have not been verified. APV reference decoding is separate evidence.
- GIF timing is quantized to 10 ms; WebP animations need at least two 1 ms frames. Browsers can impose their own minimum frame delay.
- Late MPEG-TS seeks decode from the beginning. Some Matroska writers without usable video duration metadata fall back to container duration.
- Windows ARM64, other operating systems and an upgrade from an earlier released application version have not been tested.
- The installer is unsigned; Windows may show an unknown-publisher warning.
