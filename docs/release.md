# Build and release Jjal Maker

Use Windows 11 x64, 64-bit Python 3.12, and Inno Setup 6.7.3 with the Korean language file. The scripts use the Windows `curl.exe`. The application builds into a PyInstaller one-directory bundle and an Inno Setup installer. [Inno Setup 6.7.3](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)

## Run from source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe scripts\fetch_dependencies.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m jjal_maker
```

The fetch script verifies the pinned FFmpeg archive and stages the two executables with their sibling DLLs in `vendor/bin`. The media code also supports a compatible FFmpeg and FFprobe on `PATH` for development. The source checkout has no required activation step.

## Build the installer

```powershell
.\scripts\build.ps1
```

Use `-Python <path>` to select another prepared 64-bit Python 3.12 environment, or `-Iscc <path>` to select the compiler. The compiler defaults to `ISCC.exe` on `PATH` or the standard per-user Inno Setup 6 path. The script verifies and stages the inputs under `vendor/`, writes the app to `dist/JjalMaker/`, and creates the setup executable, SHA-256 manifest and corresponding source archives in `dist/release/`.

The installer uses a persistent AppId and defaults to `%LOCALAPPDATA%\Programs\Jjal Maker`. It supports repair and upgrade in place. The application stores settings outside the install directory and saves images to the destination the user selected. The uninstaller removes installed files and shortcuts.

## Verify the release

1. Run the tests above and check that the app opens on Windows 11 x64. Exercise both UI languages and paths containing spaces and Hangul.
2. Install as a regular Windows user. Select Korean in Setup and check the installed app with the offline self-test below. Run Setup again to check repair, then uninstall and verify that settings and external output files remain. For a subsequent version, install the previous published release first and check its upgrade path.
3. In the UI, trim, scrub, crop, switch between **Edit** and **Result**, and check the result at the selection boundaries. Export all four formats. Check a timestamp spanning midnight, a target cap that succeeds after retries, and a deliberately impossible target.
4. When representative media is available, verify APV and HDR from the intended phone, D-Log M with its intended `.cube`, and ordinary H.264/HEVC. Record the tested container, codec and color metadata with any camera-specific claims.

```powershell
$app = Join-Path $env:LOCALAPPDATA 'Programs\Jjal Maker\JjalMaker.exe'
$check = Start-Process $app -ArgumentList '--self-test', 'jjal-smoke' -Wait -PassThru
Get-Content .\jjal-smoke\self-test.json
if ($check.ExitCode) { throw 'Jjal Maker self-test failed.' }
```

The offline self-test creates synthetic input, loads Qt and the bundled media tools, checks a processed preview, and exercises GIF, animated WebP, PNG, APNG and a measured file-size cap.

## Publish

Push a `v<major>.<minor>.<patch>` tag that matches the version in `pyproject.toml`. The [Windows workflow](../.github/workflows/windows.yml) runs tests, builds, checks a per-user install and repair, runs the installed self-test, checks uninstall, and stages a draft GitHub release. Review the draft installer and `SHA256SUMS` before publishing.

Keep `tas-ffmpeg-8.1.2-corresponding-source.tar.xz`, the Qt Base, Qt SVG and Qt Image Formats 6.8.3 archives, and the PySide/Shiboken 6.8.3 archive available on the same release as the installer. The input URLs and hashes are pinned in [the fetch script](../scripts/fetch_dependencies.py). [Third-party notices](../THIRD_PARTY_NOTICES.md)
