"""StickRig Studio — practical first Qt desktop editor.

Usage: python -m stickrig.app [optional project.stickrig.json]
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QBrush, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDoubleSpinBox,
    QFileDialog, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSpinBox,
    QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from .model import (History, Project, RIG_OFFSETS, angle_for_drag, delete_frame, disk_project_changed,
                    get_entity, get_layer, insert_frame, joints_at, load_project,
                    make_shape, make_stickman, save_project, set_key, value_at)
from .render import export_mp4, visible_entities


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
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
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
        self.redraw()

    def to_world(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return p.x(), p.y()

    def snap(self, point: tuple[float, float]) -> tuple[float, float]:
        if self.editor.snap_check.isChecked():
            return tuple(round(v / 10) * 10.0 for v in point)
        return point

    def frame_camera(self) -> None:
        project = self.editor.project
        cam = project.scene["camera"]
        width, height = project.width / cam["zoom"], project.height / cam["zoom"]
        x, y = cam["center"]
        self.fitInView(QRectF(x - width / 2 - 40, y - height / 2 - 40,
                              width + 80, height + 80),
                       Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        multiplier = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        factor = self.transform().m11()
        if 0.05 < factor * multiplier < 12:
            self.scale(multiplier, multiplier)
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
            self.editor.add_stickman(point)
            return
        if tool in ("line", "rectangle", "circle"):
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
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
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
        self._building = True
        self.build_ui()
        self._building = False
        if path:
            self.load_file(Path(path))
        self.refresh()
        QTimer.singleShot(0, self.canvas.frame_camera)

    def build_ui(self):
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Choose Stickman, then click the canvas. Use Select to drag joints.")
        self._build_menus()
        toolbar = QToolBar("Tools", self)
        toolbar.setMovable(False)
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

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        body = QHBoxLayout()
        main_layout.addLayout(body, 1)
        self.canvas = Canvas(self)
        body.addWidget(self.canvas, 1)
        inspector = QWidget()
        inspector.setFixedWidth(250)
        i_layout = QVBoxLayout(inspector)
        body.addWidget(inspector)
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
        i_layout.addWidget(layers_box)

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
        i_layout.addWidget(cam_box)
        i_layout.addStretch(1)

        timeline = QGroupBox("Timeline")
        time_layout = QHBoxLayout(timeline)
        main_layout.addWidget(timeline)
        for label, handler in (("|◀", self.first_frame), ("◀", self.prev_frame),
                               ("▶ Play", self.toggle_play), ("▶|", self.next_frame)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            time_layout.addWidget(button)
            if label == "▶ Play":
                self.play_btn = button
        time_layout.addWidget(QLabel("Frame"))
        self.frame_spin = QSpinBox()
        self.frame_spin.setMinimum(1)
        self.frame_spin.setMaximum(self.project.duration)
        self.frame_spin.valueChanged.connect(lambda value: self.set_frame(value - 1))
        time_layout.addWidget(self.frame_spin)
        self.frame_total = QLabel("/ 12")
        time_layout.addWidget(self.frame_total)
        for label, handler in (("+ Copy next", self.add_frame),
                               ("Delete frame", self.remove_frame)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            time_layout.addWidget(button)
        time_layout.addWidget(QLabel("FPS"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.valueChanged.connect(self.change_fps)
        time_layout.addWidget(self.fps_spin)
        time_layout.addStretch(1)

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
        view_menu = self.menuBar().addMenu("View")
        action = QAction("Fit camera", self)
        action.setShortcut(QKeySequence("Ctrl+0"))
        action.triggered.connect(lambda: self.canvas.frame_camera())
        view_menu.addAction(action)
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
        self.refresh()

    def refresh(self):
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
        self.refresh_inspector()
        self.refresh_layers()
        self.canvas.redraw()
        name = self.path.name if self.path else self.project.name
        self.setWindowTitle(f"{'* ' if self.is_dirty else ''}{name} — StickRig Studio")

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
        self.refresh()

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

    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self.timer.start(max(1, round(1000 / self.project.fps)))
            self.play_btn.setText("⏸ Pause")
        else:
            self.timer.stop()
            self.play_btn.setText("▶ Play")
        self.canvas.redraw()

    def advance_frame(self):
        self.set_frame((self.frame + 1) % self.project.duration)

    def undo(self):
        self.project = self.history.undo(self.project)
        self.after_change()

    def redo(self):
        self.project = self.history.redo(self.project)
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
        self.canvas.frame_camera()

    def open_file(self):
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
        self.canvas.frame_camera()

    def save_file(self):
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
        progress = QProgressDialog("Rendering frames…", "Cancel", 0, self.project.duration, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        try:
            def update(frame, total):
                progress.setValue(frame)
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise RuntimeError("Export cancelled")
            export_mp4(self.project, path, progress=update)
            progress.setValue(self.project.duration)
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
                                "Pan: middle mouse. Zoom: wheel.\n\n"
                                "Project is editable JSON; experimental AI patching via stickrig-cli.")

    def closeEvent(self, event):
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
