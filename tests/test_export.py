from dataclasses import replace
import subprocess
from threading import Event, Timer
import errno

from PIL import Image
import pytest

from jjal_maker import export
from jjal_maker.media import CREATE_NO_WINDOW, find_binary, output_dimensions, preview_command, probe
from jjal_maker.models import Crop, EditSpec, TimestampSpec


@pytest.fixture
def clip(tmp_path):
    try:
        ffmpeg = find_binary()
        find_binary("ffprobe")
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    source = tmp_path / "원본 영상.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error",
         "-f", "lavfi", "-i", "color=red:s=160x112:r=10:d=0.4",
         "-f", "lavfi", "-i", "color=blue:s=160x112:r=10:d=0.8",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-threads", "2", source],
        check=True, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    info = probe(source)
    return info, EditSpec(0.113, 0.804, Crop(1, 3, 119, 93), 119, 93, playhead=0.55)


@pytest.mark.parametrize("fmt", ["gif", "webp", "png", "apng"])
def test_export_dimensions_transitions_loop_and_delays(clip, tmp_path, fmt):
    info, edit = clip
    spec = TimestampSpec(True, "23:59:59.900", "band", 14)
    edit = replace(edit, format=fmt, timestamp=spec)
    output = tmp_path / f"result.{fmt}"
    result = export.export_video(info, edit, output)
    assert result.bytes == output.stat().st_size
    assert (result.width, result.height) == output_dimensions(edit)
    assert result.attempts == 1
    with Image.open(output) as image:
        assert image.size == (result.width, result.height)
        assert image.convert("RGB").crop((0, edit.height, result.width, result.height)).getbbox()
        if fmt == "png":
            assert image.n_frames == 1
            assert image.convert("RGB").getpixel((40, 40))[2] > 220
        else:
            assert image.n_frames == 14
            assert image.info["loop"] == 0
            assert image.convert("RGB").getpixel((40, 40))[0] > 220
            elapsed = 0
            for frame in range(image.n_frames):
                image.seek(frame)
                image.load()
                elapsed += image.info["duration"]
            assert elapsed == pytest.approx(691, abs=5 if fmt == "gif" else 1)
            assert image.convert("RGB").getpixel((40, 40))[2] > 220


def test_png_uses_the_playhead_outside_the_selection(clip, tmp_path):
    info, edit = clip
    edit = replace(edit, format="png", start=0.5, playhead=0.2)
    result = export.export_video(info, edit, tmp_path / "still.png")
    preview = preview_command(info, edit, edit.playhead, result=True)
    completed = subprocess.run(
        preview.args, check=True, capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW,
    )
    with Image.open(result.path) as still:
        assert still.getpixel((40, 40))[0] > 220
        assert still.convert("RGB").tobytes() == completed.stdout


def test_result_scrub_matches_export_sample_grid(clip, tmp_path):
    info, edit = clip
    edit = replace(edit, format="apng", start=0, end=info.duration, fps=1)
    result = export.export_video(info, edit, tmp_path / "sampled.apng")
    with Image.open(result.path) as image:
        assert image.n_frames == 2
        for position, frame in ((0.55, 0), (0.99, 0), (1.01, 1)):
            command = preview_command(info, edit, position, result=True)
            assert command.position == frame
            rendered = subprocess.run(
                command.args, check=True, capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW,
            ).stdout
            image.seek(frame)
            assert image.convert("RGB").tobytes() == rendered
        assert image.convert("RGB").getpixel((40, 40))[2] > 220


def test_short_trim_keeps_the_overlapping_source_frame_and_animates(clip, tmp_path):
    info, edit = clip
    edit = replace(edit, start=1.109, end=1.150, format="webp")
    result = export.export_video(info, edit, tmp_path / "short.webp")
    with Image.open(result.path) as image:
        assert image.n_frames == 2 and image.info["loop"] == 0
        elapsed = 0
        for frame in range(image.n_frames):
            image.seek(frame)
            image.load()
            elapsed += image.info["duration"]
        assert elapsed == 41
        assert image.convert("RGB").getpixel((40, 40))[2] > 220


