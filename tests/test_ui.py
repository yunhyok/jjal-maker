"""Offscreen checks for the desktop editor's input and background jobs."""

import os
import sys
import time
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from jjal_maker import export, media
from jjal_maker.models import Crop, ExportResult, MediaInfo, RenderCommand
from jjal_maker.preview import PreviewCanvas, PreviewStream, Timeline
from jjal_maker.ui import MainWindow


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def wait_until(app, condition):
    until = time.monotonic() + 8
    while not condition() and time.monotonic() < until:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    monkeypatch.setattr(window, "_schedule_preview", lambda: None)
    window.show()
    yield window
    window.close()
    wait_until(app, lambda: not window.isVisible())


def drag(widget, start, end):
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(*start))
    QTest.mouseMove(widget, QPoint(*end))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(*end))


def test_crop_maps_letterbox_and_bounds_move_resize(app):
    canvas = PreviewCanvas()
    canvas.resize(400, 400)
    canvas.set_source(1000, 500)
    image = QImage(400, 200, QImage.Format.Format_RGB888)
    image.fill(QColor("white"))
    canvas.set_image(image)
    canvas.show()
    app.processEvents()
    assert canvas.source_point(QPointF(200, 100)) == QPointF(500, 0)
    assert canvas.source_point(QPointF(200, 300)) == QPointF(500, 500)

    canvas.set_aspect((1, 1))
    canvas.new_crop = True
    drag(canvas, (60, 130), (180, 180))
    assert canvas.crop == Crop(150, 75, 125, 125)
    drag(canvas, (85, 155), (399, 299))
    assert canvas.crop == Crop(875, 375, 125, 125)
    drag(canvas, (350, 250), (310, 216))
    assert canvas.crop == Crop(790, 290, 210, 210)
    canvas.set_aspect((16, 9))
    canvas.new_crop = True
    drag(canvas, (50, 150), (51, 151))
    assert (canvas.crop.width, canvas.crop.height) == (16, 9)
    canvas.close()


def test_timeline_drag_clamps_trim_and_seek(app):
    timeline = Timeline()
    timeline.resize(600, 64)
    timeline.set_duration(1000)
    timeline.set_selection(100, 800)
    trims, seeks = [], []
    timeline.trimRequested.connect(lambda start, end: trims.append((start, end)))
    timeline.seekRequested.connect(seeks.append)
    timeline.show()
    app.processEvents()
    drag(timeline, (round(timeline._x(100)), 20), (590, 20))
    assert trims[-1] == (799, 800)
    drag(timeline, (round(timeline._x(800)), 20), (5, 20))
    assert trims[-1] == (100, 101)
    drag(timeline, (round(timeline._x(250)), 42), (590, 42))
    assert seeks[-1] == 1000
    QTest.keyClick(timeline, Qt.Key.Key_Home)
    assert seeks[-1] == 0
    timeline.close()


def test_language_and_controls_preserve_edit_state(window, tmp_path):
    assert window.windowTitle() == "짤방 메이커"
    assert window.open_button.isEnabled()
    assert not any(widget.isEnabled() for widget in (window.play, window.export_button, window.settings_scroll))

    window._apply_source(MediaInfo(tmp_path / "입력.mp4", 320, 200, 3.125, 30))
    window.in_box.setValue(400)
    window.out_box.setValue(1400)
    window.canvas.set_crop(Crop(10, 20, 240, 160))
    window.scale.setCurrentIndex(window.scale.findData("custom"))
    window.width_box.setValue(120)
    window.timestamp_enabled.setChecked(True)
    window.timestamp_base.setText("2026-11-17 14:20:50.123")
    window.timestamp_position.setCurrentIndex(window.timestamp_position.findData("band"))
    window.compression.setCurrentIndex(window.compression.findData("target"))
    window.target_mb.setValue(1.25)
    window.result_view.click()
    window._seek(555)
    window._frame_ready(QImage(), 0.550)
    assert window.timeline.position == 555
    assert "00:00:00.550" in window.clock.toolTip()
    window.format_box.setCurrentIndex(window.format_box.findData("png"))
    window._seek(2200)
    window._frame_ready(QImage(), 2.199)
    edit = window.edit_spec()
    assert (edit.start, edit.end, edit.playhead) == (0.4, 1.4, 2.2)
    assert (edit.width, edit.height, edit.target_bytes) == (120, 80, 1_250_000)
    assert f"120 × {media.output_dimensions(edit)[1]} px" == window.output_size.text()
    assert not window.fps_box.isEnabled()

    window.language_box.setCurrentIndex(window.language_box.findData("en"))
    assert window.windowTitle() == "Jjal Maker"
    assert window._settings.value("language") == "en"
    assert window.edit_spec() == edit
    assert window.result_view.isChecked()
    window.timestamp_base.setText("49:00:00")
    assert window._validated_edit() is None
    window.language_box.setCurrentIndex(window.language_box.findData("ko"))
    assert window.status.text().startswith("타임스탬프가 올바르지 않습니다.")
    detail = "x" * 400 + "\nMore detail"
    window._preview_failed(detail)
    assert len(window.status.text()) < 200
    assert detail in window.status.toolTip()


