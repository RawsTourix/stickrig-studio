"""Frame strip: preview-first cards with bottom actions and right drag handle.

A single Qt delegate paints every frame. There are no per-frame child widgets.
Hit targets are shared by painting, mouse handling and tests.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QByteArray, QElapsedTimer, QEasingCurve, QMimeData, QPoint, QRect, QRectF,
    QSize, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import QColor, QCursor, QDrag, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QLabel, QListWidget, QMenu, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem,
)

from .i18n import tr

FRAME_MIME = "application/x-stickrig-frame"
ACTION_NAMES = ("toggle", "duplicate", "delete")
CARD_HEIGHT = 154
ACTION_SIZE = 30
ACTION_GAP = 7
HANDLE_WIDTH = 34


def frame_drag_rect(rect: QRect) -> QRect:
    """The only area permitted to initiate reordering (right side)."""
    return QRect(rect.right() - HANDLE_WIDTH - 5, rect.top() + 9,
                 HANDLE_WIDTH, max(20, rect.height() - 18))


def frame_action_rects(rect: QRect) -> dict[str, QRect]:
    """Three square buttons in a bottom row, never overlapping the handle."""
    left = rect.left() + 13
    top = rect.bottom() - ACTION_SIZE - 10
    return {
        name: QRect(left + i * (ACTION_SIZE + ACTION_GAP), top, ACTION_SIZE, ACTION_SIZE)
        for i, name in enumerate(ACTION_NAMES)
    }


def frame_preview_rect(rect: QRect) -> QRect:
    """Most of the card is dedicated to the frame's actual thumbnail."""
    handle = frame_drag_rect(rect)
    actions = frame_action_rects(rect)
    return QRect(rect.left() + 11, rect.top() + 10,
                 max(16, handle.left() - rect.left() - 21),
                 max(16, actions["toggle"].top() - rect.top() - 21))


