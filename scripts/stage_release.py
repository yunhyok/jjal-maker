"""Stage verified runtime files, license notices and release source assets."""

import json
from importlib.metadata import distribution
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

from fetch_dependencies import DOWNLOADS, ROOT, SOURCE_ARCHIVES, VENDOR, sha256


def qt_notices(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r|xz") as contents:
        for member in contents:
            path = PurePosixPath(member.name)
            relative = path.parts[1:]
            if not member.isfile() or len(relative) < 2:
                continue
            is_notice = path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT", "PATENTS"))
            if relative[0] == "LICENSES" or (
                "3rdparty" in relative and (is_notice or path.name == "qt_attribution.json")
            ):
                target = destination.joinpath(path.parts[0], *relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with contents.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def main(inno_license: Path) -> None:
    app = ROOT / "dist" / "JjalMaker"
    if not (app / "JjalMaker.exe").is_file():
        raise FileNotFoundError("Package JjalMaker with PyInstaller before staging the release")

    internal = app / "_internal"
    # A raster-only Qt Widgets app does not need Qt's optional Mesa OpenGL fallback.
    (internal / "PySide6" / "opengl32sw.dll").unlink(missing_ok=True)
    licenses = app / "licenses"
    vendor_notices = VENDOR / "ffmpeg-licenses"
    manifest = json.loads((vendor_notices / "manifest.json").read_bytes())
    for relative, digest in manifest["artifacts_sha256"].items():
        source = VENDOR / relative
        if sha256(source) != digest:
            raise RuntimeError(f"FFmpeg payload checksum does not match: {relative}")
        target = internal / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source, target)

    shutil.copytree(vendor_notices, licenses / "FFmpeg")
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", app / "THIRD_PARTY_NOTICES.txt")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python.txt")
    shutil.copy2(inno_license, licenses / "InnoSetup.txt")
    pyinstaller = distribution("pyinstaller")
    copying = next(file for file in pyinstaller.files if str(file).endswith("licenses/COPYING.txt"))
    shutil.copy2(pyinstaller.locate_file(copying), licenses / "PyInstaller.txt")

    release = ROOT / "dist" / "release"
    release.mkdir(parents=True, exist_ok=True)
    for url, digest in SOURCE_ARCHIVES:
        archive = DOWNLOADS / url.rsplit("/", 1)[-1]
        if sha256(archive) != digest:
            raise RuntimeError(f"Source checksum does not match: {archive.name}")
        shutil.copy2(archive, release / archive.name)
        if archive.name != "tas-ffmpeg-8.1.2-corresponding-source.tar.xz":
            qt_notices(archive, licenses / "Qt")

    qt_license = licenses / "Qt" / "qtbase-everywhere-src-6.8.3" / "LICENSES"
    for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if not (qt_license / name).is_file():
            raise FileNotFoundError(f"Qt license missing: {name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
