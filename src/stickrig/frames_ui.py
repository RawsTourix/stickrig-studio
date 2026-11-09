"""Virtualized frame-strip interactions.

The QListWidget contains lightweight items rather than thousands of child widgets.
Only visible thumbnails are rendered by MainWindow. The custom delegate paints
the grab handle and quick actions without allocating a widget for every frame.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from .i18n import tr

from PySide6.QtWidgets import (
    QApplication, QListWidget, QMenu, QStyledItemDelegate,
    QStyleOptionViewItem,
)

FRAME_MIME = "application/x-stickrig-frame"
ACTION_NAMES = ("toggle", "duplicate", "delete")


def frame_action_rects(rect):
    """Return the same compact hit targets used by painting and mouse handling."""
    top = rect.top() + max(3, (rect.height() - 26) // 2)
    return {
        "toggle": QRect(rect.right() - 90, top, 26, 26),
        "duplicate": QRect(rect.right() - 59, top, 26, 26),
        "delete": QRect(rect.right() - 28, top, 26, 26),
    }


class FrameDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(245, 94)

    def paint(self, painter, option, index):
        # Let Qt paint selection, thumbnail and frame label. Restrict its rect
        # to keep room for the drag handle and quick-action hit targets.
        opt = QStyleOptionViewItem(option)
        opt.rect = QRect(option.rect).adjusted(20, 0, -98, 0)
        super().paint(painter, opt, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        painter.setPen(QColor("#8b98a9"))
        painter.drawText(
            QRect(rect.left() + 1, rect.top(), 19, rect.height()),
            Qt.AlignmentFlag.AlignCenter, "≡"
        )
        hidden = bool(index.data(Qt.ItemDataRole.UserRole + 2))
        for name, hit in frame_action_rects(rect).items():
            painter.setPen(QPen(QColor("#9ba8b8"), 1))
            painter.setBrush(QColor("#f0f3f7"))
            painter.drawRoundedRect(hit, 5, 5)
            painter.setPen(QColor("#525b6a"))
            symbol = (
                ("○" if hidden else "●") if name == "toggle"
                else "D" if name == "duplicate" else "×"
            )
            painter.drawText(hit, Qt.AlignmentFlag.AlignCenter, symbol)
        painter.restore()


class FrameStrip(QListWidget):
    """Click actions, context menus and drag only from the left grab area."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setItemDelegate(FrameDelegate(self))
        self.setAcceptDrops(True)
        self.setDragEnabled(False)  # Only the dedicated handle may start a drag.
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self._drag_source = None
        self._drag_origin = QPoint()
        self.setMouseTracking(True)

    def _frame_at(self, pos):
        item = self.itemAt(pos)
        return self.row(item) if item else -1

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
                rect = self.visualItemRect(self.item(row))
                for name, target in frame_action_rects(rect).items():
                    if target.contains(event.position().toPoint()):
                        self._quick_action(name, row)
                        event.accept()
                        return
                if event.position().x() <= rect.left() + 20:
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
