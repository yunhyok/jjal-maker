"""Probe local videos and build the FFmpeg filters used by preview and export."""

import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from .models import EditSpec, MediaInfo, RenderCommand, TimestampSpec


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PROTOCOLS = "file,pipe"
# Demuxers that open a single video file, including all aliases from FFmpeg's format names.
_INPUT_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,mpeg,mxf,asf,asf_o,flv,ogg,nut,apv,h264,hevc"
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_TRANSFER_OVERRIDES = {"pq": "smpte2084", "hlg": "arib-std-b67"}
_TIMESTAMP = re.compile(r"(?:(\d{4}-\d{2}-\d{2}) )?(\d{2}:\d{2}:\d{2})(\.\d{3})?", re.ASCII)
_EPOCH = datetime(1970, 1, 1)


def find_binary(name: str = "ffmpeg") -> str:
    filename = name + ".exe" if os.name == "nt" else name
    dirs = [Path(__file__).resolve().parents[1] / "vendor" / "bin"]
    if bundle := getattr(sys, "_MEIPASS", None):
        dirs.insert(0, Path(bundle) / "bin")
    for directory in dirs:
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)
    if executable := shutil.which(filename):
        return executable
    raise FileNotFoundError(f"{filename} was not found in the app, vendor/bin, or PATH.")


def _local_path(path: str | Path) -> Path:
    path = Path(path).expanduser()
    if path.drive.startswith("\\\\"):
        raise ValueError("Choose a local file.")
    path = path.resolve()
    if path.drive.startswith("\\\\"):
        raise ValueError("Choose a local file.")
    return path


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(Fraction(str(value).replace(":", "/")))
    except (ValueError, ZeroDivisionError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def _matroska_end_time(video: dict, container: dict) -> float | None:
    # FFmpeg's Matroska muxer writes the latest timestamp plus packet duration in this tag.
    encoder = container.get("tags", {}).get("ENCODER", "")
    if "matroska" not in container.get("format_name", "").split(",") or not re.fullmatch(r"Lavf\d+\.\d+\.\d+", encoder):
        return None
    tags = video.get("tags", {})
    if "_STATISTICS_WRITING_APP" in tags:
        return None
    tag = tags.get("DURATION", "")
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d\.\d{9})", tag, re.ASCII)
    if match is None:
        return None
    hours, minutes, seconds = map(_number, match.groups())
    return hours * 3600 + minutes * 60 + seconds


def probe(path: str | Path) -> MediaInfo:
    """Use the first video stream with a codec and image dimensions, excluding cover art."""
    source = _local_path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        result = subprocess.run(
            [find_binary("ffprobe"), "-v", "error", "-protocol_whitelist", _PROTOCOLS,
             "-format_whitelist", _INPUT_FORMATS,
             "-show_streams", "-show_format", "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFprobe did not finish within 20 seconds.") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "FFprobe could not open this file.")
    metadata = json.loads(result.stdout)
    video = next((
        stream for stream in metadata.get("streams", [])
        if stream.get("codec_type") == "video" and stream.get("codec_name") not in (None, "unknown")
        and stream.get("width", 0) > 0 and stream.get("height", 0) > 0
        and not stream.get("disposition", {}).get("attached_pic")
    ), None)
    if video is None:
        raise ValueError("This file has no playable video stream.")

    container = metadata.get("format", {})
    container_start = _number(container.get("start_time"))
    video_start = _number(video.get("start_time"), container_start)
    offset = video_start - container_start
    container_duration = _number(container.get("duration"))
    duration = _number(video.get("duration"))
    if duration <= 0:
        duration = _number(video.get("duration_ts")) * _number(video.get("time_base"))
    if duration <= 0:
        end_time = _matroska_end_time(video, container)
        if end_time is not None and end_time > video_start:
            if container_duration <= 0 or end_time <= container_start + container_duration + 0.001:
                duration = end_time - video_start
    if duration <= 0:
        duration = container_duration - offset
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("This video has no finite, positive duration.")

    rotation = next((
        side["rotation"] for side in video.get("side_data_list", []) if "rotation" in side
    ), video.get("tags", {}).get("rotate", 0))
    rotation = round(_number(rotation)) % 360
    sar = _number(video.get("sample_aspect_ratio"), 1.0)
    if sar <= 0:
        sar = 1.0
    width, height = max(1, round(video["width"] * sar)), video["height"]
    if rotation in (90, 270):
        width, height = height, width
    fps = _number(video.get("avg_frame_rate")) or _number(video.get("r_frame_rate"))
    return MediaInfo(
        path=source, width=width, height=height, duration=duration, fps=fps if fps > 0 else 30.0,
        codec=video["codec_name"], pix_fmt=video.get("pix_fmt", ""),
        color_transfer=video.get("color_transfer", ""), color_primaries=video.get("color_primaries", ""),
        color_space=video.get("color_space", ""), color_range=video.get("color_range", ""),
        rotation=rotation, source_width=video["width"], source_height=video["height"], sar=sar,
        stream_index=video["index"], seek_offset=max(0.0, offset), format_name=container.get("format_name", ""),
    )