def _paint_action_icon(painter: QPainter, name: str, rect: QRect, hidden: bool):
    """Small vector icons: eye/eye-off, duplicate sheets, trash can."""
    center = rect.center()
    x, y = center.x(), center.y()
    pen = QPen(QColor("#465c74"), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if name == "toggle":
        eye = QPainterPath()
        eye.moveTo(x - 9, y)
        eye.cubicTo(x - 4, y - 7, x + 4, y - 7, x + 9, y)
        eye.cubicTo(x + 4, y + 7, x - 4, y + 7, x - 9, y)
        painter.drawPath(eye)
        painter.drawEllipse(QRectF(x - 2.4, y - 2.4, 4.8, 4.8))
        if hidden:
            painter.setPen(QPen(QColor("#c65555"), 2.2))
            painter.drawLine(x - 10, y + 10, x + 10, y - 10)
    elif name == "duplicate":
        painter.drawRoundedRect(QRectF(x - 8, y - 8, 11, 12), 1, 1)
        painter.setBrush(QColor("#f8fafc"))
        painter.drawRoundedRect(QRectF(x - 3, y - 4, 11, 12), 1, 1)
    elif name == "delete":
        painter.drawLine(x - 8, y - 6, x + 8, y - 6)
        painter.drawLine(x - 3, y - 9, x + 3, y - 9)
        painter.drawRoundedRect(QRectF(x - 6, y - 4, 12, 13), 1.5, 1.5)
        painter.drawLine(x - 2, y - 1, x - 2, y + 6)
        painter.drawLine(x + 2, y - 1, x + 2, y + 6)


class FrameDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(245, CARD_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        view = self.parent()
        row = index.row()
        if getattr(view, "_drag_active", False):
            if row == view._active_source:
                painter.setOpacity(0.12)
            else:
                shifted = QStyleOptionViewItem(option)
                shifted.rect = option.rect.translated(
                    0, round(view._gap_offsets.get(row, 0.0))
                )
                option = shifted
        if row == getattr(view, "_settle_row", -1):
            painter.setOpacity(0.25 + 0.75 * view._settle_progress)
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        hidden = bool(index.data(Qt.ItemDataRole.UserRole + 2))
        card_pen = QColor("#8ca7c5") if selected else QColor("#d6dee8")
        card_fill = QColor("#eff6ff") if selected else (
            QColor("#f8fafc") if hovered else QColor("#ffffff")
        )
        painter.setPen(QPen(card_pen, 1.2))
        painter.setBrush(card_fill)
        painter.drawRoundedRect(rect, 8, 8)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#7c3aed"))
            painter.drawRoundedRect(QRect(rect.left() + 1, rect.top() + 8, 3,
                                          rect.height() - 16), 1, 1)

        image_rect = frame_preview_rect(rect)
        painter.setPen(QPen(QColor("#e1e7ee"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(image_rect, 5, 5)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(image_rect.size())
            if not pixmap.isNull():
                pixmap = pixmap.scaled(image_rect.size(),
                                      Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                at = QPoint(image_rect.center().x() - pixmap.width() // 2,
                            image_rect.center().y() - pixmap.height() // 2)
                painter.drawPixmap(at, pixmap)
        if hidden:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(240, 244, 248, 135))
            painter.drawRoundedRect(image_rect, 5, 5)

        # Label follows the quick-action row, keeping the thumbnail unobstructed.
        label_left = frame_action_rects(rect)["delete"].right() + 11
        label_right = frame_drag_rect(rect).left() - 5
        painter.setPen(QColor("#536477") if hidden else QColor("#29394b"))
        label_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(QRect(label_left, rect.bottom() - 40,
                               max(5, label_right - label_left), 32),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         label_text)

        for action, area in frame_action_rects(rect).items():
            painter.setPen(QPen(QColor("#b4c4d6"), 1))
            painter.setBrush(QColor("#e7eef7") if action == "toggle" and hidden
                             else QColor("#f7f9fc"))
            painter.drawRoundedRect(area, 5, 5)
            _paint_action_icon(painter, action, area, hidden)

        handle = frame_drag_rect(rect)
        painter.setPen(QPen(QColor("#d5dfe8"), 1))
        painter.setBrush(QColor("#f1f5f9") if hovered else QColor("#f8fafc"))
        painter.drawRoundedRect(handle, 5, 5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#64748b"))
        cx, cy = handle.center().x(), handle.center().y()
        for offset_y in (-9, -3, 3, 9):
            for offset_x in (-4, 4):
                painter.drawEllipse(QRectF(cx + offset_x - 1.5,
                                           cy + offset_y - 1.5, 3, 3))
        painter.restore()


class FrameStrip(QListWidget):
    """Context menus and quick actions, drag enabled only from right handle."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setItemDelegate(FrameDelegate(self))
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDropIndicatorShown(False)  # Our own insertion line follows the pointer.
        self.setAutoScroll(False)  # Controlled auto-scroll remains active while pointer is held.
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setSpacing(4)
        self.setMouseTracking(True)

        self._drag_source = None
        self._drag_origin = QPoint()
        self._drag_active = False
        self._active_source = -1
        self._drop_slot = None
        self._drop_target = None
        self._gap_offsets: dict[int, float] = {}
        self._gap_to: dict[int, float] = {}
        self._slot_hysteresis_px = 8
        self._drag_card: QPixmap | None = None
        self._drag_hotspot = QPoint()
        self._pending_settle = None
        self._settle_row = -1
        self._settle_progress = 1.0
        self._settle_overlay = None
        self._settle_animation = None

        # A single clock continuously chases the latest target; changing the
        # destination never restarts an easing animation from zero velocity.
        self._gap_clock = QElapsedTimer()
        self._gap_timer = QTimer(self)
        self._gap_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._gap_timer.setInterval(16)
        self._gap_timer.timeout.connect(self._step_gap)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._auto_scroll)
        self._scroll_clock = QElapsedTimer()
        self._scroll_velocity = 0.0
        self._scroll_fraction = 0.0

    def _frame_at(self, pos):
        item = self.itemAt(pos)
        return self.row(item) if item else -1

    def _card_rect(self, row):
        return self.visualItemRect(self.item(row)).adjusted(3, 3, -3, -3)

    def _quick_action(self, action, index):
        if action == "toggle":
            self.editor.toggle_frame_visibility(index)
        elif action == "duplicate":
            self.editor.duplicate_frame_at(index)
        else:
            self.editor.delete_frame_at(index)

    def _drag_pixmap(self, row):
        """Render one item with its existing preview, not a full list screenshot."""
        item_rect = self.visualItemRect(self.item(row))
        card = QPixmap(item_rect.size())
        card.fill(Qt.GlobalColor.transparent)
        painter = QPainter(card)
        painter.setOpacity(0.82)
        option = QStyleOptionViewItem()
        option.rect = QRect(QPoint(), item_rect.size())
        option.state = QStyle.StateFlag.State_Selected
        option.widget = self
        self.itemDelegate().paint(painter, option, self.model().index(row, 0))
        painter.end()
        return card

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            row = self._frame_at(event.position().toPoint())
            self._drag_source = None
            if row >= 0:
                rect = self._card_rect(row)
                for name, target in frame_action_rects(rect).items():
                    if target.contains(event.position().toPoint()):
                        self._quick_action(name, row)
                        event.accept()
                        return
                if frame_drag_rect(rect).contains(event.position().toPoint()):
                    self._drag_source = row
                    self._drag_origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_source is not None and
                event.buttons() & Qt.MouseButton.LeftButton and
                (event.position().toPoint() - self._drag_origin).manhattanLength() >=
                QApplication.startDragDistance()):
            row = self._drag_source
            origin = QPoint(self._drag_origin)
            self._drag_source = None
            self._start_drag(row, origin)
            return
        pos = event.position().toPoint()
        row = self._frame_at(pos)
        if row >= 0:
            rect = self._card_rect(row)
            in_grip = frame_drag_rect(rect).contains(pos)
            in_action = any(r.contains(pos) for r in frame_action_rects(rect).values())
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if in_grip else
                Qt.CursorShape.PointingHandCursor if in_action else
                Qt.CursorShape.ArrowCursor
            )
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_source = None
        super().mouseReleaseEvent(event)

    def _start_drag(self, source: int, origin: QPoint):
        if self.count() < 2:
            return
        self._cancel_settle()
        rect = self.visualItemRect(self.item(source))
        self._drag_card = self._drag_pixmap(source)
        self._drag_hotspot = origin - rect.topLeft()
        self._drag_active = True
        self._active_source = source
        self._drop_target = source
        self._drop_slot = source
        self._gap_timer.stop()
        self._gap_offsets.clear()
        self._gap_to.clear()
        self._scroll_velocity = 0.0
        self._scroll_fraction = 0.0
        self._scroll_clock.start()
        self.viewport().update()
        self._scroll_timer.start()

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(FRAME_MIME, QByteArray(str(source).encode("ascii")))
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_card)
        drag.setHotSpot(self._drag_hotspot)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._finish_drag()

    def _finish_drag(self):
        self._scroll_timer.stop()
        self._gap_timer.stop()
        self._drag_active = False
        self._active_source = -1
        self._drop_slot = None
        self._drop_target = None
        self._gap_offsets.clear()
        self._gap_to.clear()
        self._scroll_velocity = 0.0
        self._scroll_fraction = 0.0
        self.viewport().update()
        if self._pending_settle is not None:
            QTimer.singleShot(0, self._animate_settle)
        else:
            self._drag_card = None

    def _visible_rows(self) -> range:
        """Limit animation bookkeeping to rows near the viewport, even for 100k frames."""
        if not self.count():
            return range(0)
        h = self.viewport().height()
        top = self.indexAt(QPoint(8, 0))
        bottom = self.indexAt(QPoint(8, max(0, h - 1)))
        lo = top.row() if top.isValid() else max(0, self.indexAt(QPoint(8, min(10, h // 2))).row())
        hi = bottom.row() if bottom.isValid() else lo + max(6, h // (CARD_HEIGHT + 4) + 3)
        return range(max(0, lo - 2), min(self.count(), max(lo, hi) + 3))

    def _offset_targets(self, destination: int) -> dict[int, float]:
        source = self._active_source
        if source < 0 or destination == source:
            return {}
        step = CARD_HEIGHT + self.spacing()
        if source < destination:
            return {row: -float(step) for row in self._visible_rows()
                    if source < row <= destination}
        return {row: float(step) for row in self._visible_rows()
                if destination <= row < source}

    def _animate_gap(self, destination: int, *, immediate=False):
        if not self._drag_active:
            return
        targets = self._offset_targets(destination)
        if destination == self._drop_target and targets == self._gap_to:
            return
        self._drop_target = destination
        self._gap_to = targets
        if immediate:
            self._gap_offsets = dict(targets)
            self._gap_timer.stop()
            self.viewport().update()
        elif not self._gap_timer.isActive():
            self._gap_clock.start()
            self._gap_timer.start()

    def _step_gap(self, dt_ms: float | None = None):
        """Retargetable, frame-rate-independent smoothing.

        A live drag can cross several cards in less than 140 ms. Restarting
        a QVariantAnimation for every destination used to reset the easing
        repeatedly and caused visible pauses/jumps. This clock never restarts
        while following the cursor; it converges toward the newest offsets.
        """
        if not self._drag_active:
            self._gap_timer.stop()
            return
        if dt_ms is None:
            dt_ms = self._gap_clock.restart()
        dt = max(1.0, min(48.0, float(dt_ms)))
        blend = 1.0 - math.exp(-dt / 62.0)
        rows = self._gap_offsets.keys() | self._gap_to.keys()
        offsets = {}
        moving = False
        for row in rows:
            start = self._gap_offsets.get(row, 0.0)
            target = self._gap_to.get(row, 0.0)
            current = start + (target - start) * blend
            if abs(target - current) < 0.45:
                current = target
            else:
                moving = True
            if abs(current) >= 0.45:
                offsets[row] = current
        self._gap_offsets = offsets
        if not moving:
            self._gap_timer.stop()
        self.viewport().update()

    def _insertion_slot(self, point: QPoint) -> int:
        if not self.count():
            return 0
        target = self.indexAt(point)
        if target.isValid():
            row = target.row()
            rect = self.visualItemRect(self.item(row))
            return row + int(point.y() > rect.center().y())
        rows = list(self._visible_rows())
        if not rows:
            return self.count()
        first, last = rows[0], rows[-1]
        return first if point.y() < self.visualItemRect(self.item(first)).center().y() else min(self.count(), last + 1)

    def _stable_slot(self, proposed: int, pointer: QPoint) -> int:
        """Small hysteresis around row midpoints prevents slot flickering."""
        previous = self._drop_slot
        if previous is None or proposed == previous or abs(proposed - previous) > 1:
            return proposed
        boundary = min(previous, proposed)
        if not 0 <= boundary < self.count():
            return proposed
        midpoint = self.visualItemRect(self.item(boundary)).center().y()
        margin = self._slot_hysteresis_px
        if proposed > previous and pointer.y() < midpoint + margin:
            return previous
        if proposed < previous and pointer.y() > midpoint - margin:
            return previous
        return proposed

    def _update_target(self, point: QPoint):
        if not self._drag_active:
            return
        slot = self._stable_slot(self._insertion_slot(point), point)
        destination = slot - int(slot > self._active_source)
        destination = max(0, min(self.count() - 1, destination))
        changed = slot != self._drop_slot
        self._drop_slot = slot
        self._animate_gap(destination)
        if changed:
            self.viewport().update()

    def _insertion_line_y(self):
        slot = self._drop_slot
        if slot is None or not self.count():
            return None
        if slot < self.count():
            return self.visualItemRect(self.item(slot)).top() - self.spacing() // 2
        return self.visualItemRect(self.item(self.count() - 1)).bottom() + self.spacing() // 2

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._drag_active or self._drop_slot is None:
            return
        y = self._insertion_line_y()
        if y is None or y < 1 or y >= self.viewport().height():
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#8255d5"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(14, y, max(15, self.viewport().width() - 14), y)
        painter.setBrush(QColor("#8255d5"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(8, y - 4, 8, 8))
        painter.end()

    def _auto_scroll(self, local: QPoint | None = None, dt_ms: float | None = None):
        if not self._drag_active:
            return
        if local is None:
            local = self.viewport().mapFromGlobal(QCursor.pos())
        if dt_ms is None:
            dt_ms = self._scroll_clock.restart()
        dt = max(1.0, min(48.0, float(dt_ms)))
        rect = self.viewport().rect()
        if not (-45 <= local.x() <= rect.width() + 45 or
                0 <= local.y() <= rect.height()):
            self._scroll_velocity = 0.0
            self._scroll_fraction = 0.0
            return
        band = min(65, max(20, rect.height() // 4))
        requested = 0.0
        if local.y() < band:
            requested = -590.0 * (band - local.y()) / band
        elif local.y() > rect.height() - band:
            requested = 590.0 * (local.y() - (rect.height() - band)) / band
        # Low-pass velocity changes, including when exiting the edge band.
        blend = 1.0 - math.exp(-dt / 42.0)
        self._scroll_velocity += (requested - self._scroll_velocity) * blend
        if abs(self._scroll_velocity) < 1.0:
            self._scroll_velocity = 0.0
            self._scroll_fraction = 0.0
            return
        self._scroll_fraction += self._scroll_velocity * dt / 1000.0
        whole = math.trunc(self._scroll_fraction)
        self._scroll_fraction -= whole
        if whole:
            scroll = self.verticalScrollBar()
            previous = scroll.value()
            scroll.setValue(previous + whole)
            if scroll.value() != previous:
                self._update_target(local)
            else:
                # Stop residual scroll at physical boundaries.
                self._scroll_velocity = 0.0
                self._scroll_fraction = 0.0

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(FRAME_MIME) and event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(FRAME_MIME) and event.source() is self:
            self._update_target(event.position().toPoint())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_slot = None
        self._animate_gap(self._active_source)
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):
        if (not self._drag_active or event.source() is not self or
                not event.mimeData().hasFormat(FRAME_MIME)):
            event.ignore()
            return
        try:
            source = int(bytes(event.mimeData().data(FRAME_MIME)).decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            event.ignore()
            return
        if source != self._active_source or not 0 <= source < self.count():
            event.ignore()
            return
        self._update_target(event.position().toPoint())
        destination = self._drop_target
        if destination is None:
            event.ignore()
            return
        # Domain operation happens exactly once, after the final target is known.
        start = event.position().toPoint() - self._drag_hotspot
        self._pending_settle = (start, destination, self._drag_card)
        if source != destination:
            self.editor.move_frame_to(source, destination)
        event.acceptProposedAction()

    def _cancel_settle(self):
        if self._settle_animation is not None:
            self._settle_animation.stop()
            self._settle_animation.deleteLater()
            self._settle_animation = None
        if self._settle_overlay is not None:
            self._settle_overlay.deleteLater()
            self._settle_overlay = None
        self._settle_row = -1
        self._settle_progress = 1.0
        self._pending_settle = None

    def _animate_settle(self):
        pending = self._pending_settle
        self._pending_settle = None
        if pending is None or self._drag_active:
            return
        start, destination, pixmap = pending
        if (pixmap is None or not 0 <= destination < self.count() or
                not self.visualItemRect(self.item(destination)).intersects(self.viewport().rect())):
            self._drag_card = None
            return
        self._cancel_settle()
        end = self.visualItemRect(self.item(destination)).topLeft()
        ghost = QLabel(self.viewport())
        ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ghost.setPixmap(pixmap)
        ghost.resize(pixmap.size())
        ghost.move(start)
        effect = QGraphicsOpacityEffect(ghost)
        ghost.setGraphicsEffect(effect)
        effect.setOpacity(0.84)
        ghost.show()
        ghost.raise_()
        self._settle_overlay = ghost
        self._settle_row = destination
        self._settle_progress = 0.0

        animation = QVariantAnimation(self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._settle_animation = animation

        def step(value):
            progress = float(value)
            ghost.move(round(start.x() + (end.x() - start.x()) * progress),
                       round(start.y() + (end.y() - start.y()) * progress))
            effect.setOpacity(0.84 * (1.0 - progress))
            self._settle_progress = progress
            self.viewport().update(self.visualItemRect(self.item(destination)))

        def finish():
            if self._settle_animation is animation:
                self._cancel_settle()
                self._drag_card = None
                self.viewport().update()

        animation.valueChanged.connect(step)
        animation.finished.connect(finish)
        animation.start()

    def contextMenuEvent(self, event):
        language = getattr(self.editor, "ui_language", "en")
        label = lambda key: tr("frames." + key, language)
        row = self._frame_at(event.pos())
        menu = QMenu(self)
        if row < 0:
            menu.addAction(label("new_at_end"), self.editor.append_blank_frame)
            menu.addSeparator()
            menu.addAction(label("duplicate_current"), self.editor.add_frame)
        else:
            if self.currentRow() != row:
                self.setCurrentRow(row)
            hidden = row in self.editor.project.scene.get("hidden_frames", [])
            menu.addAction(label("show") if hidden else label("hide"),
                           lambda: self.editor.toggle_frame_visibility(row))
            menu.addAction(label("duplicate"), lambda: self.editor.duplicate_frame_at(row))
            menu.addAction(label("insert_after"), lambda: self.editor.insert_blank_frame_after(row))
            menu.addSeparator()
            above = menu.addAction(label("move_up"),
                                   lambda: self.editor.move_frame_to(row, row - 1))
            above.setEnabled(row > 0)
            below = menu.addAction(label("move_down"),
                                   lambda: self.editor.move_frame_to(row, row + 1))
            below.setEnabled(row + 1 < self.count())
            menu.addSeparator()
            remove = menu.addAction(label("delete"),
                                    lambda: self.editor.delete_frame_at(row))
            remove.setEnabled(self.count() > 1)
        menu.exec(event.globalPos())
