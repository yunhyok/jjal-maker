"""Encode local files with FFmpeg, checking size before publishing the result."""

import math
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread

from .media import (
    CREATE_NO_WINDOW,
    _PROTOCOLS,
    _animation_timing,
    _filters,
    _input_args,
    _inside,
    _local_path,
    _timestamp_min_width,
    output_dimensions,
    validate_edit,
)
from .models import EditSpec, ExportResult, MediaInfo


class ExportCancelled(Exception):
    """The export was cancelled before any result was published."""


class TargetSizeError(RuntimeError):
    def __init__(self, smallest_bytes: int):
        self.smallest_bytes = smallest_bytes
        super().__init__(f"No export met the target size. The smallest result was {smallest_bytes:,} bytes.")


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ExportCancelled("Export cancelled.")


def _run(
    args: list[str], cancel: Event | None, report: Callable[[float], None], duration: float,
) -> None:
    _check_cancel(cancel)
    updates: SimpleQueue[int] = SimpleQueue()
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
        text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
    ) as process:
        assert process.stdout is not None
        stdout = process.stdout

        def read_progress() -> None:
            for line in stdout:
                field, _, value = line.strip().partition("=")
                if field == "out_time_us" and value.isdigit():
                    updates.put(int(value))

        reader = Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                elapsed = None
                while not updates.empty():
                    elapsed = updates.get_nowait()
                if elapsed is not None:
                    report(min(0.99, elapsed / 1_000_000 / duration))
                try:
                    process.wait(timeout=0.10)
                except subprocess.TimeoutExpired:
                    continue
            _check_cancel(cancel)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join()
        if process.returncode:
            errors.seek(max(0, errors.tell() - 6000))
            detail = errors.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"FFmpeg exited with code {process.returncode}.")
    report(1.0)


def _candidate_edits(edit: EditSpec) -> Iterator[EditSpec]:
    yield edit
    if edit.target_bytes is None:
        return
    lossy = edit.format in {"gif", "webp"}
    quality = edit.quality
    if lossy:
        for reduced in (60, 40, 25):
            if reduced < quality:
                quality = reduced
                yield replace(edit, quality=quality)

    previous = (edit.width, edit.height, edit.fps, edit.timestamp.font_size)
    minimum_scale = _timestamp_min_width(edit.timestamp) / edit.width if edit.timestamp.enabled else 0.0
    for factor, rate in (
        (0.90, 1.00), (0.80, 1.00), (0.70, 0.80), (0.60, 0.65),
        (0.50, 0.55), (0.40, 0.45), (0.30, 0.35), (0.20, 0.25),
    ):
        factor = max(factor, minimum_scale)
        width = max(1, round(edit.width * factor))
        height = max(1, round(edit.height * factor))
        fps = edit.fps if edit.format == "png" else max(1.0, edit.fps * rate)
        font = max(1, round(edit.timestamp.font_size * factor)) if edit.timestamp.enabled else edit.timestamp.font_size
        current = (width, height, fps, font)
        if current != previous:
            previous = current
            yield replace(
                edit, width=width, height=height, fps=fps, quality=quality,
                timestamp=replace(edit.timestamp, font_size=font),
            )


def _temporary_path(directory: Path, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".jjal-", suffix=suffix, delete=False) as temp:
        return Path(temp.name)


