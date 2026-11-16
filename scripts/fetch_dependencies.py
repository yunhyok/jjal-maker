"""Fetch and verify the release inputs before using them."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
DOWNLOADS = VENDOR / "downloads"
TAS = "https://github.com/NevermindNilas/TAS-FFMPEG/releases/download/v8.1.2"
QT = "https://download.qt.io/archive/qt/6.8/6.8.3/submodules"
PYSIDE = "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src"
FFMPEG = (
    f"{TAS}/tas-ffmpeg-8.1.2-win64.zip",
    "1d570273dc8be2b3f386467657591a22045d058a830b1eb1eaa881dafb689bfb",
)
SOURCE_ARCHIVES = (
    (f"{TAS}/tas-ffmpeg-8.1.2-corresponding-source.tar.xz",
     "74605032486afbaaf6a489420eb4eea870c20604aace2b993e850a3e7d338ebd"),
    (f"{QT}/qtbase-everywhere-src-6.8.3.tar.xz",
     "56001b905601bb9023d399f3ba780d7fa940f3e4861e496a7c490331f49e0b80"),
    (f"{QT}/qtsvg-everywhere-src-6.8.3.tar.xz",
     "35eb516460f00f264eb504baa253432384351cf23fb9980a5857190e8deef438"),
    (f"{QT}/qtimageformats-everywhere-src-6.8.3.tar.xz",
     "049bfb99845e4801672aca07c3c4fc4c140f932a3a33faa899419579e33ef1c8"),
    (f"{PYSIDE}/pyside-setup-everywhere-src-6.8.3.tar.xz",
     "d2c896f7f1a6a7ea4c9f98cfe317272fd139703ddf612b86999f09e48aeef0e6"),
)
INNO = (
    "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe",
    "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url: str, digest: str) -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / url.rsplit("/", 1)[-1]
    if not path.is_file() or sha256(path) != digest:
        with tempfile.TemporaryDirectory(dir=DOWNLOADS) as temporary:
            download = Path(temporary) / path.name
            subprocess.run(
                ["curl.exe", "--fail", "--location", "--retry", "3", "--silent", "--show-error",
                 "--output", str(download), url],
                check=True,
            )
            if sha256(download) != digest:
                raise RuntimeError(f"SHA-256 mismatch for {path.name}")
            download.replace(path)
    print(f"Verified {path.name}", flush=True)
    return path


def stage_ffmpeg() -> None:
    archive = fetch(*FFMPEG)
    upstream = archive.stem
    licenses = VENDOR / "ffmpeg-licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as zipped:
        manifest = zipped.read(f"{upstream}/manifest.json")
        (licenses / "manifest.json").write_bytes(manifest)
        for relative, digest in json.loads(manifest)["artifacts_sha256"].items():
            destination = VENDOR / relative
            destination.parent.mkdir(exist_ok=True)
            if not destination.is_file() or sha256(destination) != digest:
                destination.write_bytes(zipped.read(f"{upstream}/{relative}"))
            if sha256(destination) != digest:
                raise RuntimeError(f"FFmpeg payload checksum does not match: {relative}")
        prefix = f"{upstream}/licenses/"
        for member in zipped.infolist():
            if member.filename.startswith(prefix) and not member.is_dir():
                destination = licenses / member.filename.removeprefix(prefix)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
        (licenses / "configure-command.txt").write_bytes(
            zipped.read(f"{upstream}/share/tas-ffmpeg/configure-command.txt")
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", action="store_true", help="also download the release source assets")
    parser.add_argument("--inno", action="store_true", help="download the pinned Inno Setup compiler installer")
    args = parser.parse_args()
    stage_ffmpeg()
    if args.sources:
        for archive in SOURCE_ARCHIVES:
            fetch(*archive)
    if args.inno:
        fetch(*INNO)


if __name__ == "__main__":
    main()
