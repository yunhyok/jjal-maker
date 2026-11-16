"""Offline installation check, invoked explicitly with --self-test OUTPUT_DIR."""

from dataclasses import replace
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import traceback


def run(output_dir: str) -> int:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "checks": []}
    try:
        from PySide6.QtWidgets import QApplication
        from jjal_maker.media import find_binary, probe, preview_command
        from jjal_maker.export import export_video
        from jjal_maker.models import Crop, EditSpec, TimestampSpec
        from jjal_maker.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        app.setOrganizationName("JjalMakerSelfTest")
        app.setApplicationName("JjalMakerSelfTest")
        window = MainWindow()
        window.close()
        app.processEvents()
        report["checks"].append("Qt widgets and plugins load")

        executable = find_binary("ffmpeg")
        report["ffmpeg"] = str(executable)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryDirectory(prefix="jjal-selftest-", dir=destination) as temp:
            source = Path(temp) / "source.nut"
            subprocess.run(
                [str(executable), "-hide_banner", "-v", "error", "-nostdin", "-y",
                 "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=20:duration=1",
                 "-c:v", "rawvideo", "-threads", "1", str(source)],
                check=True, capture_output=True, timeout=30, creationflags=flags,
            )
            info = probe(source)
            edit = EditSpec(0.1, 0.7, Crop(10, 10, 240, 150), 240, 150,
                            playhead=0.3,
                            timestamp=TimestampSpec(True, "2026-11-16 23:59:59.800", "band", 18))
            command = preview_command(info, edit, 0.3, result=True)
            raw = subprocess.run(command.args, check=True, capture_output=True,
                                 timeout=30, creationflags=flags).stdout
            assert len(raw) == command.width * command.height * 3
            band = raw[150 * command.width * 3:]
            assert sum(channel > 200 for channel in band) > 30, "Timestamp glyphs are missing"
            report["checks"].append("Processed RGB preview")
            for fmt in ("gif", "webp", "png", "apng"):
                result = export_video(info, replace(edit, format=fmt), Path(temp) / f"out.{fmt}")
                blob = result.path.read_bytes()
                assert result.bytes == len(blob) and len(blob) > 32
                assert result.width == 240 and result.height > 150
                if fmt == "gif":
                    assert blob[:6] in (b"GIF87a", b"GIF89a")
                    assert struct.unpack("<HH", blob[6:10]) == (result.width, result.height)
                elif fmt == "webp":
                    assert blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
                    assert b"ANIM" in blob and b"ANMF" in blob
                    assert struct.unpack("<I", blob[4:8])[0] + 8 == len(blob)
                else:
                    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
                    assert struct.unpack(">II", blob[16:24]) == (result.width, result.height)
                    assert (b"acTL" in blob) == (fmt == "apng")
                report["checks"].append(f"{fmt}: {result.width}x{result.height}, {result.bytes} bytes")
            limited = export_video(info, replace(edit, target_bytes=1_000_000), Path(temp) / "limited.webp")
            assert limited.bytes <= 1_000_000
            report["checks"].append("Measured target size")
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (destination / "self-test.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