def _timestamp_base(spec: TimestampSpec) -> tuple[datetime, bool, bool]:
    match = _TIMESTAMP.fullmatch(spec.base.strip())
    if match:
        date, clock, fraction = match.groups()
        try:
            base = datetime.fromisoformat(f"{date or '2000-01-01'}T{clock}{fraction or ''}")
            return base, date is not None, fraction is not None
        except ValueError:
            pass
    raise ValueError("Enter a timestamp as HH:MM:SS[.mmm] or YYYY-MM-DD HH:MM:SS[.mmm].")


def timestamp_text(spec: TimestampSpec, elapsed: float) -> str:
    base, has_date, has_millis = _timestamp_base(spec)
    current = base + timedelta(seconds=elapsed if has_date else elapsed % 86400)
    clock = current.isoformat(sep=" ", timespec="milliseconds" if has_millis else "seconds")
    return clock if has_date else clock[11:]


def _timestamp_size(edit: EditSpec) -> int:
    spec = edit.timestamp
    # Digits in Segoe UI are narrower than 0.62 em. Leave space for the outline.
    fit_width = (edit.width - 2) / (max(1, len(spec.base.strip())) * 0.62)
    fit_height = edit.height / 1.2 if spec.placement == "overlay" else spec.font_size
    return max(1, math.floor(min(spec.font_size, fit_width, fit_height)))


def _timestamp_min_width(spec: TimestampSpec) -> int:
    return math.ceil(2 + len(spec.base.strip()) * 0.62)


def _band_height(edit: EditSpec) -> int:
    return math.ceil(_timestamp_size(edit) * 1.6) if edit.timestamp.enabled and edit.timestamp.placement == "band" else 0


def output_dimensions(edit: EditSpec) -> tuple[int, int]:
    """Output dimensions include the band below the requested image height, if enabled."""
    return edit.width, edit.height + _band_height(edit)


def validate_edit(info: MediaInfo, edit: EditSpec) -> None:
    if not all(math.isfinite(value) for value in (info.duration, edit.start, edit.end, edit.playhead)):
        raise ValueError("Video duration, trim positions and playhead must be finite.")
    if not 0 <= edit.start < edit.end <= info.duration:
        raise ValueError("Choose a nonempty trim interval inside the video.")
    if not 0 <= edit.playhead <= info.duration:
        raise ValueError("The playhead must be inside the video.")
    crop = edit.crop
    if (not all(type(value) is int for value in (crop.x, crop.y, crop.width, crop.height))
            or crop.x < 0 or crop.y < 0 or crop.width < 1 or crop.height < 1
            or crop.x + crop.width > info.width or crop.y + crop.height > info.height):
        raise ValueError("Choose a nonempty crop inside the upright video.")
    if edit.format not in {"gif", "webp", "png", "apng"}:
        raise ValueError("Choose GIF, WebP, PNG or APNG.")
    if (type(edit.width) is not int or type(edit.height) is not int
            or edit.width < 1 or edit.height < 1):
        raise ValueError("Output dimensions must be positive integers.")
    if edit.format == "gif" and not 1 <= edit.fps <= 50:
        raise ValueError("Choose a GIF frame rate between 1 and 50 FPS.")
    if not 1 <= edit.fps <= 60:
        raise ValueError("Choose an output frame rate between 1 and 60 FPS.")
    if type(edit.quality) is not int or not 0 <= edit.quality <= 100:
        raise ValueError("Quality must be an integer from 0 to 100.")
    if edit.target_bytes is not None and (type(edit.target_bytes) is not int or edit.target_bytes < 1):
        raise ValueError("The target size must be a positive number of bytes.")
    if edit.color_mode not in {"auto", "sdr", "pq", "hlg"}:
        raise ValueError("Choose automatic, SDR, PQ or HLG color conversion.")
    if edit.lut_path is not None:
        lut = _local_path(edit.lut_path)
        if lut.suffix.lower() != ".cube" or not lut.is_file():
            raise ValueError("Choose an existing .cube LUT file.")
    if edit.timestamp.enabled:
        if edit.timestamp.placement not in {"overlay", "band"}:
            raise ValueError("Choose an overlay or a black band for the timestamp.")
        if type(edit.timestamp.font_size) is not int or edit.timestamp.font_size < 1:
            raise ValueError("The timestamp font size must be a positive integer.")
        base, has_date, _ = _timestamp_base(edit.timestamp)
        if edit.width < _timestamp_min_width(edit.timestamp):
            raise ValueError("Increase output width to make room for the timestamp.")
        try:
            if has_date:
                first = base + timedelta(seconds=min(0.0, edit.playhead - edit.start) if edit.format == "png" else 0.0)
                duration = max(edit.end - edit.start, edit.playhead - edit.start) if edit.format == "png" else edit.end - edit.start
                last = base + timedelta(seconds=duration)
                if first < _EPOCH or last >= datetime(3001, 1, 1):
                    raise ValueError("Dated timestamps must stay between the years 1970 and 3000.")
        except OverflowError as exc:
            raise ValueError("The timestamp runs past the supported calendar date.") from exc
    if edit.format == "webp" and max(output_dimensions(edit)) > 16383:
        raise ValueError("WebP output dimensions cannot exceed 16,383 pixels.")
    if edit.format == "gif" and max(output_dimensions(edit)) > 65535:
        raise ValueError("GIF output dimensions cannot exceed 65,535 pixels.")


