"""Frame strip: preview-first cards with bottom actions and right drag handle.

A single Qt delegate paints every frame. There are no per-frame child widgets.
Hit targets are shared by painting, mouse handling and tests.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QListWidget, QMenu, QStyle, QStyledItemDelegate,
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
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setSpacing(4)
        self._drag_source = None
        self._drag_origin = QPoint()
        self.setMouseTracking(True)

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
            self._drag_source = None
            drag = QDrag(self)
            payload = QMimeData()
            payload.setData(FRAME_MIME, QByteArray(str(row).encode("ascii")))
            drag.setMimeData(payload)
            drag.exec(Qt.DropAction.MoveAction)
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

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(FRAME_MIME) and event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(FRAME_MIME) and event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(FRAME_MIME) or event.source() is not self:
            event.ignore()
            return
        try:
            source = int(bytes(event.mimeData().data(FRAME_MIME)).decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            event.ignore()
            return
        if not 0 <= source < self.count():
            event.ignore()
            return
        target = self._frame_at(event.position().toPoint())
        if target < 0:
            target = self.count() - 1
        if source != target:
            self.editor.move_frame_to(source, target)
        event.acceptProposedAction()

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
