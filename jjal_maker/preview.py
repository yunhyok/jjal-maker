"""FFmpeg preview stream and the two image/time editing surfaces."""

from PySide6.QtCore import QObject, QPointF, QProcess, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .models import Crop, RenderCommand


ACCENT = QColor("#54d6bd")
MUTED = QColor("#a6b1bb")


def format_clock(milliseconds: int, fractional: bool = True) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{clock}.{millis:03d}" if fractional else clock


class PreviewStream(QObject):
    frameReady = Signal(QImage, float)
    failed = Signal(str)
    ended = Signal()
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.readyReadStandardError.connect(self._read_error)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)
        self._pending: tuple[RenderCommand, bool] | None = None
        self._active: tuple[RenderCommand, bool] | None = None
        self._remaining = b""
        self._stderr = ""
        self._frames = 0

    @property
    def busy(self) -> bool:
        return self._pending is not None or self._process.state() != QProcess.ProcessState.NotRunning

    def start(self, command: RenderCommand, playing: bool):
        self._pending = command, playing
        self._active = None
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._dispatch()
        else:
            self._process.kill()

    def stop(self):
        self._pending = self._active = None
        if self.busy:
            self._process.kill()

    def _dispatch(self):
        if self._pending is None:
            return
        self._active, self._pending = self._pending, None
        command, _ = self._active
        self._remaining = b""
        self._stderr = ""
        self._frames = 0
        self._process.start(command.args[0], command.args[1:])

    def _read(self):
        data = bytes(self._process.readAllStandardOutput())
        if self._active is None:
            return
        command, _ = self._active
        frame_size = command.width * command.height * 3
        data = self._remaining + data
        count, remaining = divmod(len(data), frame_size)
        self._remaining = data[-remaining:] if remaining else b""
        if count:
            frame = data[(count - 1) * frame_size : count * frame_size]
            image = QImage(frame, command.width, command.height, command.width * 3, QImage.Format.Format_RGB888)
            self._frames += count
            self.frameReady.emit(image.copy(), command.position + (self._frames - 1) / command.fps)

    def _read_error(self):
        data = bytes(self._process.readAllStandardError()).decode(errors="replace")
        self._stderr = (self._stderr + data)[-1500:]

    def _finished(self, code, _status=None):
        self._read()
        self._read_error()
        active, self._active = self._active, None
        if self._pending is not None:
            self._dispatch()
            return
        if active is not None:
            _, playing = active
            if code or not self._frames:
                self.failed.emit(self._stderr.strip() or self._process.errorString())
            elif playing:
                self.ended.emit()
        if not self.busy:
            self.idle.emit()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(-1)