def _secs(seconds: float) -> str:
    return f"{seconds:.6f}"


def _filter_value(value: str) -> str:
    # One escape pass for the named filter option and one for its enclosing graph.
    for special in ("\\':", "\\'[],;"):
        value = "".join(("\\" if char in special else "") + char for char in value)
    return value


def _font_option() -> str:
    if os.name == "nt":
        font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
        if not font.is_file():
            raise FileNotFoundError("Segoe UI was not found in the Windows Fonts directory.")
        return f"fontfile={_filter_value(font.as_posix())}"
    return "font=Sans"


def _timestamp_filters(edit: EditSpec, elapsed: float) -> list[str]:
    spec = edit.timestamp
    size = _timestamp_size(edit)
    margin = max(1, round(size / 4))
    band = _band_height(edit)
    filters = [f"pad=iw:ih+{band}:0:0:black"] if band else []
    y = f"h-{band}+({band}-text_h)/2" if band else f"max(0,h-text_h-{margin})"
    base, has_date, has_millis = _timestamp_base(spec)
    pattern = r"%H\:%M\:%S"
    if has_date:
        pattern = "%Y-%m-%d " + pattern
    seconds = (base - _EPOCH).total_seconds() + (elapsed if has_date else elapsed % 86400)
    stamp = f"%{{pts:gmtime:{_secs(seconds)}:{pattern}}}"
    if has_millis:
        # PTS gmtime uses whole seconds on Windows. Render the fraction from the same PTS.
        fraction = (base.microsecond / 1_000_000 + elapsed) % 1
        stamp += f".%{{eif:mod(floor((t+{_secs(fraction)})*1000+0.0001),1000):d:3}}"
    border = f":borderw={max(1, size // 16)}:bordercolor=black" if not band else ""
    filters.append(
        f"drawtext={_font_option()}:text={_filter_value(stamp)}:fontsize={size}:"
        f"fontcolor=white:x=(w-text_w)/2:y={_filter_value(y)}{border}"
    )
    return filters


def _color_filters(info: MediaInfo, edit: EditSpec) -> list[str]:
    filters = []
    transfer = _TRANSFER_OVERRIDES.get(edit.color_mode, info.color_transfer if edit.color_mode == "auto" else "")
    if transfer in _HDR_TRANSFERS:
        rgb = info.pix_fmt.startswith(("rgb", "bgr", "gbr"))
        matrix = info.color_space if info.color_space not in {"", "unknown", "unspecified"} else ("gbr" if rgb else "bt2020nc")
        primaries = info.color_primaries if info.color_primaries not in {"", "unknown", "unspecified"} else "bt2020"
        range_in = "full" if info.color_range == "pc" or rgb else "limited"
        filters.extend((
            f"zscale=tin={transfer}:pin={primaries}:min={matrix}:rin={range_in}:t=linear:npl=100",
            "format=gbrpf32le", "zscale=p=bt709", "tonemap=tonemap=hable:desat=0",
            "zscale=t=iec61966-2-1:m=gbr:r=full", "format=gbrp16le",
        ))
    if edit.lut_path is not None:
        lut = _filter_value(_local_path(edit.lut_path).as_posix())
        filters.extend(("format=gbrp16le", f"lut3d=file={lut}:interp=tetrahedral"))
    return filters