def _encode(
    info: MediaInfo, edit: EditSpec, temp: Path, cancel: Event | None,
    report: Callable[[float, str], None],
) -> float:
    duration = edit.end - edit.start
    still = edit.format == "png"
    position = _inside(edit.playhead, 0.0, info.duration) if still else edit.start
    fps, frames = (edit.fps, 1) if still else _animation_timing(edit)
    filters = _filters(info, edit, position, edit.end, applied=True, still=still, fps=fps, frames=frames)
    base = _input_args(info, position)
    base[1:1] = ["-y", "-progress", "pipe:1", "-stats_period", "0.2", "-nostats", "-abort_on", "empty_output"]
    options = [
        "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_chapters", "-1", "-an", "-sn", "-dn",
        "-fps_mode", "passthrough", "-threads", "2",
    ]
    delay = duration - (frames - 1) / fps
    if edit.format == "gif":
        palette = _temporary_path(temp.parent, ".png")
        try:
            colors = max(4, round(edit.quality * 256 / 100))
            palette_filters = f"{filters},palettegen=max_colors={colors}:reserve_transparent=0"
            report(0.0, "Generating palette")
            _run(
                base + ["-map", f"0:{info.stream_index}", "-vf", palette_filters]
                + options + ["-frames:v", "1", "-c:v", "png", "-f", "image2", "-update", "1", str(palette)],
                cancel, lambda fraction: report(fraction / 2, "Generating palette"), duration,
            )
            gif_filters = f"[0:{info.stream_index}]{filters}[frames];[frames][1:v]paletteuse=diff_mode=rectangle[out]"
            report(0.5, "Encoding")
            _run(
                base + ["-threads", "1", "-protocol_whitelist", _PROTOCOLS, "-i", str(palette)]
                + ["-filter_complex", gif_filters, "-map", "[out]"] + options
                + ["-c:v", "gif", "-loop", "0", "-final_delay",
                   str(max(1, math.floor(duration * 100 + 0.5) - math.floor((frames - 1) * 100 / fps + 0.5))),
                   "-f", "gif", str(temp)],
                cancel, lambda fraction: report((1 + fraction) / 2, "Encoding"), duration,
            )
        finally:
            palette.unlink(missing_ok=True)
        return fps

    if edit.format == "png":
        encoder = ["-c:v", "png", "-frames:v", "1", "-f", "image2", "-update", "1"]
    elif edit.format == "apng":
        final_delay = Fraction(max(delay, 1 / 65535)).limit_denominator(65535)
        encoder = ["-c:v", "apng", "-plays", "0", "-final_delay", str(final_delay), "-f", "apng"]
    else:
        # libwebp writes one packet per frame. The muxer uses the last packet duration for the tail.
        end_ms = math.floor(duration * 1000 + 0.5)
        timing = f"setts=time_base=1/1000:prescale=1:duration=if(eq(N\\,{frames - 1})\\,max(1\\,{end_ms}-PTS)\\,DURATION)"
        encoder = ["-c:v", "libwebp", "-quality", str(edit.quality), "-bsf:v", timing, "-loop", "0", "-f", "webp"]
    report(0.0, "Encoding")
    _run(
        base + ["-map", f"0:{info.stream_index}", "-vf", filters] + options + encoder + [str(temp)],
        cancel, lambda fraction: report(fraction, "Encoding"), duration,
    )
    return fps


def export_video(
    info: MediaInfo, edit: EditSpec, output: Path, cancel: Event | None = None,
    progress: Callable[[float, str], None] | None = None, overwrite: bool = False,
) -> ExportResult:
    validate_edit(info, edit)
    _check_cancel(cancel)
    output = Path(output).expanduser().absolute()
    resolved = _local_path(output)
    source = _local_path(info.path)
    if resolved == source or (output.exists() and output.samefile(source)):
        raise ValueError("The export cannot replace the source video.")
    if not overwrite and (output.exists() or output.is_symlink()):
        raise FileExistsError(output)

    candidates = tuple(_candidate_edits(edit))
    smallest = None
    temp = _temporary_path(output.parent, f".{edit.format}")
    try:
        for attempt, candidate in enumerate(candidates, 1):
            def report(fraction: float, stage: str) -> None:
                if progress is not None:
                    progress((attempt - 1 + fraction) / len(candidates), stage)

            fps = _encode(info, candidate, temp, cancel, report)
            _check_cancel(cancel)
            size = temp.stat().st_size
            if not size:
                raise RuntimeError("FFmpeg produced an empty output file.")
            smallest = size if smallest is None else min(smallest, size)
            if edit.target_bytes is not None and size > edit.target_bytes:
                continue
            if progress is not None:
                progress((attempt - 1) / len(candidates) + 1 / len(candidates), "Saving")
            _check_cancel(cancel)
            if overwrite:
                os.replace(temp, output)
            elif os.name == "nt":
                os.rename(temp, output)
            else:
                os.link(temp, output)
            width, height = output_dimensions(candidate)
            quality = candidate.quality if edit.format in {"gif", "webp"} else None
            if progress is not None:
                progress(1.0, "Complete")
            return ExportResult(output, size, width, height, fps, quality, attempt)
        assert smallest is not None
        raise TargetSizeError(smallest)
    finally:
        temp.unlink(missing_ok=True)
