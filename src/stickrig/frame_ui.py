"""Painted frame thumbnails, quick actions, and grip-only drag reordering.

No individual QWidget is allocated for each frame: a delegate paints its
controls, preserving responsiveness for long sequences.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QListWidget, QMenu, QStyle, QStyledItemDelegate,
)


class FrameDelegate(QStyledItemDelegate):
    ACTIONS = ("hide", "duplicate", "delete")
    SYMBOLS = {"hide": "◉", "duplicate": "⧉", "delete": "×"}

    def sizeHint(self, option, index):
        return QSize(240, 112)

    @staticmethod
    def action_rects(rect: QRect):
        right = rect.right() - 8
        top = rect.top() + 12
        return {
            action: QRect(right - (3 - i) * 27 + 1, top, 25, 25)
            for i, action in enumerate(FrameDelegate.ACTIONS)
        }

    @staticmethod
    def grip_rect(rect: QRect):
        return QRect(rect.left() + 3, rect.top() + 12, 29, rect.height() - 24)

    def hit_action(self, rect: QRect, point: QPoint):
        if self.grip_rect(rect).contains(point):
            return "drag"
        for name, action_rect in self.action_rects(rect).items():
            if action_rect.contains(point):
                return name
        return None

    def paint(self, painter: QPainter, option, index):
        painter.save()
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hidden = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        painter.setPen(QPen(QColor("#88a4bf") if selected else QColor("#c4cfda"), 1))
        painter.setBrush(QColor("#dcebf9") if selected else QColor("#fafbfd"))
        painter.drawRoundedRect(rect, 6, 6)

        grip = self.grip_rect(rect)
        painter.setPen(QPen(QColor("#778999"), 2))
        for y in (grip.center().y() - 6, grip.center().y(), grip.center().y() + 6):
            painter.drawLine(grip.center().x() - 5, y, grip.center().x() + 5, y)

        # Leave a right-hand action rail even on narrow resized docks.
        image_rect = QRect(rect.left() + 36, rect.top() + 8,
                           max(40, min(132, rect.width() - 150)), 76)
        painter.fillRect(image_rect, QColor("#ffffff"))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(image_rect.size())
            target = QRect(image_rect.x() + (image_rect.width() - pixmap.width()) // 2,
                           image_rect.y() + (image_rect.height() - pixmap.height()) // 2,
                           pixmap.width(), pixmap.height())
            painter.drawPixmap(target, pixmap)
        painter.setPen(QColor("#273746"))
        painter.drawText(QRect(rect.left() + 34, rect.bottom() - 25,
                               max(80, rect.width() - 45), 21),
                         Qt.AlignmentFlag.AlignVCenter, str(index.data(Qt.ItemDataRole.DisplayRole)))
        for action, box in self.action_rects(rect).items():
            active = action == "hide" and hidden
            painter.setPen(QColor("#c0392b") if action == "delete" else QColor("#50687f"))
            painter.setBrush(QColor("#f2d5d5") if active else QColor("#e9f0f6"))
            painter.drawRoundedRect(box, 4, 4)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter,
                             "○" if active else self.SYMBOLS[action])
        if hidden:
            painter.setBrush(QColor(255, 255, 255, 105))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(image_rect)
        painter.restore()


class FrameList(QListWidget):
    """Frame list with low-cost painted buttons and explicit drag handle."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.delegate = FrameDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.setMouseTracking(True)
        self._press_action = None
        self._press_row = -1
        self._drag_allowed = False
        self._drag_source = -1

    def _hit(self, position):
        index = self.indexAt(position)
        if not index.isValid():
            return -1, None
        return index.row(), self.delegate.hit_action(self.visualRect(index).adjusted(2, 2, -2, -2), position)

    def mousePressEvent(self, event):
        self._drag_allowed = False
        self._press_action = None
        self._press_row = -1
        if event.button() == Qt.MouseButton.LeftButton:
            row, action = self._hit(event.position().toPoint())
            self._drag_allowed = row >= 0 and action == "drag"
            self._drag_source = row if self._drag_allowed else -1
            if action in FrameDelegate.ACTIONS:
                self._press_row, self._press_action = row, action
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        row, action = self._hit(event.position().toPoint())
        pressed_row, pressed_action = self._press_row, self._press_action
        self._press_row, self._press_action = -1, None
        self._drag_allowed = False
        super().mouseReleaseEvent(event)
        if (event.button() == Qt.MouseButton.LeftButton and row == pressed_row and
                action == pressed_action and action in FrameDelegate.ACTIONS):
            self._run_action(action, row)

    def mouseMoveEvent(self, event):
        row, action = self._hit(event.position().toPoint())
        if action == "drag":
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            self.setToolTip("Drag handle to reorder frames")
        elif action in FrameDelegate.ACTIONS:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip({"hide": "Hide/show in preview and export",
                             "duplicate": "Duplicate frame",
                             "delete": "Delete frame"}[action])
        else:
            self.viewport().unsetCursor()
            self.setToolTip("Right-click for frame options")
        super().mouseMoveEvent(event)

    def startDrag(self, supportedActions):
        if self._drag_allowed and self._drag_source >= 0:
            super().startDrag(supportedActions)

    def dragEnterEvent(self, event):
        if event.source() is self and self._drag_allowed:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self and self._drag_allowed:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is not self or not self._drag_allowed:
            event.ignore()
            return
        destination = self.indexAt(event.position().toPoint()).row()
        if destination < 0:
            destination = self.count() - 1
        source = self._drag_source
        self._drag_allowed = False
        event.acceptProposedAction()
        if source >= 0 and destination >= 0 and source != destination:
            self.editor.move_frame_to(source, destination)

    def _run_action(self, action, row):
        if action == "hide":
            self.editor.toggle_frame_visibility(row)
        elif action == "duplicate":
            self.editor.duplicate_frame_at(row)
        elif action == "delete":
            self.editor.delete_frame_at(row)

    def _context_menu(self, position):
        row, _ = self._hit(position)
        menu = QMenu(self)
        if row < 0:
            menu.addAction("Add frame after current", self.editor.add_frame)
            menu.addAction("Add frame at end", self.editor.append_frame)
            menu.addSeparator()
            menu.addAction("Show all hidden frames", self.editor.unhide_all_frames)
        else:
            menu.addAction(f"Go to frame {row + 1}", lambda: self.editor.set_frame(row))
            menu.addAction("Duplicate frame", lambda: self.editor.duplicate_frame_at(row))
            hidden = row in self.editor.project.scene.get("hidden_frames", [])
            menu.addAction("Show frame" if hidden else "Hide frame",
                           lambda: self.editor.toggle_frame_visibility(row))
            menu.addSeparator()
            up = menu.addAction("Move frame up", lambda: self.editor.move_frame_to(row, row - 1))
            down = menu.addAction("Move frame down", lambda: self.editor.move_frame_to(row, row + 1))
            up.setEnabled(row > 0)
            down.setEnabled(row < self.count() - 1)
            menu.addSeparator()
            remove = menu.addAction("Delete frame", lambda: self.editor.delete_frame_at(row))
            remove.setEnabled(self.count() > 1)
        menu.exec(self.viewport().mapToGlobal(position))
