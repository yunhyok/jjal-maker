from dataclasses import replace
import os
from pathlib import Path
import subprocess
import time

from PIL import Image
import pytest

from jjal_maker.export import export_video
from jjal_maker.media import (
    CREATE_NO_WINDOW,
    _filter_value,
    _local_path,
    find_binary,
    preview_command,
    probe,
    timestamp_text,
    validate_edit,
)
from jjal_maker.models import Crop, EditSpec, TimestampSpec


def _ffmpeg(*args: str | Path) -> None:
    try:
        ffmpeg = find_binary()
        find_binary("ffprobe")
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", *(str(arg) for arg in args)],
        check=True, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )


def _render(command) -> Image.Image:
    completed = subprocess.run(
        command.args, check=True, capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW,
    )
    assert not completed.stderr
    assert len(completed.stdout) == command.width * command.height * 3
    return Image.frombytes("RGB", (command.width, command.height), completed.stdout)


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "영상의 [색상], sample's.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=160x112:r=20:d=1.2",
        "-c:v", "libx264", "-threads", "2", source,
    )
    info = probe(source)
    return info, EditSpec(0.113, 0.804, Crop(1, 3, 99, 93), 99, 93)


def test_probe_and_render_upright_anamorphic_video(tmp_path):
    base = tmp_path / "anamorphic.mp4"
    rotated = tmp_path / "rotated.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=blue:s=80x40:r=10:d=0.5",
        "-vf", "setsar=2,drawbox=x=0:y=0:w=40:h=40:color=red:t=fill",
        "-c:v", "libx264", "-threads", "2", base,
    )
    _ffmpeg("-display_rotation:v:0", "90", "-i", base, "-c", "copy", rotated)
    info = probe(rotated)
    assert (info.width, info.height, info.sar, info.rotation) == (40, 160, 2.0, 90)
    edit = EditSpec(0, info.duration, Crop(0, 0, info.width, info.height), info.width, info.height)
    picture = _render(preview_command(info, edit, 0))
    assert picture.getpixel((20, 20))[2] > 230
    assert picture.getpixel((20, 140))[0] > 230


def test_video_start_is_relative_to_first_video_stream(tmp_path):
    source = tmp_path / "audio starts first.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-itsoffset", "0.3", "-f", "lavfi", "-i", "color=red:s=80x40:r=10:d=0.6",
        "-map", "0:a", "-map", "1:v", "-c:a", "aac", "-c:v", "libx264",
        "-threads", "2", "-fps_mode", "passthrough", source,
    )
    info = probe(source)
    assert info.stream_index == 1
    assert info.duration == pytest.approx(0.6)
    assert info.seek_offset == pytest.approx(0.3)
    edit = EditSpec(0, info.duration, Crop(0, 0, info.width, info.height), info.width, info.height)
    assert _render(preview_command(info, edit, 0)).getpixel((20, 20))[0] > 230


@pytest.mark.parametrize("extension", ["mkv", "ts"])
def test_delayed_video_uses_its_own_duration_and_samples_nonzero_time(tmp_path, extension):
    source = tmp_path / f"audio outlasts video.{extension}"
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-itsoffset", "0.7", "-f", "lavfi", "-i",
        "color=red:s=100x80:r=20:d=2,drawbox=c=blue:t=fill:enable='gte(t,0.5)'",
        "-map", "0:a", "-map", "1:v", "-c:a", "aac", "-c:v", "libx264",
        "-threads", "2", "-fps_mode", "passthrough", source,
    )
    info = probe(source)
    assert info.duration == pytest.approx(2)
    assert info.seek_offset == pytest.approx(0.7, abs=0.03)
    edit = EditSpec(0, info.duration, Crop(0, 0, 100, 80), 100, 80, format="png")
    assert _render(preview_command(info, edit, 0.2, result=True)).getpixel((20, 20))[0] > 230
    assert _render(preview_command(info, edit, 0.6, result=True)).getpixel((20, 20))[2] > 230
    assert _render(preview_command(info, edit, 2, result=True)).getpixel((20, 20))[2] > 230
    exported = export_video(info, replace(edit, playhead=0.6), tmp_path / "still.png")
    with Image.open(exported.path) as still:
        assert still.getpixel((20, 20))[2] > 230


@pytest.mark.parametrize(("kind", "suffix"), [("concat", "mp4"), ("hls", "mp4"), ("hls", "m3u8")])
def test_playlist_rejected_by_probe_and_render(sample, tmp_path, kind, suffix):
    info, edit = sample
    segment = tmp_path / "segment.ts"
    _ffmpeg("-i", info.path, "-c", "copy", segment)
    source = tmp_path / f"playlist.{suffix}"
    manifest = (
        "ffconcat version 1.0\nfile segment.ts\n" if kind == "concat"
        else "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1.2,\nsegment.ts\n#EXT-X-ENDLIST\n"
    )
    source.write_text(manifest, encoding="ascii")
    with pytest.raises(RuntimeError, match="(?i)whitelist|not detecting m3u8"):
        probe(source)

    command = preview_command(replace(info, path=source), edit, 0, result=True)
    completed = subprocess.run(
        command.args, capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW,
    )
    assert completed.returncode
    assert any(error in completed.stderr.lower() for error in (b"whitelist", b"not detecting m3u8"))
    assert not completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="UNC drive syntax is specific to Windows")
