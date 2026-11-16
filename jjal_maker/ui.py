"""Desktop editor and background media/export work."""

import math
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSettings, QSignalBlocker, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from . import export, media
from .i18n import translate
from .models import EditSpec, ExportResult, MediaInfo, TimestampSpec
from .preview import PreviewCanvas, PreviewStream, Timeline, format_clock


STYLE = """
QMainWindow, QWidget#editor, QWidget#settings { background: #1b2026; color: #eef0f2; }
QWidget { color: #eef0f2; font-size: 13px; }
QLabel#brand { font-size: 25px; font-weight: 700; }
QLabel#section { font-size: 14px; font-weight: 650; }
QLabel#muted { color: #a6b1bb; }
QFrame#panel { background: #242a32; border: 1px solid #353e48; border-radius: 10px; }
QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
  background: #303944; border: 1px solid #495462; border-radius: 5px; padding: 4px 8px;
  min-height: 19px; selection-background-color: #276057;
}
QPushButton:hover { background: #3b4854; }
QPushButton:checked { background: #284b48; border-color: #54d6bd; color: #ebfffa; }
QPushButton#primary { background: #54d6bd; color: #142c29; border-color: #54d6bd; font-weight: 650; }
QPushButton#primary:hover { background: #76e6d1; }
QPushButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
  border-color: #54d6bd;
}
QPushButton:disabled, QPushButton#primary:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QLineEdit:disabled {
  background: #282e36; color: #87929e; border-color: #3d4651;
}
QLabel:disabled, QCheckBox:disabled { color: #87929e; }
QComboBox QAbstractItemView { background: #2b343e; selection-background-color: #315d56; color: #eef0f2; }
QSlider::groove:horizontal { height: 5px; background: #465360; border-radius: 2px; }
QSlider::handle:horizontal { background: #54d6bd; width: 13px; margin: -4px 0; border-radius: 6px; }
QSlider::handle:disabled { background: #71808c; }
QProgressBar { background: #303944; border: 1px solid #495462; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #31685f; border-radius: 4px; }
QScrollBar:vertical { background: #1b2026; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #56636f; min-height: 26px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMessageBox { background: #242a32; }
"""

VIDEO_GLOBS = "*.mp4 *.mov *.mkv *.webm *.avi *.mxf *.mpg *.mpeg *.m4v *.ts *.mts *.m2ts *.apv"


class _Job(QThread):
    progress = Signal(float, str)

    def __init__(self, task, parent):
        super().__init__(parent)
        self.task = task
        self.result = None
        self.error: Exception | None = None

    def run(self):
        try:
            self.result = self.task(self.progress.emit)
        except Exception as error:
            self.error = error


def _select(combo: QComboBox, value):
    combo.setCurrentIndex(combo.findData(value))


