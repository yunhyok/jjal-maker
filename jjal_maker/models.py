"""Shared editing state. Crop coordinates use the upright square-pixel image."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    width: int
    height: int
    duration: float
    fps: float
    codec: str = ""
    pix_fmt: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    color_space: str = ""
    color_range: str = ""
    rotation: int = 0
    source_width: int = 0
    source_height: int = 0
    sar: float = 1.0
    stream_index: int = 0
    seek_offset: float = 0.0
    format_name: str = ""


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class TimestampSpec:
    enabled: bool = False
    base: str = "00:00:00"
    placement: str = "overlay"
    font_size: int = 28


@dataclass(frozen=True)
class EditSpec:
    start: float
    end: float
    crop: Crop
    width: int
    height: int
    fps: float = 20.0
    quality: int = 80
    format: str = "webp"
    target_bytes: int | None = None
    playhead: float = 0.0
    timestamp: TimestampSpec = field(default_factory=TimestampSpec)
    lut_path: Path | None = None
    color_mode: str = "auto"


@dataclass(frozen=True)
class RenderCommand:
    args: list[str]
    width: int
    height: int
    fps: float
    position: float


@dataclass(frozen=True)
class ExportResult:
    path: Path
    bytes: int
    width: int
    height: int
    fps: float
    quality: int | None
    attempts: int