def test_unc_rejected_before_path_resolution(monkeypatch):
    def unexpected_filesystem_lookup(*_args, **_kwargs):
        pytest.fail("UNC path reached filesystem lookup")

    monkeypatch.setattr(type(Path()), "resolve", unexpected_filesystem_lookup)
    monkeypatch.setattr(type(Path()), "is_file", unexpected_filesystem_lookup)
    with pytest.raises(ValueError, match="local file"):
        _local_path(r"\\server\share\test.mp4")


def test_preview_end_and_realtime_partial_selection(sample):
    info, edit = sample
    last = preview_command(info, edit, info.duration)
    assert last.position < info.duration
    assert _render(last).size == (info.width, info.height)

    start = 0.401
    command = preview_command(info, edit, start, result=True, playing=True, max_width=64, max_height=64)
    before = time.monotonic()
    completed = subprocess.run(
        command.args, check=True, capture_output=True, timeout=7, creationflags=CREATE_NO_WINDOW,
    )
    assert time.monotonic() - before >= 0.25
    assert command.position == pytest.approx(0.363)
    assert len(completed.stdout) == 9 * command.width * command.height * 3


@pytest.mark.parametrize(("base", "expected"), [
    ("23:59:59.900", "00:00:00.050"),
    ("2024-02-29 23:59:59.900", "2024-03-01 00:00:00.050"),
    ("2024-12-31 23:59:59", "2024-12-31 23:59:59"),
])
@pytest.mark.parametrize("placement", ["overlay", "band"])
def test_timestamp_renders_real_visible_glyphs_after_rollover(sample, base, expected, placement):
    info, edit = sample
    spec = TimestampSpec(True, base, placement, 20)
    edit = replace(edit, timestamp=spec)
    assert timestamp_text(spec, 0.15) == expected
    command = preview_command(info, edit, edit.start + 0.15, result=True)
    dynamic = _render(command)
    if placement == "band":
        band = dynamic.crop((0, edit.height, command.width, command.height))
        bounds = band.getbbox()
        assert bounds is not None
        assert abs((bounds[0] + bounds[2]) / 2 - command.width / 2) < 2
    else:
        plain = _render(preview_command(info, replace(edit, timestamp=TimestampSpec()), edit.start + 0.15, result=True))
        assert plain.tobytes() != dynamic.tobytes()

    filters_index = command.args.index("-vf") + 1
    graph = command.args[filters_index]
    start = graph.index(":text=") + len(":text=")
    end = graph.index(":fontsize=", start)
    command.args[filters_index] = graph[:start] + _filter_value(expected) + graph[end:]
    assert _render(command).tobytes() == dynamic.tobytes()


def test_lut_path_accepts_filter_punctuation(sample, tmp_path):
    info, edit = sample
    lut = tmp_path / "색상 [green, bright]; John's.cube"
    lut.write_text("LUT_3D_SIZE 2\n" + "0 1 0\n" * 8, encoding="ascii")
    edit = replace(edit, lut_path=lut)
    picture = _render(preview_command(info, edit, 0.4, result=True))
    red, green, blue = picture.getpixel((20, 20))
    assert red <= 1 and green >= 254 and blue <= 1


@pytest.mark.parametrize(("transfer", "value"), [("smpte2084", 16), ("arib-std-b67", 18)])
def test_tagged_hdr_uses_tone_mapping(sample, tmp_path, transfer, value):
    _, edit = sample
    source = tmp_path / "hdr.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=160x112:r=10:d=1",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-threads", "2",
        "-x265-params", "pools=none:log-level=error",
        "-bsf:v", f"hevc_metadata=colour_primaries=9:transfer_characteristics={value}:matrix_coefficients=9",
        source,
    )
    info = probe(source)
    assert info.codec == "hevc" and info.color_transfer == transfer
    tone_mapped = _render(preview_command(info, edit, 0.4, result=True))
    bypassed = _render(preview_command(info, replace(edit, color_mode="sdr"), 0.4, result=True))
    assert tone_mapped.tobytes() != bypassed.tobytes()


def test_invalid_trim_crop_and_timestamp_are_rejected(sample):
    info, edit = sample
    for invalid in (
        replace(edit, end=float("nan")),
        replace(edit, end=edit.start),
        replace(edit, crop=Crop(1, 1, info.width, info.height)),
        replace(edit, format="gif", fps=51),
        replace(edit, timestamp=TimestampSpec(True, "2025-02-29 00:00:00")),
        replace(edit, timestamp=TimestampSpec(True, "1969-12-31 23:59:59")),
    ):
        with pytest.raises(ValueError):
            validate_edit(info, invalid)
