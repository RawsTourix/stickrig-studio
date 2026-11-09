"""StickRig Studio — practical first Qt desktop editor.

Usage: python -m stickrig.app [optional project.stickrig.json]
"""

from __future__ import annotations

import copy
import math
import sys
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QBrush, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDoubleSpinBox,
    QDockWidget, QFileDialog, QFrame, QGraphicsScene, QGraphicsView, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSpinBox,
    QStatusBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .model import (History, Project, RIG_OFFSETS, angle_for_drag, delete_frame, disk_project_changed,
                    get_entity, get_layer, insert_frame, joints_at, load_project, move_frame,
                    make_shape, make_stickman, playable_frames, save_project, set_key,
                    toggle_frame_hidden, value_at)
from .render import export_mp4, render_image, visible_entities
from .frame_ui import FrameList
from .view_math import pan_sensitivity


def _distance_segment(point, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return math.dist(point, a)
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / denom))
    return math.dist(point, (ax + t * dx, ay + t * dy))


class Canvas(QGraphicsView):
    def __init__(self, editor: "MainWindow"):
        super().__init__()
        self.editor = editor
        self.graphics = QGraphicsScene(self)
        self.setScene(self.graphics)
        self.graphics.setSceneRect(-2000, -2000, 6000, 6000)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QColor("#dfe4eb"))
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.drag_id: str | None = None
        self.drag_joint: str | None = None
        self.drag_start: tuple[float, float] | None = None
        self.drag_origin: tuple[float, float] | None = None
        self.drag_before = None
        self.shape_start: tuple[float, float] | None = None
        self.shape_end: tuple[float, float] | None = None
        self.panning = False
        self.pan_start: QPoint | None = None
        self._pan_residual = [0.0, 0.0]
        self._hover_point: tuple[float, float] | None = None
        self._hover_item = None
        self._make_zoom_toolbar()
        self.redraw()

    def to_world(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return p.x(), p.y()

    def snap(self, point: tuple[float, float]) -> tuple[float, float]:
        if self.editor.snap_check.isChecked():
            return tuple(round(v / 10) * 10.0 for v in point)
        return point

    def _make_zoom_toolbar(self):
        """Overlay view zoom in the bottom-right of the canvas (not camera zoom)."""
        bar = QFrame(self.viewport())
        bar.setObjectName("canvasZoomToolbar")
        bar.setStyleSheet(
            "QFrame#canvasZoomToolbar {background: #f7fafce8; border: 1px solid #b9c6d2;"
            "border-radius: 7px;} QToolButton {border: 0; padding: 3px 8px;}"
            "QToolButton:hover {background: #dae6f2;}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        self.zoom_minus = QToolButton(bar)
        self.zoom_minus.setText("−")
        self.zoom_minus.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        self.zoom_percent = QToolButton(bar)
        self.zoom_percent.setToolTip("Viewport scale; click for 100%")
        self.zoom_percent.clicked.connect(self.reset_view_zoom)
        self.zoom_plus = QToolButton(bar)
        self.zoom_plus.setText("+")
        self.zoom_plus.clicked.connect(lambda: self.zoom_by(1.2))
        for button in (self.zoom_minus, self.zoom_percent, self.zoom_plus):
            layout.addWidget(button)
        self.zoom_toolbar = bar
        self.update_zoom_toolbar()

    def update_zoom_toolbar(self):
        scale = self.transform().m11()
        self.zoom_percent.setText(f"{scale * 100:.0f}%")
        self.zoom_toolbar.adjustSize()
        if self.viewport().width() > 0:
            self.zoom_toolbar.move(
                max(0, self.viewport().width() - self.zoom_toolbar.width() - 12),
                max(0, self.viewport().height() - self.zoom_toolbar.height() - 12),
            )
        self.zoom_toolbar.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "zoom_toolbar"):
            self.update_zoom_toolbar()

    def zoom_by(self, factor: float):
        current = self.transform().m11()
        if 0.05 <= current * factor <= 12:
            self.scale(factor, factor)
            self.update_zoom_toolbar()

    def reset_view_zoom(self):
        self.resetTransform()
        self.update_zoom_toolbar()

    def frame_camera(self) -> None:
        project = self.editor.project
        cam = project.scene["camera"]
        width, height = project.width / cam["zoom"], project.height / cam["zoom"]
        x, y = cam["center"]
        self.fitInView(QRectF(x - width / 2 - 40, y - height / 2 - 40,
                              width + 80, height + 80),
                       Qt.AspectRatioMode.KeepAspectRatio)
        self.update_zoom_toolbar()

    def pan_by_pixels(self, dx: float, dy: float):
        """Convert touchpad pixel motion to scene scroll with fractional carry."""
        zoom = max(0.05, self.transform().m11())
        factor = pan_sensitivity(zoom) / zoom
        for axis, (delta, bar) in enumerate((
            (dx, self.horizontalScrollBar()), (dy, self.verticalScrollBar())
        )):
            self._pan_residual[axis] += delta * factor
            steps = math.trunc(self._pan_residual[axis])
            if steps:
                self._pan_residual[axis] -= steps
                bar.setValue(bar.value() - steps)

    def wheelEvent(self, event):
        """Bare touchpad swipe pans; Ctrl+wheel/pinch zooms about the pointer."""
        pixels, angles = event.pixelDelta(), event.angleDelta()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = pixels.y() if pixels.y() else angles.y() / 120 * 56
            if dy:
                self.zoom_by(math.exp(max(-4.0, min(4.0, dy / 280))))
        else:
            dx = pixels.x() if pixels.x() else angles.x() / 120 * 56
            dy = pixels.y() if pixels.y() else angles.y() / 120 * 56
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not dx:
                dx, dy = dy, 0
            self.pan_by_pixels(dx, dy)
        event.accept()

    def _line(self, a, b, color, width, *, z=0, opacity=1.0, dash=False):
        pen = QPen(QColor(color), width)
        pen.setCosmetic(False)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if dash:
            pen.setStyle(Qt.PenStyle.DashLine)
        item = self.graphics.addLine(a[0], a[1], b[0], b[1], pen)
        item.setOpacity(opacity)
        item.setZValue(z)
        return item

    def _ellipse(self, box, color, width, fill=None, *, z=0, opacity=1.0):
        pen = QPen(QColor(color), width)
        brush = QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush)
        item = self.graphics.addEllipse(QRectF(*box), pen, brush)
        item.setZValue(z)
        item.setOpacity(opacity)
        return item

    def _rect(self, box, color, width, fill=None, *, z=0, opacity=1.0):
        pen = QPen(QColor(color), width)
        brush = QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush)
        item = self.graphics.addRect(QRectF(*box), pen, brush)
        item.setZValue(z)
        item.setOpacity(opacity)
        return item

    def _render_entity(self, e, frame: int, *, z=0, opacity=1.0, ghost=False):
        kind = e["kind"]
        color = "#4a9fd4" if ghost else e["color"]
        width = e["stroke_width"]
        if kind == "line":
            self._line(e["a"], e["b"], color, width, z=z, opacity=opacity)
        elif kind in ("rectangle", "circle"):
            a, b = e["a"], e["b"]
            box = (min(a[0], b[0]), min(a[1], b[1]),
                   abs(a[0] - b[0]), abs(a[1] - b[1]))
            if kind == "rectangle":
                self._rect(box, color, width, z=z, opacity=opacity)
            else:
                self._ellipse(box, color, width, z=z, opacity=opacity)
        elif kind == "stickman":
            points, _ = joints_at(self.editor.project, e, frame)
            for joint, (parent, _) in RIG_OFFSETS.items():
                if parent:
                    self._line(points[parent], points[joint], color, width, z=z, opacity=opacity)
            x, y = points["head"]
            self._ellipse((x - 15, y - 15, 30, 30), color, width,
                          color if e.get("head_filled") else "white",
                          z=z, opacity=opacity)
            if self.editor.selected_id == e["id"] and not ghost and not self.editor.playing:
                for joint, xy in points.items():
                    cx, cy = xy
                    self._ellipse((cx - 5, cy - 5, 10, 10),
                                  "#d97706" if joint == self.editor.selected_joint else "#2176b7",
                                  1.5, "#ffffff", z=50)

    def redraw(self) -> None:
        self.graphics.clear()
        self._hover_item = None
        project = self.editor.project
        cam = project.scene["camera"]
        width, height = project.width / cam["zoom"], project.height / cam["zoom"]
        x, y = cam["center"]
        left, top = x - width / 2, y - height / 2
        self._rect((left, top, width, height), "#3d485b", 2.0, "#ffffff", z=-200)
        if self.editor.grid_check.isChecked():
            grid = 50
            start_x = math.floor(left / grid) * grid
            start_y = math.floor(top / grid) * grid
            for gx in range(start_x, math.ceil((left + width) / grid) * grid + 1, grid):
                self._line((gx, top), (gx, top + height), "#e8ecf2", 0.8, z=-190)
            for gy in range(start_y, math.ceil((top + height) / grid) * grid + 1, grid):
                self._line((left, gy), (left + width, gy), "#e8ecf2", 0.8, z=-190)
        if self.editor.onion_check.isChecked() and self.editor.frame > 0 and not self.editor.playing:
            for e in visible_entities(project):
                if e["kind"] == "stickman":
                    self._render_entity(e, self.editor.frame - 1, z=-10, opacity=0.32, ghost=True)
        for index, e in enumerate(visible_entities(project)):
            self._render_entity(e, self.editor.frame, z=index)
        if self.shape_start and self.shape_end:
            kind = self.editor.tool
            if kind in ("line", "rectangle", "circle"):
                demo = make_shape(kind, list(self.shape_start), list(self.shape_end))
                self._render_entity(demo, self.editor.frame, z=60, opacity=0.5)
        self._update_hover()

    def _update_hover(self):
        """Lightweight pointer preview: redraw only the transient path item."""
        if self._hover_item is not None:
            self.graphics.removeItem(self._hover_item)
            self._hover_item = None
        if (self._hover_point is None or self.editor.playing or
                self.editor.tool == "select" or self.shape_start is not None):
            return
        x, y = self._hover_point
        kind = self.editor.tool
        path = QPainterPath()
        if kind == "stickman":
            preview = make_stickman(x, y)
            points, _ = joints_at(self.editor.project, preview, self.editor.frame)
            for joint, (parent, _) in RIG_OFFSETS.items():
                if parent:
                    px, py = points[parent]
                    qx, qy = points[joint]
                    path.moveTo(px, py)
                    path.lineTo(qx, qy)
            hx, hy = points["head"]
            path.addEllipse(hx - 15, hy - 15, 30, 30)
        elif kind == "line":
            path.moveTo(x, y)
            path.lineTo(x + 80, y)
        elif kind == "rectangle":
            path.addRect(x, y, 80, 55)
        elif kind == "circle":
            path.addEllipse(x, y, 80, 55)
        else:
            return
        pen = QPen(QColor("#2381bf"), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        item = self.graphics.addPath(path, pen)
        item.setOpacity(0.45)
        item.setZValue(1000)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._hover_item = item

    def clear_hover(self):
        self._hover_point = None
        self._update_hover()

    def leaveEvent(self, event):
        self.clear_hover()
        super().leaveEvent(event)

    def _pick(self, point: tuple[float, float]):
        threshold = 13 / max(self.transform().m11(), 0.05)
        for e in reversed(list(visible_entities(self.editor.project))):
            if get_layer(self.editor.project, e["layer_id"])["locked"]:
                continue
            if e["kind"] == "stickman":
                points, _ = joints_at(self.editor.project, e, self.editor.frame)
                for joint, xy in reversed(list(points.items())):
                    if math.dist(point, xy) < threshold:
                        return e["id"], joint
                for joint, (parent, _) in RIG_OFFSETS.items():
                    if parent and _distance_segment(point, points[parent], points[joint]) < threshold:
                        return e["id"], None
            elif e["kind"] == "line":
                if _distance_segment(point, e["a"], e["b"]) < threshold:
                    return e["id"], None
            else:
                a, b = e["a"], e["b"]
                x1, x2 = sorted((a[0], b[0]))
                y1, y2 = sorted((a[1], b[1]))
                if x1 - threshold <= point[0] <= x2 + threshold and y1 - threshold <= point[1] <= y2 + threshold:
                    return e["id"], None
        return None, None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.panning = True
            self.pan_start = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.editor.playing:
            return super().mousePressEvent(event)
        point = self.snap(self.to_world(event))
        tool = self.editor.tool
        if tool == "stickman":
            self.clear_hover()
            self.editor.add_stickman(point)
            return
        if tool in ("line", "rectangle", "circle"):
            self.clear_hover()
            self.shape_start = point
            self.shape_end = point
            self.redraw()
            return
        eid, joint = self._pick(point)
        self.editor.select_entity(eid, joint)
        if eid:
            self.drag_id = eid
            self.drag_joint = joint
            self.drag_start = point
            self.drag_before = self.editor.history.capture(self.editor.project)
            e = get_entity(self.editor.project, eid)
            if e["kind"] == "stickman":
                current = value_at(self.editor.project, e, "position", self.editor.frame)
                self.drag_origin = tuple(current)
            else:
                self.drag_origin = (0.0, 0.0)
        self.redraw()
        event.accept()

    def mouseMoveEvent(self, event):
        point = self.snap(self.to_world(event))
        if self.panning and self.pan_start is not None:
            current = event.position().toPoint()
            delta = current - self.pan_start
            self.pan_by_pixels(delta.x(), delta.y())
            self.pan_start = current
            return
        if self.shape_start is not None:
            self.shape_end = point
            self.redraw()
            return
        if self.drag_id is not None and self.drag_start is not None:
            e = get_entity(self.editor.project, self.drag_id)
            if e["kind"] == "stickman" and self.drag_joint and self.drag_joint != "pelvis":
                angle = angle_for_drag(self.editor.project, e, self.editor.frame, self.drag_joint, point)
                set_key(self.editor.project, self.drag_id,
                        f"pose.{self.drag_joint}", self.editor.frame, angle)
            elif e["kind"] == "stickman":
                assert self.drag_origin is not None
                dx, dy = point[0] - self.drag_start[0], point[1] - self.drag_start[1]
                set_key(self.editor.project, self.drag_id, "position", self.editor.frame,
                        [self.drag_origin[0] + dx, self.drag_origin[1] + dy])
            else:
                before = next((entry for entry in self.drag_before["scenes"][0]["entities"]
                               if entry["id"] == self.drag_id), None)
                assert before is not None
                dx, dy = point[0] - self.drag_start[0], point[1] - self.drag_start[1]
                e["a"] = [before["a"][0] + dx, before["a"][1] + dy]
                e["b"] = [before["b"][0] + dx, before["b"][1] + dy]
            self.redraw()
            return
        self._hover_point = point
        self._update_hover()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.panning = False
            self.pan_start = None
            self.viewport().unsetCursor()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        if self.shape_start:
            point = self.snap(self.to_world(event))
            start = self.shape_start
            self.shape_start = self.shape_end = None
            if math.dist(start, point) < 8:
                point = (start[0] + 80, start[1] + (0 if self.editor.tool == "line" else 55))
            self.editor.add_shape(self.editor.tool, start, point)
            self.redraw()
            return
        if self.drag_id:
            self.editor.history.commit(self.drag_before, self.editor.project)
            self.editor.after_change()
        self.drag_id = self.drag_joint = self.drag_start = self.drag_origin = self.drag_before = None
        self.redraw()
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.project = Project()
        self.history = History()
        self.frame = 0
        self.path: Path | None = None
        self.saved_snapshot = self.project.to_dict()
        self.selected_id: str | None = None
        self.selected_joint: str | None = None
        self.tool = "select"
        self.playing = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)
        self.setWindowTitle("StickRig Studio")
        self.resize(1380, 850)
        self._docks = []
        self._restoring_layout = False
        self._thumb_epoch = 0
        self._thumb_pending = False
        self._thumb_cache = OrderedDict()
        self._building = True
        self.build_ui()
        self._building = False
        if path:
            self.load_file(Path(path))
        self.refresh()
        if not path:
            self.restore_workspace()
        QTimer.singleShot(0, self.canvas.frame_camera)

    def build_ui(self):
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Choose Stickman, then click the canvas. Use Select to drag joints.")
        self._build_menus()
        toolbar = QToolBar("Tools", self)
        toolbar.setObjectName("drawing_toolbar")
        toolbar.setMovable(True)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.tool_actions = {}
        for key, label in [("select", "Select / Move"), ("stickman", "+ Stickman"),
                           ("line", "Line"), ("rectangle", "Rectangle"), ("circle", "Ellipse")]:
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=key: self.select_tool(key))
            toolbar.addAction(action)
            self.tool_actions[key] = action
        self.tool_actions["select"].setChecked(True)
        toolbar.addSeparator()
        self.grid_check = QCheckBox("Grid")
        self.grid_check.stateChanged.connect(lambda _: self.canvas.redraw())
        toolbar.addWidget(self.grid_check)
        self.snap_check = QCheckBox("Snap (10)")
        toolbar.addWidget(self.snap_check)
        self.onion_check = QCheckBox("Onion skin")
        self.onion_check.setChecked(True)
        self.onion_check.stateChanged.connect(lambda _: self.canvas.redraw())
        toolbar.addWidget(self.onion_check)
        toolbar.addSeparator()
        camera_button = QPushButton("Frame camera")
        camera_button.clicked.connect(lambda: self.canvas.frame_camera())
        toolbar.addWidget(camera_button)

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        inspector = QWidget()
        i_layout = QVBoxLayout(inspector)
        self.inspector_dock = self.make_dock("Inspector", "inspector_dock",
                                              inspector, Qt.DockWidgetArea.RightDockWidgetArea)
        selected_box = QGroupBox("Selection")
        select_layout = QVBoxLayout(selected_box)
        self.info_label = QLabel("Nothing selected")
        self.info_label.setWordWrap(True)
        select_layout.addWidget(self.info_label)
        color_button = QPushButton("Change color…")
        color_button.clicked.connect(self.change_color)
        select_layout.addWidget(color_button)
        self.fill_head = QCheckBox("Fill head")
        self.fill_head.toggled.connect(self.change_head_fill)
        select_layout.addWidget(self.fill_head)
        delete_button = QPushButton("Delete selected")
        delete_button.clicked.connect(self.delete_selected)
        select_layout.addWidget(delete_button)
        i_layout.addWidget(selected_box)

        layers_box = QGroupBox("Layers (visible / locked)")
        layer_layout = QVBoxLayout(layers_box)
        self.layers_list = QListWidget()
        self.layers_list.itemChanged.connect(self.change_layer_visibility)
        layer_layout.addWidget(self.layers_list)
        lock_btn = QPushButton("Toggle selected layer lock")
        lock_btn.clicked.connect(self.toggle_layer_lock)
        layer_layout.addWidget(lock_btn)
        self.layers_dock = self.make_dock("Layers", "layers_dock", layers_box,
                                          Qt.DockWidgetArea.RightDockWidgetArea)

        cam_box = QGroupBox("Camera (static in v0.1)")
        cam_layout = QVBoxLayout(cam_box)
        self.cam_x = QDoubleSpinBox()
        self.cam_y = QDoubleSpinBox()
        self.cam_zoom = QDoubleSpinBox()
        for field in (self.cam_x, self.cam_y):
            field.setRange(-100000, 100000)
            field.setDecimals(1)
        self.cam_zoom.setRange(0.05, 30.0)
        self.cam_zoom.setSingleStep(0.1)
        self.cam_zoom.setDecimals(2)
        for label, field in (("Center X", self.cam_x), ("Center Y", self.cam_y), ("Zoom", self.cam_zoom)):
            cam_layout.addWidget(QLabel(label))
            cam_layout.addWidget(field)
            field.editingFinished.connect(self.update_camera)
        self.camera_dock = self.make_dock("Camera", "camera_dock", cam_box,
                                          Qt.DockWidgetArea.RightDockWidgetArea)
        i_layout.addStretch(1)

        frames = QWidget()
        frame_layout = QVBoxLayout(frames)
        frame_layout.setContentsMargins(5, 5, 5, 5)
        self.frames_list = FrameList(self)
        self.frames_list.setIconSize(QSize(128, 72))
        self.frames_list.setSpacing(6)
        self.frames_list.setMinimumWidth(210)
        self.frames_list.currentRowChanged.connect(
            lambda row: self.set_frame(row) if row >= 0 else None
        )
        self.frames_list.verticalScrollBar().valueChanged.connect(
            lambda _: self.schedule_thumbnails()
        )
        frame_layout.addWidget(self.frames_list, 1)
        frame_ops = QHBoxLayout()
        for label, handler in (("+ Copy next", self.add_frame), ("Delete", self.remove_frame)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            frame_ops.addWidget(button)
        frame_layout.addLayout(frame_ops)
        self.frames_dock = self.make_dock(
            "Frames", "frames_dock", frames, Qt.DockWidgetArea.LeftDockWidgetArea
        )

        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.addWidget(QLabel("Select a model, then click or drag on the canvas:"))
        self.library_list = QListWidget()
        self.library_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.library_list.setFlow(QListWidget.Flow.LeftToRight)
        self.library_list.setWrapping(True)
        self.library_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.library_list.setMovement(QListWidget.Movement.Static)
        self.library_list.setIconSize(QSize(72, 52))
        self.library_list.setGridSize(QSize(110, 90))
        self.library_list.setMinimumHeight(115)
        self.library_list.itemClicked.connect(
            lambda item: self.select_tool(item.data(Qt.ItemDataRole.UserRole))
        )
        for key, label in (("stickman", "Stickman"), ("line", "Line"),
                           ("rectangle", "Rectangle"), ("circle", "Ellipse")):
            item = QListWidgetItem(self.library_icon(key), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.library_list.addItem(item)
        library_layout.addWidget(self.library_list)
        self.library_dock = self.make_dock(
            "Elements", "library_dock", library, Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # A thin, independent preview strip, not part of the Frames sidebar.
        preview = QToolBar("Preview", self)
        preview.setObjectName("preview_toolbar")
        preview.setMovable(True)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, preview)
        self.preview_toolbar = preview
        for label, handler in (("|◀", self.first_frame), ("◀", self.prev_frame),
                               ("▶ Play", self.toggle_play), ("▶|", self.next_frame)):
            button = QPushButton(label)
            button.setMinimumWidth(38)
            button.clicked.connect(handler)
            preview.addWidget(button)
            if label == "▶ Play":
                self.play_btn = button
        preview.addSeparator()
        preview.addWidget(QLabel("  Frame  "))
        self.frame_spin = QSpinBox()
        self.frame_spin.setMinimum(1)
        self.frame_spin.setMaximum(self.project.duration)
        self.frame_spin.valueChanged.connect(lambda value: self.set_frame(value - 1))
        preview.addWidget(self.frame_spin)
        self.frame_total = QLabel("/ 1")
        preview.addWidget(self.frame_total)
        preview.addSeparator()
        preview.addWidget(QLabel("  FPS  "))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.valueChanged.connect(self.change_fps)
        preview.addWidget(self.fps_spin)

        self.tabifyDockWidget(self.inspector_dock, self.layers_dock)
        self.inspector_dock.raise_()
        self.view_menu.addSeparator()
        for dock in self._docks:
            self.view_menu.addAction(dock.toggleViewAction())
        self._default_dock_state = self.saveState()
        self._layout_monitor = QTimer(self)
        self._layout_monitor.setSingleShot(True)
        self._layout_monitor.setInterval(200)
        self._layout_monitor.timeout.connect(self.capture_workspace)
        for dock in self._docks:
            dock.installEventFilter(self)
            dock.dockLocationChanged.connect(lambda _: self.schedule_layout_capture())
            dock.topLevelChanged.connect(lambda _: self.schedule_layout_capture())
            dock.visibilityChanged.connect(lambda _: self.schedule_layout_capture())

    def make_dock(self, title, name, content, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable |
                         QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setWidget(content)
        self.addDockWidget(area, dock)
        self._docks.append(dock)
        return dock

    @staticmethod
    def library_icon(kind):
        pix = QPixmap(72, 52)
        pix.fill(QColor("#ffffff"))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#20252d"), 3))
        if kind == "stickman":
            painter.drawEllipse(30, 5, 12, 12)
            for a, b in [((36, 17), (36, 33)), ((36, 23), (19, 29)),
                         ((36, 23), (53, 29)), ((36, 33), (24, 46)),
                         ((36, 33), (48, 46))]:
                painter.drawLine(a[0], a[1], b[0], b[1])
        elif kind == "line":
            painter.drawLine(10, 44, 62, 8)
        elif kind == "rectangle":
            painter.drawRect(11, 9, 50, 34)
        elif kind == "circle":
            painter.drawEllipse(21, 8, 30, 36)
        painter.end()
        return QIcon(pix)

    def eventFilter(self, watched, event):
        if (watched in self._docks and
                event.type() in (QEvent.Type.Move, QEvent.Type.Resize,
                                 QEvent.Type.Show, QEvent.Type.Hide)):
            self.schedule_layout_capture()
        return super().eventFilter(watched, event)

    def schedule_layout_capture(self):
        if (self._building or self._restoring_layout or
                not hasattr(self, "_layout_monitor")):
            return
        self._layout_monitor.start()

    def capture_workspace(self, force=False):
        if self._restoring_layout and not force:
            return
        state = bytes(self.saveState().toBase64()).decode("ascii")
        if self.project.workspace.get("dock_state") != state:
            self.project.workspace["dock_state"] = state
            self.refresh_title()

    def restore_workspace(self):
        self._restoring_layout = True
        self._layout_monitor.stop()
        value = self.project.workspace.get("dock_state")
        if value:
            state = QByteArray.fromBase64(QByteArray(value.encode("ascii")))
            if not self.restoreState(state):
                self.restoreState(self._default_dock_state)
        else:
            self.restoreState(self._default_dock_state)
        QTimer.singleShot(0, self.finish_workspace_restore)

    def finish_workspace_restore(self):
        self._restoring_layout = False

    def reset_workspace(self):
        self._restoring_layout = True
        self._layout_monitor.stop()
        self.restoreState(self._default_dock_state)
        self.project.workspace.pop("dock_state", None)
        QTimer.singleShot(0, self.finish_workspace_restore)
        self.refresh_title()

    def refresh_title(self):
        name = self.path.name if self.path else self.project.name
        self.setWindowTitle(f"{'* ' if self.is_dirty else ''}{name} — StickRig Studio")

    def visible_frame_rows(self):
        count = self.frames_list.count()
        if not count:
            return ()
        viewport = self.frames_list.viewport()
        first = self.frames_list.indexAt(QPoint(5, 5))
        last = self.frames_list.indexAt(QPoint(5, max(5, viewport.height() - 5)))
        start = first.row() if first.isValid() else 0
        stop = last.row() if last.isValid() else min(count - 1, start + 8)
        return range(max(0, start - 1), min(count, max(start, stop) + 2))

    def schedule_thumbnails(self):
        if self.playing or self._thumb_pending or not self.frames_list.count():
            return
        self._thumb_pending = True
        QTimer.singleShot(0, self.render_one_thumbnail)

    def render_one_thumbnail(self):
        self._thumb_pending = False
        if self.playing:
            return
        visible = set(self.visible_frame_rows())
        for old in list(self._thumb_cache):
            if old not in visible:
                self.frames_list.item(old).setIcon(QIcon())
                self.frames_list.item(old).setData(Qt.ItemDataRole.UserRole + 1, -1)
                del self._thumb_cache[old]
        for row in visible:
            item = self.frames_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole + 1) == self._thumb_epoch:
                continue
            width = 128
            height = max(1, round(width * self.project.height / self.project.width))
            image = render_image(self.project, row, antialias=1,
                                 target_size=(width, height))
            qimage = QImage(image.tobytes(), width, height, width * 3,
                            QImage.Format.Format_RGB888).copy()
            icon = QIcon(QPixmap.fromImage(qimage))
            item.setIcon(icon)
            item.setData(Qt.ItemDataRole.UserRole + 1, self._thumb_epoch)
            self._thumb_cache[row] = icon
            self.schedule_thumbnails()
            break

    def refresh_frames(self, *, content_changed):
        if self.frames_list.count() != self.project.duration:
            self.frames_list.blockSignals(True)
            self.frames_list.clear()
            self._thumb_cache.clear()
            for row in range(self.project.duration):
                item = QListWidgetItem(f"Frame {row + 1}")
                item.setSizeHint(QSize(245, 112))
                self.frames_list.addItem(item)
            self.frames_list.blockSignals(False)
            self._previous_hidden_frames = set()
            content_changed = True
        if content_changed:
            self._thumb_epoch += 1
            for row in list(self._thumb_cache):
                item = self.frames_list.item(row)
                if item:
                    item.setIcon(QIcon())
                    item.setData(Qt.ItemDataRole.UserRole + 1, -1)
            self._thumb_cache.clear()
        if content_changed:
            hidden = set(self.project.scene.get("hidden_frames", []))
            previous = getattr(self, "_previous_hidden_frames", set())
            for row in hidden ^ previous:
                item = self.frames_list.item(row)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole + 3, row in hidden)
            self._previous_hidden_frames = hidden
        if self.frames_list.currentRow() != self.frame:
            self.frames_list.blockSignals(True)
            self.frames_list.setCurrentRow(self.frame)
            self.frames_list.blockSignals(False)
        if not self.playing:
            self.schedule_thumbnails()

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        for title, callback, shortcut in [
            ("New project", self.new_project, "Ctrl+N"),
            ("Open…", self.open_file, "Ctrl+O"),
            ("Save", self.save_file, "Ctrl+S"),
            ("Save as…", self.save_as_file, "Ctrl+Shift+S"),
            ("Export current frame as PNG…", self.export_png_dialog, None),
            ("Export animation as MP4…", self.export_video_dialog, None),
        ]:
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
        edit_menu = self.menuBar().addMenu("Edit")
        for title, callback, shortcut in [("Undo", self.undo, "Ctrl+Z"),
                                          ("Redo", self.redo, "Ctrl+Y"),
                                          ("Delete selected object", self.delete_selected, "Delete")]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)
        timeline_menu = self.menuBar().addMenu("Timeline")
        for title, callback, shortcut in [
            ("Previous frame", self.prev_frame, "Left"),
            ("Next frame", self.next_frame, "Right"),
            ("Duplicate pose into next frame", self.add_frame, "Ctrl+D"),
            ("Play / Pause", self.toggle_play, "Space"),
        ]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            timeline_menu.addAction(action)
        self.view_menu = self.menuBar().addMenu("View")
        view_menu = self.view_menu
        action = QAction("Fit camera", self)
        action.setShortcut(QKeySequence("Ctrl+0"))
        action.triggered.connect(lambda: self.canvas.frame_camera())
        view_menu.addAction(action)
        reset = QAction("Reset workspace", self)
        reset.triggered.connect(self.reset_workspace)
        view_menu.addAction(reset)
        help_menu = self.menuBar().addMenu("Help")
        about = QAction("About / keyboard shortcuts", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    @property
    def is_dirty(self):
        current = self.project.to_dict()
        # Revision is a concurrency marker, not a user-visible change to the scene.
        old = copy.deepcopy(self.saved_snapshot)
        current.pop("revision", None)
        old.pop("revision", None)
        return current != old

    def select_tool(self, key):
        self.tool = key
        self.canvas._update_hover()
        for name, action in self.tool_actions.items():
            action.setChecked(key == name)
        self.statusBar().showMessage({"select": "Drag a joint to pose it; drag a bone to move the whole character.",
                                      "stickman": "Click anywhere to add a stickman.",
                                      "line": "Drag to draw a line.", "rectangle": "Drag to draw a rectangle.",
                                      "circle": "Drag to draw an ellipse."}[key])

    def select_entity(self, entity_id, joint=None):
        self.selected_id = entity_id
        self.selected_joint = joint
        self.refresh_inspector()

    def can_edit_layer(self, layer_id):
        if get_layer(self.project, layer_id)["locked"]:
            self.statusBar().showMessage(f"Layer '{layer_id}' is locked", 4000)
            return False
        return True

    def add_stickman(self, point):
        if not self.can_edit_layer("actors"):
            return
        obj = make_stickman(*point)
        self.mutate(lambda: self.project.scene["entities"].append(obj))
        self.select_entity(obj["id"])

    def add_shape(self, kind, a, b):
        if not self.can_edit_layer("background"):
            return
        obj = make_shape(kind, list(a), list(b))
        self.mutate(lambda: self.project.scene["entities"].append(obj))
        self.select_entity(obj["id"])

    def mutate(self, fn):
        self.history.apply(self.project, fn)
        self.after_change()

    def after_change(self):
        self.project.validate()
        self.refresh(content_changed=True)

    def refresh(self, *, content_changed=True):
        if self._building:
            return
        self.frame = max(0, min(self.frame, self.project.duration - 1))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setMaximum(self.project.duration)
        self.frame_spin.setValue(self.frame + 1)
        self.frame_spin.blockSignals(False)
        self.frame_total.setText(f"/ {self.project.duration}")
        self.fps_spin.blockSignals(True)
        self.fps_spin.setValue(self.project.fps)
        self.fps_spin.blockSignals(False)
        cam = self.project.scene["camera"]
        for spinner, value in ((self.cam_x, cam["center"][0]), (self.cam_y, cam["center"][1]),
                               (self.cam_zoom, cam["zoom"])):
            spinner.blockSignals(True)
            spinner.setValue(value)
            spinner.blockSignals(False)
        if content_changed:
            self.refresh_inspector()
            self.refresh_layers()
        self.refresh_frames(content_changed=content_changed)
        self.canvas.redraw()
        self.refresh_title()

    def refresh_inspector(self):
        try:
            e = get_entity(self.project, self.selected_id) if self.selected_id else None
        except ValueError:
            e = None
            self.selected_id = self.selected_joint = None
        if not e:
            self.info_label.setText("Nothing selected")
        else:
            self.info_label.setText(f"{e['kind']} · {e['id']}\nLayer: {e['layer_id']}\nColor: {e['color']}" +
                                    (f"\nJoint: {self.selected_joint}" if self.selected_joint else ""))
        self.fill_head.blockSignals(True)
        self.fill_head.setVisible(bool(e and e["kind"] == "stickman"))
        self.fill_head.setChecked(bool(e and e.get("head_filled", False)))
        self.fill_head.blockSignals(False)

    def refresh_layers(self):
        selected_item = self.layers_list.currentItem()
        selected_id = selected_item.data(Qt.ItemDataRole.UserRole) if selected_item else None
        self.layers_list.blockSignals(True)
        self.layers_list.clear()
        for layer in self.project.scene["layers"]:
            label = layer["name"] + ("  🔒" if layer["locked"] else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, layer["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if layer["visible"] else Qt.CheckState.Unchecked)
            self.layers_list.addItem(item)
            if selected_id == layer["id"]:
                self.layers_list.setCurrentItem(item)
        self.layers_list.blockSignals(False)

    def change_layer_visibility(self, item):
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        checked = item.checkState() == Qt.CheckState.Checked
        self.mutate(lambda: get_layer(self.project, layer_id).__setitem__("visible", checked))

    def toggle_layer_lock(self):
        item = self.layers_list.currentItem()
        if not item:
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        def op():
            layer = get_layer(self.project, lid)
            layer["locked"] = not layer["locked"]
        self.mutate(op)

    def change_color(self):
        if not self.selected_id:
            return
        e = get_entity(self.project, self.selected_id)
        if not self.can_edit_layer(e["layer_id"]):
            return
        color = QColorDialog.getColor(QColor(e["color"]), self, "Object color")
        if color.isValid():
            self.mutate(lambda: e.__setitem__("color", color.name()))

    def change_head_fill(self, state):
        if not self.selected_id:
            return
        e = get_entity(self.project, self.selected_id)
        if e["kind"] == "stickman" and self.can_edit_layer(e["layer_id"]):
            self.mutate(lambda: e.__setitem__("head_filled", bool(state)))

    def delete_selected(self):
        if not self.selected_id:
            return
        e = get_entity(self.project, self.selected_id)
        if not self.can_edit_layer(e["layer_id"]):
            return
        ident = self.selected_id
        def op():
            self.project.scene["entities"] = [e for e in self.project.scene["entities"] if e["id"] != ident]
            self.project.scene["tracks"] = [t for t in self.project.scene["tracks"] if t["target_id"] != ident]
        self.selected_id = self.selected_joint = None
        self.mutate(op)

    def change_fps(self, value):
        if not self._building and value != self.project.fps:
            self.mutate(lambda: setattr(self.project, "fps", value))
            if self.playing:
                self.timer.setInterval(round(1000 / self.project.fps))

    def update_camera(self):
        cam = self.project.scene["camera"]
        center = [self.cam_x.value(), self.cam_y.value()]
        zoom = self.cam_zoom.value()
        if cam["center"] != center or cam["zoom"] != zoom:
            self.mutate(lambda: cam.update({"center": center, "zoom": zoom}))

    def first_frame(self):
        self.set_frame(0)

    def prev_frame(self):
        self.set_frame(max(0, self.frame - 1))

    def next_frame(self):
        self.set_frame(min(self.project.duration - 1, self.frame + 1))

    def set_frame(self, frame):
        self.frame = max(0, min(self.project.duration - 1, frame))
        self.refresh(content_changed=False)

    def add_frame(self):
        if self.playing:
            self.toggle_play()
        self.mutate(lambda: setattr(self, "frame", insert_frame(self.project, self.frame)))

    def remove_frame(self):
        if self.playing:
            self.toggle_play()
        try:
            self.mutate(lambda: setattr(self, "frame", delete_frame(self.project, self.frame)))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 3000)

    def duplicate_frame_at(self, frame: int):
        if not 0 <= frame < self.project.duration:
            return
        self.stop_playback()
        self.frame = frame
        self.add_frame()

    def delete_frame_at(self, frame: int):
        if not 0 <= frame < self.project.duration:
            return
        self.stop_playback()
        self.frame = frame
        self.remove_frame()

    def append_frame(self):
        self.duplicate_frame_at(self.project.duration - 1)

    def toggle_frame_visibility(self, frame: int):
        if not 0 <= frame < self.project.duration:
            return
        self.stop_playback()
        self.mutate(lambda: toggle_frame_hidden(self.project, frame))
        self.statusBar().showMessage(
            f"Frame {frame + 1}: " +
            ("hidden from preview and export" if frame in self.project.scene.get("hidden_frames", [])
             else "visible in preview and export"),
            4000
        )

    def unhide_all_frames(self):
        self.stop_playback()
        if self.project.scene.get("hidden_frames"):
            self.mutate(lambda: self.project.scene.__setitem__("hidden_frames", []))

    def move_frame_to(self, source: int, destination: int):
        if (not 0 <= source < self.project.duration or
                not 0 <= destination < self.project.duration or source == destination):
            return
        self.stop_playback()
        def op():
            move_frame(self.project, source, destination)
            self.frame = destination
        self.mutate(op)

    def toggle_play(self):
        if not self.playing:
            frames = playable_frames(self.project)
            if not frames:
                self.statusBar().showMessage("All frames are hidden. Show a frame first.", 4000)
                return
            if self.frame not in frames:
                self.set_frame(frames[0])
        self.playing = not self.playing
        if self.playing:
            self.canvas.clear_hover()
            self.timer.start(max(1, round(1000 / self.project.fps)))
            self.play_btn.setText("⏸ Pause")
        else:
            self.timer.stop()
            self.play_btn.setText("▶ Play")
            self.schedule_thumbnails()
        self.canvas.redraw()

    def advance_frame(self):
        frames = playable_frames(self.project)
        if not frames:
            self.toggle_play()
            return
        self.set_frame(next((f for f in frames if f > self.frame), frames[0]))

    def undo(self):
        workspace = copy.deepcopy(self.project.workspace)
        self.project = self.history.undo(self.project)
        self.project.workspace = workspace
        self.after_change()

    def redo(self):
        workspace = copy.deepcopy(self.project.workspace)
        self.project = self.history.redo(self.project)
        self.project.workspace = workspace
        self.after_change()

    def check_unsaved(self):
        if not self.is_dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Save the current project?",
                                      QMessageBox.StandardButton.Save |
                                      QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_file()
        return True

    def new_project(self):
        self.capture_workspace()
        if not self.check_unsaved():
            return
        self.stop_playback()
        self.project = Project()
        self.frame = 0
        self.path = None
        self.history = History()
        self.selected_id = self.selected_joint = None
        self.saved_snapshot = self.project.to_dict()
        self.refresh()
        self.restore_workspace()
        self.canvas.frame_camera()

    def open_file(self):
        self.capture_workspace()
        if not self.check_unsaved():
            return
        name, _ = QFileDialog.getOpenFileName(self, "Open StickRig project", "",
                                              "StickRig project (*.json);;All files (*)")
        if name:
            self.load_file(Path(name))

    def load_file(self, path: Path):
        try:
            new_project = load_project(path)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Cannot open project", str(exc))
            return
        self.stop_playback()
        self.project = new_project
        self.path = path
        self.frame = 0
        self.history = History()
        self.selected_id = self.selected_joint = None
        self.saved_snapshot = self.project.to_dict()
        self.refresh()
        self.restore_workspace()
        self.canvas.frame_camera()

    def save_file(self):
        self.capture_workspace(force=True)
        if not self.path:
            return self.save_as_file()
        try:
            if disk_project_changed(self.path, self.saved_snapshot):
                QMessageBox.warning(
                    self, "Project changed outside StickRig",
                    "The project file was modified on disk after it was opened.\n\n"
                    "To preserve those changes, reopen it or choose Save As with a new filename."
                )
                return False
            save_project(self.project, self.path)
            self.saved_snapshot = self.project.to_dict()
            self.refresh()
            self.statusBar().showMessage(f"Saved {self.path}", 4000)
            return True
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False

    def save_as_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save StickRig project", "animation.stickrig.json",
                                              "StickRig project (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".stickrig.json"
        self.path = Path(path)
        return self.save_file()

    def export_png_dialog(self):
        from .render import render_png
        path, _ = QFileDialog.getSaveFileName(self, "Render frame", "frame.png", "PNG (*.png)")
        if path:
            try:
                render_png(self.project, path, self.frame)
                self.statusBar().showMessage(f"Rendered {path}", 5000)
            except (ValueError, OSError) as exc:
                QMessageBox.critical(self, "Render failed", str(exc))

    def export_video_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export MP4", "animation.mp4", "MP4 (*.mp4)")
        if not path:
            return
        self.stop_playback()
        progress = QProgressDialog("Rendering frames…", "Cancel", 0, len(playable_frames(self.project)), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        try:
            def update(frame, total):
                progress.setValue(frame)
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise RuntimeError("Export cancelled")
            export_mp4(self.project, path, progress=update)
            progress.setValue(len(playable_frames(self.project)))
            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
        finally:
            progress.close()

    def stop_playback(self):
        if self.playing:
            self.toggle_play()

    def about(self):
        QMessageBox.information(self, "StickRig Studio 0.1",
                                "StickRig Studio 0.1 — prototype\n\n"
                                "Select/Move: drag a joint to pose, drag a bone to move.\n"
                                "Tools: click to add a stickman; drag to draw shapes.\n"
                                "Frames: + Copy next / Delete frame, Onion skin.\n"
                                "Camera: static X/Y/zoom, Frame camera (Ctrl+0).\n"
                                "Undo/Redo: Ctrl+Z / Ctrl+Y. Save: Ctrl+S.\n"
                                "Pan: touchpad swipe / wheel / middle mouse; Ctrl+wheel zooms.\n"
                                "Viewport zoom: lower-right overlay. Hover: placement preview.\n"
                                "Frames: right-click menus, action buttons and drag grip.\n"
                                "Hidden frames are skipped in preview/MP4, kept editable.\n"
                                "Docks: drag, resize, close, reopen from View menu.\n\n"
                                "Project is editable JSON; experimental AI patching via stickrig-cli.")

    def closeEvent(self, event):
        self.capture_workspace(force=True)
        if self.check_unsaved():
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    window = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
