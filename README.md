# Jjal Maker

[한국어](README.ko.md)

Jjal Maker turns a local video clip into an animated WebP, GIF, APNG, or a single PNG. Trim and crop the clip, resize the result, and optionally burn in a timestamp. Processing runs on the PC. Exported images contain no audio.

## Install on Windows 11 x64

Download `JjalMaker-0.1.0-win-x64-setup.exe` from [GitHub Releases](https://github.com/yunhyok/jjal-maker/releases/latest). Setup offers English and Korean and installs for the current Windows user without an administrator prompt. The app includes Python, Qt, FFmpeg and FFprobe and works offline after installation.

Run a newer installer over the existing installation to upgrade. To remove the app, select **Jjal Maker** in **Settings → Apps → Installed apps**. Exported images and user settings are retained. Release checksums are in `SHA256SUMS` alongside the installer.

## Edit and export

1. Open or drop a local video. Drag the timeline's **In** and **Out** handles, or set each to the playhead.
2. Drag the crop in **Edit** and set the output dimensions and frame rate. **Result** renders the processed image through the same FFmpeg filters used for export.
3. To add a timestamp, enter the displayed time at **In** as `HH:MM:SS[.mmm]` or `YYYY-MM-DD HH:MM:SS[.mmm]`. The timestamp advances from that value as the selection plays. With a date, it advances to the next date at midnight. Without a date, the display wraps to `00:00:00`. Place it over the image or in a bottom band.
4. Choose animated **WebP**, **GIF**, or **APNG**, or **PNG** to save the frame at the playhead. Click **Export** and pick a destination.

The optional **Target file size** is a hard maximum. The app measures each encoded file and tries up to 12 encodes, reducing quality or GIF palette size where applicable, then dimensions and frame rate. If every attempt exceeds the cap, the app reports the smallest attempt and leaves the destination alone. Check the encoder summary for the settings that met the limit.

## Color and input support

The included FFmpeg build has H.264, HEVC and APV decoders. Compatibility also depends on the container, stream profile and metadata. Actual Galaxy footage has not been verified with this release.

Under **Source color**, **Auto** tone maps videos tagged PQ or HLG to SDR. Choose **SDR** to bypass tone mapping, or choose **HDR (PQ)** or **HDR (HLG)** to specify the transfer function when metadata is missing or wrong. For Log footage, choose a `.cube` LUT for the camera and shooting profile. When tone mapping and a LUT are both active, the LUT runs after tone mapping. Real-camera Log color accuracy has not been validated. Use the **Result** preview to check the chosen transform before exporting.

## Build and licensing

See the [v0.1.0 validation record](docs/validation.md) for tested behavior. GIF is limited to 50 FPS and uses 10 ms frame-time increments. Very short WebP animations require at least two 1 ms frames. MPEG-TS seeking decodes from the beginning for correctness and can be slow near the end of long files. Some MKV files without video-track duration use container duration instead; check their final frames before export.

See [build and release instructions](docs/release.md) for development and installer verification. Jjal Maker source is [MIT licensed](LICENSE). The separately executed FFmpeg programs and dynamically loaded Qt libraries come with their own [licenses and source information](THIRD_PARTY_NOTICES.md).