class PreviewCanvas(QWidget):
    cropChanged = Signal(Crop)
    cropGestureFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 175)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._image = QImage()
        self._width = self._height = 0
        self.crop: Crop | None = None
        self.aspect: tuple[int, int] | None = None
        self.result_view = False
        self.new_crop = False
        self.placeholder = ""
        self._drag: tuple[str, QPointF, Crop] | None = None

    def set_source(self, width: int, height: int):
        self._width, self._height = width, height
        self._image = QImage()
        self._drag = None
        self.crop = Crop(0, 0, width, height) if width and height else None
        self.update()

    def set_image(self, image: QImage):
        self._image = image
        self.update()

    def set_crop(self, crop: Crop):
        width = min(self._width, max(1, crop.width))
        height = min(self._height, max(1, crop.height))
        bounded = Crop(
            max(0, min(self._width - width, crop.x)),
            max(0, min(self._height - height, crop.y)),
            width, height,
        )
        if bounded != self.crop:
            self.crop = bounded
            self.update()
            self.cropChanged.emit(bounded)

    def set_aspect(self, aspect: tuple[int, int] | None):
        self.aspect = aspect
        if self.crop and aspect:
            crop = self.crop
            width, height = self._fit_dimensions(crop.width, crop.height)
            self.set_crop(Crop(crop.x + (crop.width - width) // 2, crop.y + (crop.height - height) // 2, width, height))
        self.update()

    def reset_crop(self):
        if self.crop:
            width, height = self._fit_dimensions(self._width, self._height)
            self.set_crop(Crop((self._width - width) // 2, (self._height - height) // 2, width, height))

    def _fit_dimensions(self, width: float, height: float) -> tuple[int, int]:
        if self.aspect is None:
            return max(1, round(width)), max(1, round(height))
        aw, ah = self.aspect
        unit = min(width / aw, height / ah)
        if self._width >= aw and self._height >= ah:
            steps = max(1, int(unit))
            return steps * aw, steps * ah
        return max(1, round(unit * aw)), max(1, round(unit * ah))

    def image_rect(self) -> QRectF:
        width, height = self._width, self._height
        if self.result_view:
            width, height = self._image.width(), self._image.height()
        if not width or not height:
            return QRectF()
        scale = min(self.width() / width, self.height() / height)
        w, h = width * scale, height * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def source_point(self, point: QPointF) -> QPointF:
        rect = self.image_rect()
        if rect.isEmpty():
            return QPointF()
        return QPointF(
            max(0, min(self._width, (point.x() - rect.left()) * self._width / rect.width())),
            max(0, min(self._height, (point.y() - rect.top()) * self._height / rect.height())),
        )

    def _crop_rect(self) -> QRectF:
        rect = self.image_rect()
        crop = self.crop
        if crop is None or rect.isEmpty():
            return QRectF()
        xs, ys = rect.width() / self._width, rect.height() / self._height
        return QRectF(rect.left() + crop.x * xs, rect.top() + crop.y * ys, crop.width * xs, crop.height * ys)

    def _handles(self, rect: QRectF) -> dict[str, QPointF]:
        center = rect.center()
        corners = {"nw": rect.topLeft(), "ne": rect.topRight(), "sw": rect.bottomLeft(), "se": rect.bottomRight()}
        if self.aspect is None:
            corners.update({
                "n": QPointF(center.x(), rect.top()), "s": QPointF(center.x(), rect.bottom()),
                "w": QPointF(rect.left(), center.y()), "e": QPointF(rect.right(), center.y()),
            })
        return corners

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111418"))
        if self._image.isNull():
            painter.setPen(MUTED)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frame = self.image_rect()
        painter.drawImage(frame, self._image)
        if self.result_view or not self.crop:
            return
        crop_rect = self._crop_rect()
        shade = QPainterPath()
        shade.addRect(frame)
        shade.addRect(crop_rect)
        painter.fillPath(shade, QColor(0, 0, 0, 145))
        outline = crop_rect.adjusted(1, 1, -1, -1) if crop_rect.width() > 2 and crop_rect.height() > 2 else crop_rect
        painter.setPen(QPen(ACCENT, 1.5))
        painter.drawRect(outline)
        for handle in self._handles(outline).values():
            painter.fillRect(QRectF(handle.x() - 3, handle.y() - 3, 6, 6), ACCENT)

    def _hit(self, point: QPointF) -> str:
        rect = self._crop_rect()
        for name, handle in self._handles(rect).items():
            if (handle - point).manhattanLength() <= 12:
                return name
        if self.new_crop or rect == self.image_rect() or not rect.contains(point):
            return "new"
        return "move"

    def mousePressEvent(self, event):
        if self.result_view or self._image.isNull() or not self.crop:
            return
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            mode = "new" if self.new_crop else self._hit(event.position())
            self._drag = mode, self.source_point(event.position()), self.crop
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._drag is None:
            cursors = {
                "move": Qt.CursorShape.SizeAllCursor, "new": Qt.CursorShape.CrossCursor,
                "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
            }
            if self.crop and not self.result_view and not self._image.isNull() and self.image_rect().contains(point):
                self.setCursor(cursors["new" if self.new_crop else self._hit(point)])
            else:
                self.unsetCursor()
            return
        self._move_crop(self.source_point(point))

    def _move_crop(self, point: QPointF):
        mode, start, crop = self._drag
        dx, dy = round(point.x() - start.x()), round(point.y() - start.y())
        if mode == "move":
            self.set_crop(Crop(crop.x + dx, crop.y + dy, crop.width, crop.height))
            return
        if mode == "new":
            x, y = round(start.x()), round(start.y())
            width, height = self._fit_dimensions(abs(dx), abs(dy))
            self.set_crop(Crop(x - width if dx < 0 else x, y - height if dy < 0 else y, width, height))
            return
        left, top = crop.x, crop.y
        right, bottom = left + crop.width, top + crop.height
        if "w" in mode:
            left = max(0, min(right - 1, left + dx))
        if "e" in mode:
            right = min(self._width, max(left + 1, right + dx))
        if "n" in mode:
            top = max(0, min(bottom - 1, top + dy))
        if "s" in mode:
            bottom = min(self._height, max(top + 1, bottom + dy))
        width, height = self._fit_dimensions(right - left, bottom - top)
        self.set_crop(Crop(right - width if "w" in mode else left, bottom - height if "n" in mode else top, width, height))

    def mouseReleaseEvent(self, event):
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            point = self.source_point(event.position())
            if (point - self._drag[1]).manhattanLength() >= 1:
                self._move_crop(point)
            self._drag = None
            self.new_crop = False
            self.cropGestureFinished.emit()
            event.accept()

    def keyPressEvent(self, event):
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if self.result_view or not self.crop or event.key() not in arrows:
            super().keyPressEvent(event)
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        dx, dy = arrows[event.key()]
        crop = self.crop
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            aw, ah = self.aspect or (1, 1)
            width = min(self._width - crop.x, max(1, crop.width + dx * step * aw))
            height = min(self._height - crop.y, max(1, crop.height + dy * step * ah))
            if self.aspect:
                if dx:
                    height = min(self._height - crop.y, width * ah / aw)
                else:
                    width = min(self._width - crop.x, height * aw / ah)
            width, height = self._fit_dimensions(width, height)
            self.set_crop(Crop(crop.x, crop.y, width, height))
        else:
            self.set_crop(Crop(crop.x + dx * step, crop.y + dy * step, crop.width, crop.height))
        event.accept()


class Timeline(QWidget):
    seekRequested = Signal(int)
    trimRequested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.duration = self.start = self.end = self.position = 0
        self._drag = None

    def set_duration(self, milliseconds: int):
        self.duration = self.end = milliseconds
        self.start = self.position = 0
        self.update()

    def set_selection(self, start: int, end: int):
        self.start, self.end = start, end
        self.update()

    def set_position(self, position: int):
        self.position = position
        self.update()

    def _x(self, milliseconds: int) -> float:
        return 18 + max(1, self.width() - 36) * milliseconds / max(1, self.duration)

    def _time(self, x: float) -> int:
        span = self._x(self.duration) - 18
        return round(self.duration * max(0, min(1, (x - 18) / span))) if span else 0

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        x1, x2 = self._x(self.start), self._x(self.end)
        rail = QRectF(18, 21, self.width() - 36, 9)
        painter.setBrush(QColor("#343e48"))
        painter.drawRoundedRect(rail, 4, 4)
        if self.duration:
            painter.setBrush(QColor("#275b55"))
            painter.drawRoundedRect(QRectF(x1, rail.top(), x2 - x1, rail.height()), 4, 4)
            painter.setBrush(ACCENT)
            for x in (x1, x2):
                painter.drawRoundedRect(QRectF(x - 3, 13, 6, 25), 2, 2)
            x = self._x(self.position)
            painter.setPen(QPen(QColor("#e7e9ec"), 1.5))
            painter.drawLine(QPointF(x, 5), QPointF(x, 42))
            painter.setBrush(QColor("#e7e9ec"))
            painter.drawEllipse(QPointF(x, 42), 2.7, 2.7)
        if self.hasFocus():
            painter.setPen(QPen(ACCENT, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        painter.setPen(MUTED)
        labels = [
            (0, Qt.AlignmentFlag.AlignLeft),
            (self.duration // 2, Qt.AlignmentFlag.AlignHCenter),
            (self.duration, Qt.AlignmentFlag.AlignRight),
        ]
        for time, align in labels if self.duration else labels[:1]:
            painter.drawText(QRectF(12, 45, self.width() - 24, 16), align, format_clock(time, fractional=self.duration < 10000))

    def mousePressEvent(self, event):
        if not self.duration or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        handle = min(("in", "out"), key=lambda mode: abs(point.x() - self._x(self.start if mode == "in" else self.end)))
        edge = self.start if handle == "in" else self.end
        self._drag = handle if point.y() <= 32 and abs(point.x() - self._x(edge)) <= 10 else "seek"
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._drag == "seek":
            self._drag_to(point.x())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag:
            self._drag_to(event.position().x())

    def mouseReleaseEvent(self, event):
        if self._drag and event.button() == Qt.MouseButton.LeftButton:
            self._drag_to(event.position().x())
            self._drag = None
            event.accept()

    def _drag_to(self, x: float):
        position = self._time(x)
        if self._drag == "in":
            self.trimRequested.emit(min(position, self.end - 1), self.end)
        elif self._drag == "out":
            self.trimRequested.emit(self.start, max(position, self.start + 1))
        else:
            self.seekRequested.emit(position)

    def keyPressEvent(self, event):
        key = event.key()
        step = 1000 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 33
        positions = {Qt.Key.Key_Left: self.position - step, Qt.Key.Key_Right: self.position + step,
                     Qt.Key.Key_Home: 0, Qt.Key.Key_End: self.duration}
        if self.duration and key in positions:
            self.seekRequested.emit(max(0, min(self.duration, positions[key])))
            event.accept()
        else:
            super().keyPressEvent(event)