def _fps(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") + " fps"


def _size(value: int) -> str:
    return f"{value:,} B ({value / 1_000_000:.3f} MB)"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.resize(1200, 820)
        self.setMinimumSize(855, 620)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        self._settings = settings if settings is not None else QSettings()
        self.language = self._settings.value("language", "ko")
        if self.language not in ("ko", "en"):
            self.language = "ko"
        self._texts = []
        self._combos = []
        self.info: MediaInfo | None = None
        self._loading = self._resetting = self._playing = self._closing = False
        self._generation = 0
        self._jobs = {}
        self._cancel: Event | None = None
        self._result: ExportResult | None = None
        self._result_format = ""
        self._lut: Path | None = None
        self._displayed_frame_ms: int | None = None
        self._status = ("Choose a video to start.", {}, False)
        self._preview_empty = "Drop a video here"

        self.preview_stream = PreviewStream(self)
        self.preview_stream.frameReady.connect(self._frame_ready)
        self.preview_stream.failed.connect(self._preview_failed)
        self.preview_stream.ended.connect(self._preview_ended)
        self.preview_stream.idle.connect(self._maybe_close)
        self._preview_timer = QTimer(self, singleShot=True, interval=105)
        self._preview_timer.timeout.connect(self._render_preview)
        self._build()
        self._connect()
        _select(self.language_box, self.language)
        self._retranslate()
        self._sync_controls()

    def tr_text(self, key: str, **values) -> str:
        return translate(self.language, key, **values)

    def _label(self, key="", name="") -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(name)
        if key:
            self._texts.append((label.setText, key))
        return label

    def _button(self, key: str, slot=None, *, primary=False) -> QPushButton:
        button = QPushButton()
        self._texts.append((button.setText, key))
        if slot:
            button.clicked.connect(slot)
        if primary:
            button.setObjectName("primary")
        return button

    def _combo(self, *choices) -> QComboBox:
        combo = QComboBox()
        for key, value in choices:
            combo.addItem(key, value)
        self._combos.append((combo, [key for key, _ in choices]))
        return combo

    def _spin(self, high: int, value: int, suffix: str = "", low: int = 1) -> QSpinBox:
        field = QSpinBox()
        field.setRange(low, high)
        field.setKeyboardTracking(False)
        field.setSingleStep(1)
        field.setValue(value)
        field.setSuffix(suffix)
        return field

    def _decimal(self, high: float, value: float, suffix: str, low: float = 1) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setDecimals(3)
        field.setRange(low, high)
        field.setKeyboardTracking(False)
        field.setValue(value)
        field.setSuffix(suffix)
        return field

    def _panel(self):
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _section(self, parent_layout, key):
        panel = self._panel()
        parent_layout.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.addWidget(self._label(key, "section"))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        layout.addLayout(form)
        return form

    def _row(self, form, key, field):
        label = self._label(key)
        label.setBuddy(field)
        self._texts.append((field.setAccessibleName, key))
        form.addRow(label, field)

    def _build(self):
        central = QWidget()
        central.setObjectName("editor")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(12)
        layout.addLayout(header)
        header.addWidget(self._label("Jjal Maker", "brand"))
        self.source_label = self._label(name="muted")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.source_label, 1)
        self.open_button = self._button("Open video…", self._open_dialog)
        header.addWidget(self.open_button)
        self.language_box = self._combo(("한국어", "ko"), ("English", "en"))
        self._texts.append((self.language_box.setAccessibleName, "Language"))
        header.addWidget(self.language_box)
        self.open_action = QAction(self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._texts.append((self.open_action.setText, "Open video…"))
        self.open_action.triggered.connect(self._open_dialog)
        self.addAction(self.open_action)

        body = QHBoxLayout()
        body.setSpacing(12)
        layout.addLayout(body, 1)
        preview_panel = self._panel()
        body.addWidget(preview_panel, 1)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        toolbar = QHBoxLayout()
        preview_layout.addLayout(toolbar)
        self.edit_view = self._button("Edit")
        self.result_view = self._button("Result")
        views = QButtonGroup(self)
        for button in (self.edit_view, self.result_view):
            button.setCheckable(True)
            views.addButton(button)
            toolbar.addWidget(button)
        self.edit_view.setChecked(True)
        toolbar.addStretch(1)
        self.new_crop = self._button("New crop")
        self.new_crop.setCheckable(True)
        self.reset_crop = self._button("Reset crop")
        toolbar.addWidget(self.new_crop)
        toolbar.addWidget(self.reset_crop)
        self.canvas = PreviewCanvas()
        self._texts.append((self.canvas.setAccessibleName, "Preview"))
        self._texts.append((self.canvas.setAccessibleDescription, "Drag to select, move or resize the crop. Arrow keys move it."))
        preview_layout.addWidget(self.canvas, 1)

        settings = QWidget()
        settings.setObjectName("settings")
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setMinimumWidth(304)
        self.settings_scroll.setMaximumWidth(353)
        self.settings_scroll.setWidget(settings)
        body.addWidget(self.settings_scroll, 0)
        self._build_settings(settings)
        self._build_timeline(layout)

    def _build_settings(self, settings):
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 3, 0)
        layout.setSpacing(8)

        crop = self._section(layout, "Crop")
        self.aspect = self._combo(("Free", None), ("1:1", (1, 1)), ("3:2", (3, 2)), ("16:9", (16, 9)))
        self._row(crop, "Aspect ratio", self.aspect)
        self.crop_size = self._label(name="muted")
        self._row(crop, "Crop size", self.crop_size)

        output = self._section(layout, "Output")
        self.format_box = self._combo(("WebP", "webp"), ("GIF", "gif"), ("PNG (still frame)", "png"), ("APNG", "apng"))
        self._row(output, "Format", self.format_box)
        self.scale = self._combo(("Cropped size (100%)", 100), ("75%", 75), ("50%", 50), ("25%", 25), ("Custom", "custom"))
        self._row(output, "Scale", self.scale)
        self.width_box = self._spin(65535, 1, " px")
        self.height_box = self._spin(65535, 1, " px")
        self._row(output, "Width", self.width_box)
        self._row(output, "Height", self.height_box)
        self.output_size = self._label(name="muted")
        self._row(output, "Output size", self.output_size)
        self.fps_box = self._decimal(60, 20, " fps")
        self._row(output, "Frame rate", self.fps_box)
        self.compression = self._combo(("Quality", "quality"), ("Target file size", "target"))
        self._row(output, "Compression", self.compression)
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(0, 100)
        self.quality.setValue(80)
        self.quality.setTracking(False)
        self.quality_label = self._label(name="muted")
        quality_row = QWidget()
        quality_layout = QHBoxLayout(quality_row)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.addWidget(self.quality, 1)
        quality_layout.addWidget(self.quality_label)
        self._texts.append((self.quality.setAccessibleName, "Quality"))
        self._row(output, "Quality", quality_row)
        self.target_mb = self._decimal(10000, 10, " MB", low=0.001)
        self._row(output, "Max. size", self.target_mb)
        self.target_hint = self._label("Target size can reduce resolution or FPS.", "muted")
        self.target_hint.setWordWrap(True)
        output.addRow(self.target_hint)
        self._output_form = output
        self._quality_row = quality_row

        timestamp = self._section(layout, "Timestamp")
        self.timestamp_enabled = QCheckBox()
        self._texts.append((self.timestamp_enabled.setText, "Show timestamp"))
        timestamp.addRow(self.timestamp_enabled)
        self.timestamp_base = QLineEdit("00:00:00")
        self.timestamp_base.setMaxLength(30)
        self._row(timestamp, "Time at In", self.timestamp_base)
        self._texts.append((self.timestamp_base.setToolTip, "Use HH:MM:SS[.mmm] or YYYY-MM-DD HH:MM:SS[.mmm]."))
        self.timestamp_position = self._combo(("Over image", "overlay"), ("Bottom band", "band"))
        self._row(timestamp, "Position", self.timestamp_position)
        self.timestamp_font = self._spin(512, 28, " px")
        self._row(timestamp, "Font size", self.timestamp_font)

        color = self._section(layout, "Color")
        self.color_mode = self._combo(("Auto", "auto"), ("SDR", "sdr"), ("HDR (PQ)", "pq"), ("HDR (HLG)", "hlg"))
        self._row(color, "Source color", self.color_mode)
        self.lut_label = self._label(name="muted")
        self.lut_label.setWordWrap(True)
        self._row(color, "LUT", self.lut_label)
        lut_buttons = QHBoxLayout()
        self.load_lut = self._button("Load .cube…", self._load_lut_dialog)
        self.clear_lut = self._button("Clear LUT", self._clear_lut)
        lut_buttons.addWidget(self.load_lut)
        lut_buttons.addWidget(self.clear_lut)
        color.addRow(lut_buttons)
        lut_link = QLabel()
        lut_link.setOpenExternalLinks(True)
        lut_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self._texts.append((
            lambda text: lut_link.setText(
                f'<a style="color:#76dfcb;" href="https://www.dji.com/nano/downloads">{text}</a>'
            ),
            "DJI Osmo Nano LUT downloads",
        ))
        color.addRow(lut_link)
        layout.addStretch(1)

    def _build_timeline(self, parent_layout):
        panel = self._panel()
        parent_layout.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(4)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        layout.addLayout(controls)
        controls.addWidget(self._label("Timeline", "section"))
        self.play = QPushButton()
        self.play.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play)
        self.clock = self._label(name="muted")
        self.clock.setText(format_clock(0))
        controls.addWidget(self.clock)
        controls.addStretch(1)
        self.in_box = self._spin(0, 0, " ms", low=0)
        self.out_box = self._spin(0, 0, " ms", low=0)
        self._time_buttons = []
        for key, field, tip, slot in (
            ("In", self.in_box, "Set In to playhead", self._set_in_to_head),
            ("Out", self.out_box, "Set Out to playhead", self._set_out_to_head),
        ):
            label = self._label(key)
            label.setBuddy(field)
            controls.addWidget(label)
            controls.addWidget(field)
            self._texts.append((field.setAccessibleName, key))
            button = QPushButton("↳")
            button.clicked.connect(slot)
            self._texts.extend(((button.setAccessibleName, tip), (button.setToolTip, tip)))
            controls.addWidget(button)
            self._time_buttons.append(button)
        self.timeline = Timeline()
        self._texts.append((self.timeline.setAccessibleName, "Timeline"))
        self._texts.append((self.timeline.setAccessibleDescription, "Arrow keys seek. Drag the handles to trim."))
        layout.addWidget(self.timeline)
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.timeline)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(self._toggle_playback)
        footer = QHBoxLayout()
        layout.addLayout(footer)
        self.status = self._label(name="muted")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(self.status.fontMetrics().lineSpacing() * 2 + 2)
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(105)
        footer.addWidget(self.progress)
        self.cancel_button = self._button("Cancel export", self._cancel_export)
        footer.addWidget(self.cancel_button)
        self.folder_button = self._button("Open output folder", self._open_result_folder)
        footer.addWidget(self.folder_button)
        self.export_button = self._button("Export…", self._export_dialog, primary=True)
        footer.addWidget(self.export_button)

    def _connect(self):
        self.language_box.currentIndexChanged.connect(self._change_language)
        self.result_view.toggled.connect(self._view_changed)
        self.new_crop.toggled.connect(self._new_crop_mode)
        self.canvas.cropGestureFinished.connect(lambda: self.new_crop.setChecked(False))
        self.reset_crop.clicked.connect(self.canvas.reset_crop)
        self.canvas.cropChanged.connect(self._crop_changed)
        self.aspect.currentIndexChanged.connect(self._aspect_changed)
        self.scale.currentIndexChanged.connect(self._scale_changed)
        self.width_box.valueChanged.connect(lambda: self._custom_size_changed(self.width_box))
        self.height_box.valueChanged.connect(lambda: self._custom_size_changed(self.height_box))
        for widget in (self.format_box, self.compression, self.timestamp_position, self.color_mode):
            widget.currentIndexChanged.connect(self._edit_changed)
        for widget in (self.fps_box, self.target_mb, self.timestamp_font, self.quality):
            widget.valueChanged.connect(self._edit_changed)
        self.quality.sliderMoved.connect(lambda value: self.quality_label.setText(str(value)))
        self.timestamp_enabled.toggled.connect(self._edit_changed)
        self.timestamp_base.textChanged.connect(self._edit_changed)
        self.timeline.trimRequested.connect(self._set_trim)
        self.timeline.seekRequested.connect(self._seek)
        self.in_box.valueChanged.connect(lambda value: self._set_trim(value, self.timeline.end))
        self.out_box.valueChanged.connect(lambda value: self._set_trim(self.timeline.start, value))

    def _change_language(self):
        self.language = self.language_box.currentData()
        self._settings.setValue("language", self.language)
        self._retranslate()
        self._sync_controls()

    def _retranslate(self):
        self.setWindowTitle(self.tr_text("Jjal Maker"))
        for setter, key in self._texts:
            setter(self.tr_text(key))
        for combo, keys in self._combos:
            for index, key in enumerate(keys):
                combo.setItemText(index, self.tr_text(key))
        self._update_source_label()
        self._update_status()
        self._update_play_label()
        self._update_clock()
        self._update_sizes()
        self._update_lut_label()
        self._set_placeholder(self._preview_empty)

    def _set_status(self, key, *, error=False, **values):
        self._status = key, values, error
        self._update_status()

    def _update_status(self):
        key, values, error = self._status
        if key == "export_result":
            visible = self._result_summary(elide_name=True)
            full = self._result_summary()
        else:
            full = self.tr_text(key, **values)
            if "detail" in values:
                detail = self.tr_text(values["detail"]).strip()
                full = self.tr_text(key, **{**values, "detail": detail})
                line = detail.partition("\n")[0].strip()
                if len(line) > 140:
                    line = f"{line[:139]}…"
                values = {**values, "detail": line}
            visible = self.tr_text(key, **values)
        self.status.setStyleSheet("color: #ffbab3;" if error else "")
        self.status.setText(visible)
        self.status.setToolTip(full)

    def _set_placeholder(self, key):
        self._preview_empty = key
        self.canvas.placeholder = self.tr_text(key)
        self.canvas.update()

    def _update_source_label(self):
        if self.info is None:
            self.source_label.setText(self.tr_text("No video selected"))
            self.source_label.setToolTip("")
            return
        info = self.info
        self.source_label.setText(f"{info.path.name}  ·  {info.width} × {info.height}  ·  {_fps(info.fps)}")
        self.source_label.setToolTip(str(info.path))

    def _update_play_label(self):
        self.play.setText(self.tr_text("Pause" if self._playing else "Play selection"))

    def _update_lut_label(self):
        self.lut_label.setText(self._lut.name if self._lut else self.tr_text("No LUT loaded"))
        self.lut_label.setToolTip(str(self._lut) if self._lut else "")

    def _sync_controls(self):
        editing = self.info is not None and not self._loading and self._cancel is None
        self.settings_scroll.setEnabled(editing)
        for widget in (self.canvas, self.timeline, self.play, self.edit_view, self.result_view, self.in_box, self.out_box, *self._time_buttons):
            widget.setEnabled(editing)
        self.new_crop.setEnabled(editing and not self.result_view.isChecked())
        self.reset_crop.setEnabled(self.new_crop.isEnabled())
        self.open_button.setEnabled(self._cancel is None)
        self.open_action.setEnabled(self.open_button.isEnabled())
        self.export_button.setEnabled(editing)
        self.progress.setVisible(self._cancel is not None)
        self.cancel_button.setVisible(self._cancel is not None)
        self.cancel_button.setEnabled(self._cancel is not None and not self._cancel.is_set())
        self.folder_button.setVisible(self._result is not None and self._cancel is None)
        custom = self.scale.currentData() == "custom"
        self.width_box.setEnabled(custom)
        self.height_box.setEnabled(custom)
        fmt = self.format_box.currentData()
        with QSignalBlocker(self.fps_box):
            self.fps_box.setMaximum(50 if fmt == "gif" else 60)
        self.fps_box.setEnabled(fmt != "png")
        target = self.compression.currentData() == "target"
        self._output_form.setRowVisible(self._quality_row, not target)
        self._output_form.setRowVisible(self.target_mb, target)
        self._output_form.setRowVisible(self.target_hint, target)
        self.quality.setEnabled(fmt in ("gif", "webp"))
        self.quality_label.setText(str(self.quality.value()) if fmt in ("gif", "webp") else self.tr_text("Lossless"))
        for widget in (self.timestamp_base, self.timestamp_position, self.timestamp_font):
            widget.setEnabled(self.timestamp_enabled.isChecked())
        self.clear_lut.setEnabled(self._lut is not None)

    def _open_dialog(self):
        video_filter = f"{self.tr_text('Video files')} ({VIDEO_GLOBS});;{self.tr_text('All files')} (*)"
        directory = str(self.info.path.parent) if self.info else ""
        name, _ = QFileDialog.getOpenFileName(self, self.tr_text("Open video…"), directory, video_filter)
        if name:
            self.open_path(name)

    def dragEnterEvent(self, event):
        if self._cancel is None and event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile() and not Path(urls[0].toLocalFile()).drive.startswith("\\\\"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if self._cancel is not None:
            return
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            self._set_status("Open one local video file.", error=True)
            return
        event.acceptProposedAction()
        self.open_path(urls[0].toLocalFile())

    def open_path(self, name: str):
        if self._cancel is not None or self._closing:
            return
        path = Path(name)
        if path.drive.startswith("\\\\") or not path.is_file():
            self._set_status("Open one local video file.", error=True)
            return
        self._generation += 1
        generation = self._generation
        self._loading = True
        self._stop_preview()
        self._set_status("Loading {name}…", name=path.name)
        self._set_placeholder("Loading preview…")
        self._sync_controls()
        self._run_job(lambda _progress: media.probe(path), lambda job: self._probe_finished(job, generation))

    def _run_job(self, task, finished, with_progress=False):
        job = _Job(task, self)
        self._jobs[job] = finished
        job.finished.connect(self._job_finished)
        if with_progress:
            job.progress.connect(self._export_progress)
        job.start()

    @Slot()
    def _job_finished(self):
        job = self.sender()
        finished = self._jobs.pop(job)
        if not self._closing:
            finished(job)
        job.deleteLater()
        self._maybe_close()

    def _probe_finished(self, job, generation):
        if generation != self._generation:
            return
        self._loading = False
        if job.error:
            self._set_status("Could not open the video. {detail}", error=True, detail=str(job.error))
            self._set_placeholder("Drop a video here" if self.info is None else "Loading preview…")
        else:
            self._apply_source(job.result)
            self._set_status("Ready")
        self._sync_controls()
        self._schedule_preview()

    def _apply_source(self, info: MediaInfo):
        self.info = info
        self._resetting = True
        self._result = self._lut = None
        self.edit_view.setChecked(True)
        self.new_crop.setChecked(False)
        self.canvas.result_view = self.canvas.new_crop = False
        self.canvas.set_aspect(None)
        self.canvas.set_source(info.width, info.height)
        self.timeline.set_duration(max(1, min(2_147_483_647, math.ceil(info.duration * 1000))))
        self._update_trim_boxes()
        self._set_head(0)
        for combo, value in (
            (self.aspect, None), (self.format_box, "webp"), (self.scale, 100),
            (self.compression, "quality"), (self.timestamp_position, "overlay"), (self.color_mode, "auto"),
        ):
            _select(combo, value)
        self.fps_box.setValue(20)
        self.target_mb.setValue(10)
        self.quality.setValue(80)
        self.timestamp_enabled.setChecked(False)
        self.timestamp_base.setText("00:00:00")
        self.timestamp_font.setValue(28)
        self._resetting = False
        self._scale_changed()
        self._update_source_label()
        self._update_lut_label()

    def _new_crop_mode(self, enabled: bool):
        self.canvas.new_crop = enabled
        if enabled:
            self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    def _aspect_changed(self):
        if not self._resetting:
            self.canvas.set_aspect(self.aspect.currentData())

    def _view_changed(self, result: bool):
        if self._resetting:
            return
        self.canvas.result_view = result
        self.canvas.set_image(QImage())
        self.new_crop.setChecked(False)
        self._set_head(self._seek_position(self.timeline.position))
        self._set_placeholder("Loading preview…")
        self._sync_controls()
        self._schedule_preview()

    def _crop_changed(self, _crop):
        if not self._resetting:
            self._scale_changed()

    def _scale_changed(self):
        crop = self.canvas.crop
        if self._resetting or crop is None:
            return
        scale = self.scale.currentData()
        if scale == "custom":
            self._custom_size_changed(self.width_box)
            return
        self._set_dimensions(round(crop.width * scale / 100), round(crop.height * scale / 100))
        self._edit_changed()

    def _set_dimensions(self, width: int, height: int):
        with QSignalBlocker(self.width_box), QSignalBlocker(self.height_box):
            self.width_box.setValue(width)
            self.height_box.setValue(height)

    def _custom_size_changed(self, field):
        crop = self.canvas.crop
        if self._resetting or crop is None or self.scale.currentData() != "custom":
            return
        if field is self.width_box:
            width = field.value()
            height = min(self.height_box.maximum(), max(1, round(width * crop.height / crop.width)))
            if height == self.height_box.maximum():
                width = min(width, round(height * crop.width / crop.height))
        else:
            height = field.value()
            width = min(self.width_box.maximum(), max(1, round(height * crop.width / crop.height)))
            if width == self.width_box.maximum():
                height = min(height, round(width * crop.height / crop.width))
        self._set_dimensions(width, height)
        self._edit_changed()

    def _edit_changed(self):
        if self._resetting:
            return
        self._sync_controls()
        self._update_sizes()
        if self.result_view.isChecked():
            self._set_head(self._seek_position(self.timeline.position))
            self._schedule_preview()

    def _update_sizes(self):
        crop = self.canvas.crop
        if crop is None:
            self.crop_size.setText("—")
            self.output_size.setText("—")
            return
        self.crop_size.setText(f"{crop.width} × {crop.height} px")
        width, height = media.output_dimensions(self.edit_spec())
        self.output_size.setText(f"{width} × {height} px")

    def _update_trim_boxes(self):
        timeline = self.timeline
        with QSignalBlocker(self.in_box), QSignalBlocker(self.out_box):
            self.in_box.setRange(0, timeline.end - 1)
            self.out_box.setRange(timeline.start + 1, timeline.duration)
            self.in_box.setValue(timeline.start)
            self.out_box.setValue(timeline.end)

    def _set_trim(self, start: int, end: int):
        if self.info is None or self._loading:
            return
        timeline = self.timeline
        start = max(0, min(timeline.duration - 1, start))
        end = max(start + 1, min(timeline.duration, end))
        if (start, end) == (timeline.start, timeline.end):
            return
        timeline.set_selection(start, end)
        self._update_trim_boxes()
        if self._playing:
            self._stop_preview()
        self._set_head(max(start, min(end, timeline.position)))
        self._schedule_preview()

    def _set_in_to_head(self):
        self._set_trim(min(self.timeline.position, self.timeline.end - 1), self.timeline.end)

    def _set_out_to_head(self):
        self._set_trim(self.timeline.start, max(self.timeline.position, self.timeline.start + 1))

    def _set_head(self, position: int):
        self.timeline.set_position(position)
        self._displayed_frame_ms = None
        self._update_clock()

    def _update_clock(self):
        self.clock.setText(format_clock(self.timeline.position))
        self.clock.setToolTip(
            self.tr_text("Displayed frame at {time}", time=format_clock(self._displayed_frame_ms))
            if self._displayed_frame_ms is not None and self._displayed_frame_ms != self.timeline.position else ""
        )

    def _seek_position(self, position: int) -> int:
        if self.result_view.isChecked() and self.format_box.currentData() != "png":
            return max(self.timeline.start, min(self.timeline.end, position))
        return max(0, min(self.timeline.duration, position))

    def _seek(self, position: int):
        if self._playing:
            self._stop_preview()
        self._set_head(self._seek_position(position))
        self._schedule_preview()

    def edit_spec(self) -> EditSpec:
        assert self.info is not None and self.canvas.crop is not None
        timeline = self.timeline
        target_bytes = round(self.target_mb.value() * 1_000_000) if self.compression.currentData() == "target" else None
        return EditSpec(
            timeline.start / 1000,
            min(self.info.duration, timeline.end / 1000),
            self.canvas.crop, self.width_box.value(), self.height_box.value(),
            fps=self.fps_box.value(), quality=self.quality.value(), format=self.format_box.currentData(),
            target_bytes=target_bytes, playhead=min(self.info.duration, timeline.position / 1000),
            timestamp=TimestampSpec(
                self.timestamp_enabled.isChecked(), self.timestamp_base.text().strip(),
                self.timestamp_position.currentData(), self.timestamp_font.value(),
            ),
            lut_path=self._lut, color_mode=self.color_mode.currentData(),
        )

    def _validated_edit(self):
        edit = self.edit_spec()
        if edit.timestamp.enabled:
            try:
                media.timestamp_text(edit.timestamp, 0)
            except ValueError:
                self._set_status("The timestamp is invalid. Use HH:MM:SS[.mmm] or YYYY-MM-DD HH:MM:SS[.mmm].", error=True)
                return None
        try:
            media.validate_edit(self.info, edit)
        except (ValueError, OSError, RuntimeError) as error:
            self._set_status("Check the edit settings. {detail}", error=True, detail=str(error))
            return None
        return edit

    def _load_lut_dialog(self):
        name, _ = QFileDialog.getOpenFileName(self, self.tr_text("Load .cube…"), "", "LUT (*.cube)")
        if name:
            self.set_lut(Path(name))

    def set_lut(self, path: Path):
        if path.suffix.lower() != ".cube" or not path.is_file():
            self._set_status("Select an existing .cube file.", error=True)
            return
        self._lut = path.resolve()
        self._update_lut_label()
        self._edit_changed()

    def _clear_lut(self):
        self._lut = None
        self._update_lut_label()
        self._edit_changed()

    def _schedule_preview(self):
        if self.info is None or self._loading or self._closing:
            return
        self.preview_stream.stop()
        self._preview_timer.start()

    def _render_preview(self):
        if self.info is None or self._loading or self._closing:
            return
        result = self.result_view.isChecked()
        if result:
            edit = self._validated_edit()
        else:
            edit = replace(
                self.edit_spec(), format="png", timestamp=TimestampSpec(),
                lut_path=None, color_mode="auto", fps=min(30, max(1, self.info.fps)),
            )
        if edit is None:
            self._preview_failed("")
            return
        try:
            command = media.preview_command(self.info, edit, self.timeline.position / 1000, result=result, playing=self._playing)
        except (ValueError, OSError, RuntimeError) as error:
            self._preview_failed(str(error))
            return
        self.preview_stream.start(command, self._playing)

    def _toggle_playback(self):
        if not self.play.isEnabled():
            return
        if self._playing:
            self._stop_preview()
        else:
            self._playing = True
            timeline = self.timeline
            if not timeline.start <= timeline.position < timeline.end:
                self._set_head(timeline.start)
            self._update_play_label()
        self._schedule_preview()

    def _stop_preview(self):
        self._playing = False
        self._update_play_label()
        self._preview_timer.stop()
        self.preview_stream.stop()

    @Slot(QImage, float)
    def _frame_ready(self, image, position):
        position_ms = round(position * 1000)
        if self._playing:
            if position_ms >= self.timeline.end:
                self._preview_ended()
                return
            self._set_head(max(self.timeline.position, position_ms))
        if self.result_view.isChecked() and self.format_box.currentData() != "png":
            self._displayed_frame_ms = position_ms
            self._update_clock()
        self.canvas.set_image(image)
        if self._status[2] and (
            self._status[0] == "Could not preview the video. {detail}"
            or (self.result_view.isChecked() and self._status[0].startswith(("The timestamp is invalid.", "Check the edit settings.")))
        ):
            self._set_status("Ready")

    def _preview_ended(self):
        if self._playing:
            self._set_head(self.timeline.start)
            self._schedule_preview()

    def _preview_failed(self, detail):
        self._stop_preview()
        self.canvas.set_image(QImage())
        self._set_placeholder("Preview could not be generated.")
        if detail:
            self._set_status("Could not preview the video. {detail}", error=True, detail=detail)

    def _export_dialog(self):
        if not self.export_button.isEnabled():
            return
        edit = self._validated_edit()
        if edit is None:
            return
        if self._playing:
            self._stop_preview()
            self._schedule_preview()
        extension = f".{edit.format}"
        candidate = self.info.path.with_name(f"{self.info.path.stem}-jjal{extension}")
        name, _ = QFileDialog.getSaveFileName(
            self, self.tr_text("Export…"), str(candidate), f"{edit.format.upper()} (*{extension})",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not name:
            return
        destination = Path(name)
        if destination.suffix.lower() != extension:
            destination = destination.with_suffix(extension)
        try:
            if destination.resolve() == self.info.path.resolve() or (
                destination.is_file() and destination.samefile(self.info.path)
            ):
                self._set_status("Choose a file other than the source video.", error=True)
                return
            if not destination.parent.is_dir() or destination.is_dir():
                self._set_status("Select a save location for the output.", error=True)
                return
        except (OSError, RuntimeError) as error:
            self._set_status("Export failed. {detail}", error=True, detail=str(error))
            return
        overwrite = destination.exists() or destination.is_symlink()
        if overwrite:
            answer = QMessageBox.question(
                self, self.tr_text("Replace existing file?"), self.tr_text("Replace {name}?", name=destination.name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_export(edit, destination, overwrite)

    def _start_export(self, edit, destination, overwrite):
        info = self.info
        self._cancel = cancel = Event()
        self.progress.setValue(0)
        self._set_status("Exporting…")
        self._sync_controls()
        self._run_job(
            lambda progress: export.export_video(info, edit, destination, cancel=cancel, progress=progress, overwrite=overwrite),
            lambda job: self._export_finished(job, edit.format), with_progress=True,
        )

    @Slot(float, str)
    def _export_progress(self, fraction, _stage):
        if self._cancel is not None and not self._cancel.is_set():
            self.progress.setValue(round(100 * max(0, min(1, fraction))))

    def _cancel_export(self):
        if self._cancel is not None:
            self._cancel.set()
            self._set_status("Cancelling…")
            self._sync_controls()

    def _export_finished(self, job, fmt):
        self._cancel = None
        if isinstance(job.error, export.ExportCancelled):
            self._set_status("Export cancelled.")
        elif isinstance(job.error, export.TargetSizeError):
            self._set_status("The output exceeds the size limit. Smallest attempt was {size}.",
                             error=True, size=_size(job.error.smallest_bytes))
        elif job.error:
            self._set_status("Export failed. {detail}", error=True, detail=str(job.error))
        else:
            self._result, self._result_format = job.result, fmt
            self._set_status("export_result")
        self._sync_controls()

    def _result_summary(self, elide_name=False):
        result = self._result
        if result is None:
            return ""
        rate = self.tr_text("Still frame") if self._result_format == "png" else _fps(result.fps)
        quality = self.tr_text("Lossless") if result.quality is None else self.tr_text("Quality {quality}", quality=result.quality)
        name = result.path.name
        if elide_name:
            name = self.status.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, 150)
        return self.tr_text(
            "{name}  ·  {size}  ·  {dimensions}  ·  {rate}  ·  {quality}  ·  Attempts {attempts}",
            name=name, size=_size(result.bytes), dimensions=f"{result.width} × {result.height} px",
            rate=rate, quality=quality, attempts=result.attempts,
        )

    def _open_result_folder(self):
        if self._result and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.path.parent))):
            self._set_status("Could not open the output folder.", error=True)

    def _maybe_close(self):
        if self._closing and not self._jobs and not self.preview_stream.busy:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        self._closing = True
        self._stop_preview()
        self._cancel_export()
        if self._jobs or self.preview_stream.busy:
            self.centralWidget().setEnabled(False)
            event.ignore()
        else:
            event.accept()
