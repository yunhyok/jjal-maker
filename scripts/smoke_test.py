"""Exercise all exporters on a local clip; keep outputs outside version control."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

from PIL import Image

from jjal_maker.export import export_video
from jjal_maker.media import probe
from jjal_maker.models import Crop, EditSpec, TimestampSpec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=1.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    info = probe(args.source)
    side = min(info.width, info.height)
    crop = Crop((info.width - side) // 2, (info.height - side) // 2, side, side)
    edit = EditSpec(args.start, min(args.start + args.duration, info.duration), crop, 320, 320,
                    playhead=args.start,
                    timestamp=TimestampSpec(True, "2026-11-16 23:59:59.750", "band", 20))
    results = []
    for fmt in ("gif", "webp", "png", "apng"):
        started = time.monotonic()
        result = export_video(info, replace(edit, format=fmt), args.output / f"output.{fmt}", overwrite=True)
        with Image.open(result.path) as picture:
            assert picture.size == (result.width, result.height)
            frame_count = getattr(picture, "n_frames", 1)
            durations = []
            stamps = []
            for index in range(frame_count):
                picture.seek(index)
                picture.load()
                durations.append(picture.info.get("duration", 0))
                band = picture.convert("RGB").crop((0, edit.height, result.width, result.height))
                assert max(band.getextrema()[0]) > 200, (fmt, "Missing timestamp glyphs")
                stamps.append(band.tobytes())
            if fmt != "png":
                assert frame_count > 1
                actual_ms = sum(durations)
                expected_ms = (edit.end - edit.start) * 1000
                assert abs(actual_ms - expected_ms) <= 100, (fmt, actual_ms, expected_ms)
                assert stamps[0] != stamps[-1], (fmt, "Timestamp did not advance")
            else:
                assert frame_count == 1
        results.append({"format": fmt, "bytes": result.bytes,
                        "width": result.width, "height": result.height,
                        "frames": frame_count, "duration_ms": sum(durations),
                        "elapsed_seconds": round(time.monotonic() - started, 2)})
    summary = {"success": True, "source_codec": info.codec, "source_pixel_format": info.pix_fmt,
               "source_dimensions": [info.width, info.height], "results": results}
    (args.output / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
