"""The LectureWeave+ opening animation: "sound becomes text".

The gold sound bars pulse, turn sideways and fly into the transcript page as
its lines; the slide rises in behind, the bars settle back into place, the
"+" pops in with a chime and the name comes into focus. It is drawn with
QPainter from the logo's own shapes (assets/lectureweave-plus.svg, a 512-unit
square), so it stays sharp at any display scaling.

It plays while the rest of the app loads and fades out only once the main
window is ready, so it never adds a wait. A click or any key skips it.
Only PySide6 is imported here, so it can appear before the heavy modules load.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

DURATION = 1.6  # seconds
FADE_MS = 220

PURPLE = QColor("#36145F")
PURPLE_MID = QColor("#6945A0")
LAVENDER = QColor("#9A85C6")
PALE = QColor("#F1ECFC")
PANEL = QColor("#DDD2F5")
PAPER = QColor("#FFFCFF")
GOLD = QColor("#F4AD45")
NAME_PLUS = QColor("#E0952B")
BACKGROUND = QColor("#F6F2FD")
BORDER = QColor("#E4E0E8")

# (x, y, width, height) of the four sound bars and the four transcript lines.
BARS = [(112, 316, 18, 28), (140, 304, 18, 52), (168, 296, 18, 68), (196, 309, 18, 42)]
LINES = [(259, 300, 146, 16), (259, 332, 146, 16), (259, 364, 115, 16), (259, 409, 126, 16)]


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def progress(t: float, start: float, length: float) -> float:
    return clamp((t - start) / length) if length > 0 else float(t >= start)


def ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    return 3 * x * x - 2 * x * x * x


def overshoot(x: float, amount: float = 1.6) -> float:
    x -= 1
    return 1 + x * x * ((amount + 1) * x + amount)


class StartupSplash(QWidget):
    """Frameless splash window that animates once and hands over to the app."""

    finished = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(440, 340)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to skip")
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.tick)
        self.skipped = False
        self.window: QWidget | None = None
        self.fading = False
        self.closed = False
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2)

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.clock.start()
        self.timer.start()
        self.show()
        self.raise_()

    def elapsed(self) -> float:
        if self.skipped or not self.clock.isValid():
            return DURATION
        return self.clock.elapsed() / 1000.0

    def tick(self) -> None:
        self.update()
        if self.elapsed() >= DURATION:
            self.maybe_hand_over()

    def skip(self) -> None:
        self.skipped = True
        self.update()
        self.maybe_hand_over()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.skip()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        self.skip()

    def hand_over(self, window: QWidget) -> None:
        """Called when the main window is built; it is shown when the animation ends."""
        self.window = window
        self.maybe_hand_over()

    def maybe_hand_over(self) -> None:
        if self.fading or self.window is None or self.elapsed() < DURATION:
            return
        self.fading = True
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.raise_()
        fade_clock = QElapsedTimer()
        fade_clock.start()

        def fade_step() -> None:
            opacity = 1.0 - fade_clock.elapsed() / FADE_MS
            if opacity <= 0:
                self.timer.stop()
                fade_timer.stop()
                self.closed = True
                self.close()
                self.finished.emit()
            else:
                self.setWindowOpacity(opacity)

        fade_timer = QTimer(self)
        fade_timer.setInterval(15)
        fade_timer.timeout.connect(fade_step)
        fade_timer.start()

    # --- drawing ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        painter.setPen(QPen(BORDER, 1))
        painter.setBrush(BACKGROUND)
        painter.drawRoundedRect(card, 18, 18)
        t = self.elapsed()

        logo_size = 200
        painter.save()
        painter.translate((self.width() - logo_size) / 2, 34)
        painter.scale(logo_size / 512, logo_size / 512)
        self.draw_logo(painter, t)
        painter.restore()
        self.draw_name(painter, t, QRectF(0, 250, self.width(), 56))
        painter.end()

    def draw_logo(self, p: QPainter, t: float) -> None:
        # Slide frame, rising in behind.
        back = ease_out(progress(t, 0.75, 0.3))
        if back > 0:
            p.save()
            p.setOpacity(back)
            p.translate(0, (1 - back) * 24)
            self.rounded(p, 55, 63, 402, 306, 35, PURPLE)
            self.rounded(p, 82, 91, 348, 202, 18, PALE)
            self.rounded(p, 107, 117, 125, 145, 15, PANEL)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PURPLE_MID)
            p.drawEllipse(QPointF(169.5, 174), 34, 34)
            wedge = QPainterPath(QPointF(169.5, 174))
            wedge.arcTo(QRectF(135.5, 140, 68, 68), 90, -90)
            wedge.closeSubpath()
            p.setBrush(GOLD)
            p.drawPath(wedge)
            p.setBrush(PANEL)
            p.drawEllipse(QPointF(169.5, 174), 11, 11)
            self.rounded(p, 126, 225, 87, 14, 7, PURPLE_MID)
            self.rounded(p, 252, 129, 143, 16, 8, PURPLE)
            self.rounded(p, 252, 162, 112, 14, 7, LAVENDER)
            self.rounded(p, 252, 190, 130, 14, 7, LAVENDER)
            p.restore()

        # Sound bars settling back into their place.
        for index, (x, y, w, h) in enumerate(BARS):
            settle = progress(t, 0.95 + index * 0.05, 0.2)
            if settle > 0:
                scale = overshoot(settle, 2.2)
                p.save()
                p.translate(x + w / 2, y + h / 2)
                p.scale(1, max(0.0, scale))
                self.rounded(p, -w / 2, -h / 2, w, h, w / 2, GOLD)
                p.restore()

        # Transcript page.
        page = ease_out(progress(t, 0.3, 0.2))
        if page > 0:
            p.save()
            p.setOpacity(page)
            p.translate(337, 327)
            p.scale(0.88 + 0.12 * page, 0.88 + 0.12 * page)
            p.translate(-337, -327)
            outline = QPainterPath()
            outline.moveTo(257, 197)
            outline.lineTo(371, 197)
            outline.lineTo(443, 269)
            outline.lineTo(443, 431)
            outline.quadTo(443, 457, 417, 457)
            outline.lineTo(257, 457)
            outline.quadTo(231, 457, 231, 431)
            outline.lineTo(231, 223)
            outline.quadTo(231, 197, 257, 197)
            outline.closeSubpath()
            pen = QPen(PURPLE, 14)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(PAPER)
            p.drawPath(outline)
            fold = QPainterPath()
            fold.moveTo(371, 201)
            fold.lineTo(371, 253)
            fold.quadTo(371, 269, 387, 269)
            fold.lineTo(439, 269)
            pen = QPen(PURPLE, 12)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(fold)
            p.restore()

            accent = ease_out(progress(t, 0.55, 0.13))
            if accent > 0:
                self.rounded(p, 259, 248, 74 * accent, 17, 8.5, GOLD)
            for index, (x, y, w, h) in enumerate(LINES):
                grow = ease_out(progress(t, 0.6 + index * 0.05, 0.15))
                if grow > 0:
                    self.rounded(p, x, y, max(h, w * grow), h, h / 2, PURPLE_MID)
            dot = ease_out(progress(t, 0.8, 0.1))
            if dot > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(GOLD)
                p.drawEllipse(QPointF(402, 417), 8 * dot, 8 * dot)

        # Sound bars: pulse at the centre, then turn and fly into the lines.
        for index, (x, y, w, h) in enumerate(BARS):
            self.draw_flying_bar(p, t, index, x, y, w, h)

        # The "+" badge.
        plus = progress(t, 1.15, 0.22)
        if plus > 0:
            scale = overshoot(plus, 2.4)
            p.save()
            p.translate(452, 72)
            p.scale(scale, scale)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(GOLD)
            p.drawEllipse(QPointF(0, 0), 46, 46)
            self.rounded(p, -5, -26, 10, 52, 5, PURPLE)
            self.rounded(p, -26, -5, 52, 10, 5, PURPLE)
            p.restore()

    def draw_flying_bar(self, p: QPainter, t: float, index: int, x: float, y: float, w: float, h: float) -> None:
        if t >= 0.86:
            return
        start = (256 + (index - 1.5) * 44, 250)
        line_x, line_y, line_w, line_h = LINES[index]
        end = (line_x + line_w / 2, line_y + line_h / 2)
        appear = ease_out(progress(t, 0.0, 0.08))
        if t < 0.45:
            pulse = 1.0 + 0.32 * math.sin((t / 0.45) * math.pi * 2.0 + index * 0.9) * (1 - t / 0.45 * 0.6)
            px, py, rotation, sx, sy, opacity = start[0], start[1], 0.0, 1.6, 1.6 * appear * pulse, appear
        else:
            move = ease_in_out(progress(t, 0.45, 0.3))
            px = start[0] + (end[0] - start[0]) * move
            py = start[1] + (end[1] - start[1]) * move
            rotation = 90 * move
            sx = sy = 1.6 - 0.6 * move
            opacity = 1.0 - progress(t, 0.75, 0.1)
        if opacity <= 0:
            return
        p.save()
        p.setOpacity(opacity)
        p.translate(px, py)
        p.rotate(rotation)
        p.scale(sx, sy)
        self.rounded(p, -w / 2, -h / 2, w, h, w / 2, GOLD)
        p.restore()

    def draw_name(self, p: QPainter, t: float, area: QRectF) -> None:
        focus = ease_out(progress(t, 1.2, 0.35))
        if focus <= 0:
            return
        font = QFont("Segoe UI", 22)
        font.setWeight(QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, (1 - focus) * 10)
        p.setFont(font)
        metrics = p.fontMetrics()
        name, plus = "LectureWeave", "+"
        width = metrics.horizontalAdvance(name) + metrics.horizontalAdvance(plus)
        left = area.center().x() - width / 2
        baseline = area.center().y() + metrics.ascent() / 2 - 2
        blur = (1 - focus) * 6
        copies = [(0.0, 0.0)] if blur < 0.4 else [
            (math.cos(a) * blur, math.sin(a) * blur) for a in [i * math.pi / 4 for i in range(8)]
        ]
        alpha = focus / len(copies) if len(copies) > 1 else focus
        for dx, dy in copies:
            p.setOpacity(min(1.0, alpha * (1.6 if len(copies) > 1 else 1)))
            p.setPen(PURPLE)
            p.drawText(QPointF(left + dx, baseline + dy), name)
            p.setPen(NAME_PLUS)
            p.drawText(QPointF(left + metrics.horizontalAdvance(name) + dx, baseline + dy), plus)
        p.setOpacity(1.0)

    @staticmethod
    def rounded(p: QPainter, x: float, y: float, w: float, h: float, r: float, colour: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(QRectF(x, y, w, h), r, r)