def test_lossless_target_enforces_limit_by_resizing(clip, tmp_path):
    info, edit = clip
    edit = replace(edit, format="apng")
    baseline = export.export_video(info, edit, tmp_path / "baseline.apng")
    target = baseline.bytes * 3 // 4
    result = export.export_video(info, replace(edit, target_bytes=target), tmp_path / "within.apng")
    assert result.bytes == result.path.stat().st_size <= target
    assert result.width < baseline.width and result.quality is None
    assert 1 < result.attempts <= 12
    with Image.open(result.path) as image:
        elapsed = 0
        for frame in range(image.n_frames):
            image.seek(frame)
            image.load()
            elapsed += image.info["duration"]
        assert elapsed == pytest.approx(691, abs=1)


def test_target_failure_and_late_collision_preserve_destination(clip, tmp_path):
    info, edit = clip
    output = tmp_path / "existing.webp"
    output.write_bytes(b"existing user file")
    with pytest.raises(export.TargetSizeError) as error:
        export.export_video(info, replace(edit, target_bytes=1), output, overwrite=True)
    assert error.value.smallest_bytes > 1
    assert output.read_bytes() == b"existing user file"
    assert not list(tmp_path.glob(".jjal-*"))

    competitor = tmp_path / "winner.webp"

    def other_writer(_fraction, stage):
        if stage == "Saving":
            competitor.write_bytes(b"other writer won")

    with pytest.raises(FileExistsError):
        export.export_video(info, edit, competitor, progress=other_writer)
    assert competitor.read_bytes() == b"other writer won"
    assert not list(tmp_path.glob(".jjal-*"))


def test_cancel_in_flight_and_reject_source_replacement(clip, tmp_path, monkeypatch):
    info, edit = clip
    original = info.path.read_bytes()
    with pytest.raises(ValueError, match="source video"):
        export.export_video(info, edit, info.path, overwrite=True)
    assert info.path.read_bytes() == original

    output = tmp_path / "existing.webp"
    output.write_bytes(b"prior output")
    input_args = export._input_args

    def paced_input(*args):
        return input_args(*args, playing=True)

    monkeypatch.setattr(export, "_input_args", paced_input)
    cancel = Event()
    timer = Timer(0.15, cancel.set)

    def start_timer(fraction, stage):
        if (fraction, stage) == (0.0, "Encoding"):
            timer.start()

    try:
        with pytest.raises(export.ExportCancelled):
            export.export_video(info, edit, output, cancel=cancel, progress=start_timer, overwrite=True)
    finally:
        timer.cancel()
        timer.join()
    assert output.read_bytes() == b"prior output"
    assert not list(tmp_path.glob(".jjal-*"))


def test_vfr_keeps_real_transition_time(clip, tmp_path):
    info, _ = clip
    source = tmp_path / "vfr.mp4"
    subprocess.run(
        [find_binary(), "-v", "error", "-i", str(info.path),
         "-vf", "select='eq(n,0)+eq(n,2)+eq(n,4)+eq(n,8)'",
         "-fps_mode", "vfr", "-c:v", "libx264", "-threads", "2", str(source)],
        check=True, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    info = probe(source)
    edit = EditSpec(0.1, 0.7, Crop(0, 0, 160, 112), 160, 112, fps=10)
    result = export.export_video(info, edit, tmp_path / "vfr.webp")
    with Image.open(result.path) as picture:
        assert picture.n_frames == 6
        for frame in range(6):
            picture.seek(frame)
            picture.load()
            color = picture.convert("RGB").getpixel((40, 40))
            assert color[0 if frame < 3 else 2] > 220
            assert picture.info["duration"] == 100


def test_publish_io_failure_preserves_previous_file(clip, tmp_path, monkeypatch):
    info, edit = clip
    output = tmp_path / "existing.webp"
    output.write_bytes(b"previous valid user file")

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "simulated disk full")

    monkeypatch.setattr(export.os, "replace", full_disk)
    with pytest.raises(OSError, match="disk full"):
        export.export_video(info, edit, output, overwrite=True)
    assert output.read_bytes() == b"previous valid user file"
    assert not list(tmp_path.glob(".jjal-*"))