def test_latest_probe_wins_and_close_waits_for_workers(app, window, tmp_path, monkeypatch):
    old_started, close_started, old_release, close_release = (Event() for _ in range(4))
    sources = [tmp_path / f"{name}.mp4" for name in ("old", "new", "close")]
    for source in sources:
        source.touch()

    def probe(path):
        if path == sources[0]:
            old_started.set()
            old_release.wait(8)
        elif path == sources[2]:
            close_started.set()
            close_release.wait(8)
        return MediaInfo(path, 100, 100, 1.5, 25)

    monkeypatch.setattr(media, "probe", probe)
    try:
        window.open_path(str(sources[0]))
        wait_until(app, old_started.is_set)
        window.open_path(str(sources[1]))
        wait_until(app, lambda: window.info is not None and window.info.path == sources[1])
        old_release.set()
        wait_until(app, lambda: not window._jobs)
        assert window.info.path == sources[1]
        window.open_path(str(sources[2]))
        wait_until(app, close_started.is_set)
        assert not window.close()
        assert window.isVisible()
        close_release.set()
        wait_until(app, lambda: not window.isVisible())
        assert not window._jobs
        assert window.info.path == sources[1]
    finally:
        old_release.set()
        close_release.set()


def test_preview_stream_ignores_replaced_command_and_buffers_split_frames(app):
    stream = PreviewStream()
    pixels, errors = [], []
    stream.frameReady.connect(lambda image, position: pixels.append((image.pixelColor(0, 0).name(), position)))
    stream.failed.connect(errors.append)
    old = RenderCommand([sys.executable, "-c", "import time; time.sleep(4)"], 1, 1, 20, 0)
    new = RenderCommand(
        [sys.executable, "-c",
         "import sys,time; out=sys.stdout.buffer; out.write(bytes([255,0,0,0])); out.flush(); "
         "time.sleep(.06); out.write(bytes([255,0])); out.flush()"],
        1, 1, 20, 1,
    )
    try:
        stream.start(old, False)
        stream.start(new, False)
        wait_until(app, lambda: not stream.busy)
        assert not errors
        assert pixels[-1] == ("#00ff00", 1.05)
    finally:
        stream.stop()
        wait_until(app, lambda: not stream.busy)


def test_background_export_reports_result_and_cancels(app, window, tmp_path, monkeypatch):
    window._apply_source(MediaInfo(tmp_path / "source.mp4", 64, 48, 1, 30))
    output = tmp_path / "out.webp"

    def succeeds(info, edit, destination, cancel, progress, overwrite):
        progress(0.5, "Encoding")
        return ExportResult(Path(destination), 4096, edit.width, edit.height, edit.fps, edit.quality, 2)

    monkeypatch.setattr(export, "export_video", succeeds)
    window._start_export(window.edit_spec(), output, False)
    wait_until(app, lambda: not window._jobs)
    assert "4,096 B" in window.status.text()
    assert "64 × 48 px" in window.status.text()
    assert "시도 2회" in window.status.text()
    assert window.folder_button.isVisible()

    def cancels(info, edit, destination, cancel, progress, overwrite):
        assert cancel.wait(8)
        raise export.ExportCancelled()

    monkeypatch.setattr(export, "export_video", cancels)
    window._start_export(window.edit_spec(), output, False)
    window.cancel_button.click()
    wait_until(app, lambda: not window._jobs)
    assert window.status.text() == "내보내기를 취소했습니다."


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC paths")
def test_unc_drop_rejected_before_filesystem_access(window, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("UNC path reached the filesystem")

    monkeypatch.setattr(Path, "is_file", forbidden)
    window.open_path(r"\\synthetic-review-host\share\clip.mp4")
    assert not window._jobs
    assert window.info is None


def test_gif_fps_control_caps_at_fifty(window, tmp_path):
    window._apply_source(MediaInfo(tmp_path / "source.mp4", 320, 200, 3.0, 60))
    window.fps_box.setValue(60)
    window.format_box.setCurrentIndex(window.format_box.findData("gif"))
    assert window.fps_box.value() == 50
    window.format_box.setCurrentIndex(window.format_box.findData("webp"))
    assert window.fps_box.maximum() == 60