def _input_args(info: MediaInfo, position: float, *, playing: bool = False) -> list[str]:
    args = [
        find_binary(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2",
        "-protocol_whitelist", _PROTOCOLS, "-format_whitelist", _INPUT_FORMATS,
    ]
    if info.format_name != "mpegts":
        if playing:
            args.extend(("-readrate", "1"))
        args.extend(("-noaccurate_seek", "-ss", _secs(position + info.seek_offset)))
    args.extend(("-i", str(_local_path(info.path))))
    return args


def _frame_count(duration: float, fps: float) -> int:
    return max(1, math.ceil(duration * fps - 1e-9))


def _animation_timing(edit: EditSpec) -> tuple[float, int]:
    duration = edit.end - edit.start
    frames = _frame_count(duration, edit.fps)
    if frames == 1 and edit.format in {"apng", "webp"}:
        # The muxers write a static image with only one packet.
        return 2 / max(duration, 0.000002), 2
    return edit.fps, frames


def _filters(
    info: MediaInfo, edit: EditSpec, position: float, end: float, *,
    applied: bool, still: bool, fps: float, frames: int,
) -> str:
    length = end - position
    if still:
        # Preserve the prior decoded frame when the seek is between source timestamps.
        filters = ["tpad=stop_mode=clone:stop_duration=1", "fps=25:start_time=0:round=up", "trim=end_frame=1"]
    else:
        filters = [
            # Keep seek preroll until CFR sampling so the first frame can overlap the cut.
            f"trim=end={_secs(length)}", "tpad=stop_mode=clone:stop_duration=1",
            f"fps={fps:.12g}:start_time=0:round=up:eof_action=pass",
            "tpad=stop=-1:stop_mode=clone", f"trim=end_frame={frames}",
        ]
    if info.format_name == "mpegts":
        # Timestamp seeks in transport streams can land on a packet inside the GOP.
        filters.insert(0, f"setpts=PTS-STARTPTS-{_secs(position)}/TB")
    filters.extend(_color_filters(info, edit))
    filters.extend((f"scale={info.width}:{info.height}:flags=lanczos", "setsar=1", "format=rgb24"))
    if applied:
        crop = edit.crop
        filters.extend((
            f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}:exact=1",
            f"scale={edit.width}:{edit.height}:flags=lanczos", "setsar=1",
        ))
        if edit.timestamp.enabled:
            filters.extend(_timestamp_filters(edit, position - edit.start))
    return ",".join(filters)


def _inside(position: float, start: float, end: float) -> float:
    return max(start, min(position, end - 0.000001))


def preview_command(
    info: MediaInfo, edit: EditSpec, position: float, result: bool = False,
    playing: bool = False, max_width: int = 960, max_height: int = 540,
) -> RenderCommand:
    validate_edit(info, edit)
    if not math.isfinite(position) or max_width < 1 or max_height < 1:
        raise ValueError("Preview position and dimensions must be valid.")
    use_trim = result and (playing or edit.format != "png")
    start, end = (edit.start, edit.end) if use_trim else (0.0, info.duration)
    position = _inside(position, start, end)
    fps = edit.fps
    if result and edit.format != "png":
        fps, total_frames = _animation_timing(edit)
        frame = min(total_frames - 1, math.floor((position - start) * fps + 1e-9))
        position = start + frame / fps
        frames = total_frames - frame
    else:
        frames = _frame_count(end - position, fps)
    width, height = output_dimensions(edit) if result else (info.width, info.height)
    factor = min(1.0, max_width / width, max_height / height)
    width, height = max(1, round(width * factor)), max(1, round(height * factor))
    filters = _filters(
        info, edit, position, end, applied=result, still=not playing,
        fps=fps, frames=frames,
    )
    filters += f",scale={width}:{height}:flags=lanczos,format=rgb24"
    if playing:
        # Input pacing alone can burst frames from seek preroll and the trim tail.
        filters += ",realtime"
    args = _input_args(info, position, playing=playing)
    args.extend((
        "-map", f"0:{info.stream_index}", "-an", "-sn", "-dn",
        "-vf", filters,
    ))
    if not playing:
        args.extend(("-frames:v", "1"))
    args.extend(("-fps_mode", "passthrough", "-threads", "2", "-f", "rawvideo", "pipe:1"))
    return RenderCommand(args, width, height, fps, position)
