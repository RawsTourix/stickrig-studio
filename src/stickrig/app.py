"""StickRig Studio — practical first Qt desktop editor.

Usage: python -m stickrig.app [optional project.stickrig.json]
"""

from __future__ import annotations

import copy
import math
import sys
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QColor, QBrush, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QDoubleSpinBox,
    QDockWidget, QFileDialog, QFrame, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSpinBox,
    QStatusBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .model import (History, Project, RIG_OFFSETS, SIMPLE_HIDDEN_LINKS,
                    angle_for_drag, bend_for_drag, bezier_points, delete_frame,
                    disk_project_changed, get_entity, get_layer, grid_settings,
                    insert_frame, insert_blank_frame, joints_at,
                    key_new_actor_visibility, load_project, make_shape,
                    make_stickman, rig_offsets, save_project, segment_offset,
                    set_key, set_segment_bend, set_segment_scale,
                    segment_scale_for_drag, snap_to_grid,
                    stickman_strokes, value_at)
from .render import export_mp4, render_image, visible_entities
from .frames_ui import FrameStrip
from .selection import (copy_entities, entity_bounds, ids_in_rectangle,
                        materialize_paste, move_from_origins, selection_bounds,
                        transform_origins)
from .model import move_frame, set_frame_hidden, visible_frame_indices


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
        self.marquee_start = self.marquee_end = None
        self.marquee_previous: set[str] = set()
        self.marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self._group_drag_origins = None
        self._group_drag_bounds = None
        self._group_drag_start = None
        self._group_drag_started = False
        self._paste_clipboard = None
        self._paste_top_left = None
        self.shape_start: tuple[float, float] | None = None
        self.shape_end: tuple[float, float] | None = None
        self.panning = False
        self.pan_start: QPoint | None = None
        self._pan_residual_x = 0.0
        self._pan_residual_y = 0.0
        self._hover_position = None
        self._preview_items = []
        self._preview_collect = False
        self.zoom_hud = QFrame(self.viewport())
        self.zoom_hud.setObjectName("viewportZoomHud")
        self.zoom_hud.setStyleSheet(
            "#viewportZoomHud { background: rgba(252,253,255,235); "
            "border: 1px solid #a6b2c1; border-radius: 8px; }"
        )
        zoom_layout = QHBoxLayout(self.zoom_hud)
        zoom_layout.setContentsMargins(6, 2, 6, 2)
        zoom_layout.setSpacing(4)
        smaller = QToolButton(self.zoom_hud)
        smaller.setText("−")
        smaller.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        zoom_layout.addWidget(smaller)
        self.zoom_label = QLabel("100%", self.zoom_hud)
        self.zoom_label.setMinimumWidth(55)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_layout.addWidget(self.zoom_label)
        bigger = QToolButton(self.zoom_hud)
        bigger.setText("+")
        bigger.clicked.connect(lambda: self.zoom_by(1.2))
        zoom_layout.addWidget(bigger)
        fit = QToolButton(self.zoom_hud)
        fit.setText("Fit")
        fit.setToolTip("Fit camera in view")
        fit.clicked.connect(self.frame_camera)
        zoom_layout.addWidget(fit)
        self.zoom_hud.adjustSize()
        self.redraw()
        self.reposition_zoom_hud()

    def to_world(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return p.x(), p.y()

    def snap(self, point: tuple[float, float]) -> tuple[float, float]:
        return snap_to_grid(point, grid_settings(self.editor.project))

    def frame_camera(self) -> None:
        project = self.editor.project
        cam = project.scene["camera"]
        width, height = project.width / cam["zoom"], project.height / cam["zoom"]
        x, y = cam["center"]
        self.fitInView(QRectF(x - width / 2 - 40, y - height / 2 - 40,
                              width + 80, height + 80),
                       Qt.AspectRatioMode.KeepAspectRatio)
        self.update_zoom_label()
        self.refresh_grid(force=True)

    def update_zoom_label(self):
        # Viewport zoom is separate from the project's camera zoom.
        self.zoom_label.setText(f"{self.transform().m11() * 100:.0f}%")

    def zoom_by(self, multiplier: float):
        old = self.transform().m11()
        if 0.05 <= old * multiplier <= 12:
            self.scale(multiplier, multiplier)
            self.update_zoom_label()
            self.refresh_grid(force=True)

    @staticmethod
    def pan_sensitivity(view_scale: float) -> float:
        """Gentle continuous sensitivity curve: precise when zoomed in."""
        return max(0.4, min(3.2, 1 / math.sqrt(max(0.05, view_scale))))

    def pan_pixels(self, dx, dy):
        sensitivity = self.pan_sensitivity(self.transform().m11())
        self._pan_residual_x += dx * sensitivity
        self._pan_residual_y += dy * sensitivity
        whole_x = math.trunc(self._pan_residual_x)
        whole_y = math.trunc(self._pan_residual_y)
        self._pan_residual_x -= whole_x
        self._pan_residual_y -= whole_y
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - whole_x)
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - whole_y)

    def wheelEvent(self, event):
        """Swipe/wheel pans with zoom-adaptive sensitivity; Ctrl+wheel zooms."""
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
            self.pan_pixels(dx, dy)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "zoom_hud"):
            self.reposition_zoom_hud()

    def reposition_zoom_hud(self):
        if self.zoom_hud.parentWidget() is not self.viewport():
            return
        self.zoom_hud.adjustSize()
        self.zoom_hud.move(max(6, self.viewport().width() - self.zoom_hud.width() - 12),
                           max(6, self.viewport().height() - self.zoom_hud.height() - 12))
        self.zoom_hud.raise_()

    def _track_preview_item(self, item):
        if self._preview_collect:
            self._preview_items.append(item)
        return item

    def clear_placement_preview(self):
        for item in self._preview_items:
            if item.scene() is self.graphics:
                self.graphics.removeItem(item)
        self._preview_items.clear()

    def start_paste_preview(self, clipboard):
        """Cursor-anchored paste, with a visible-centre fallback off canvas."""
        self._paste_clipboard = copy.deepcopy(clipboard)
        if self._hover_position is not None:
            anchor = self._hover_position
        else:
            centre = self.mapToScene(self.viewport().rect().center())
            anchor = (centre.x(), centre.y())
        self._paste_top_left = self.snap(anchor)
        self.redraw()

    def cancel_paste(self):
        self._paste_clipboard = None
        self._paste_top_left = None
        self.redraw()

    def render_paste_preview(self):
        if self._paste_clipboard is None or self._paste_top_left is None:
            return
        dx = self._paste_top_left[0] - self._paste_clipboard["bounds"][0]
        dy = self._paste_top_left[1] - self._paste_clipboard["bounds"][1]
        for source in self._paste_clipboard["entities"]:
            ghost = copy.deepcopy(source)
            if ghost["kind"] == "stickman":
                ghost["position"] = [ghost["position"][0] + dx,
                                     ghost["position"][1] + dy]
            else:
                ghost["a"] = [ghost["a"][0] + dx, ghost["a"][1] + dy]
                ghost["b"] = [ghost["b"][0] + dx, ghost["b"][1] + dy]
            self._render_entity(ghost, self.editor.frame, z=90,
                                opacity=0.40, ghost=True)
        box = self._paste_clipboard["bounds"]
        self._rect((self._paste_top_left[0], self._paste_top_left[1],
                    box[2] - box[0], box[3] - box[1]),
                   "#2d84cc", 1.5, z=100, opacity=0.72)

    def update_placement_preview(self):
        self.clear_placement_preview()
        kind = self.editor.tool
        if (self._hover_position is None or self.editor.playing or self.panning or
                self.shape_start is not None or self.drag_id is not None or
                kind not in ("stickman", "line", "rectangle", "circle")):
            return
        x, y = self._hover_position
        grid = grid_settings(self.editor.project)
        if kind == "stickman":
            obj = make_stickman(x, y, frame_index=self.editor.frame,
                                rig_unit=grid["spacing"])
        else:
            unit = grid["spacing"]
            end = [x + unit * 9, y + (0 if kind == "line" else unit * 6)]
            obj = make_shape(kind, [x, y], end)
        self._preview_collect = True
        try:
            self._render_entity(obj, self.editor.frame, z=95, opacity=0.40, ghost=True)
        finally:
            self._preview_collect = False

    def leaveEvent(self, event):
        self.editor.update_canvas_coordinates(None)
        self._hover_position = None
        self.clear_placement_preview()
        super().leaveEvent(event)

    def _line(self, a, b, color, width, *, z=0, opacity=1.0, dash=False):
        pen = QPen(QColor(color), width)
        pen.setCosmetic(False)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if dash:
            pen.setStyle(Qt.PenStyle.DashLine)
        item = self.graphics.addLine(a[0], a[1], b[0], b[1], pen)
        item.setOpacity(opacity)
        item.setZValue(z)
        return self._track_preview_item(item)

    def _curve(self, start, end, bend, color, width, *, z=0, opacity=1.0):
        if abs(bend) < 1e-8:
            return self._line(start, end, color, width, z=z, opacity=opacity)
        dx, dy = end[0] - start[0], end[1] - start[1]
        path = QPainterPath()
        path.moveTo(*start)
        path.quadTo((start[0] + end[0]) / 2 - dy * bend,
                    (start[1] + end[1]) / 2 + dx * bend, *end)
        pen = QPen(QColor(color), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        item = self.graphics.addPath(path, pen)
        item.setZValue(z)
        item.setOpacity(opacity)
        return self._track_preview_item(item)

    def _ellipse(self, box, color, width, fill=None, *, z=0, opacity=1.0):
        pen = QPen(QColor(color), width)
        brush = QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush)
        item = self.graphics.addEllipse(QRectF(*box), pen, brush)
        item.setZValue(z)
        item.setOpacity(opacity)
        return self._track_preview_item(item)

    def _rect(self, box, color, width, fill=None, *, z=0, opacity=1.0):
        pen = QPen(QColor(color), width)
        brush = QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush)
        item = self.graphics.addRect(QRectF(*box), pen, brush)
        item.setZValue(z)
        item.setOpacity(opacity)
        return self._track_preview_item(item)

    def _render_entity(self, e, frame: int, *, z=0, opacity=1.0, ghost=False):
        kind = e["kind"]
        color = "#4a9fd4" if ghost else e["color"]
        width = e["stroke_width"]
        if kind == "line":
            self._curve(e["a"], e["b"], e.get("bend", 0.0), color, width,
                        z=z, opacity=opacity)
            if self.editor.selected_id == e["id"] and not ghost and not self.editor.playing:
                for name in ("a", "b"):
                    px, py = e[name]
                    self._ellipse((px - 4.5, py - 4.5, 9, 9), "#2176b7", 1.4,
                                  "#ffffff", z=50)
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
            for _, start, end, bend in stickman_strokes(e, points):
                self._curve(start, end, bend, color, width, z=z, opacity=opacity)
            x, y = points["head"]
            radius = 1.7 * e.get("rig_unit", 10) if e.get("rig_style") == "simple" else 15
            self._ellipse((x - radius, y - radius, radius * 2, radius * 2),
                          color, width, color if e.get("head_filled") else "white",
                          z=z, opacity=opacity)
            if self.editor.selected_id == e["id"] and not ghost and not self.editor.playing:
                for joint, xy in points.items():
                    if e.get("rig_style") == "simple" and joint in SIMPLE_HIDDEN_LINKS:
                        continue
                    cx, cy = xy
                    self._ellipse((cx - 5, cy - 5, 10, 10),
                                  "#d97706" if joint == self.editor.selected_joint else "#2176b7",
                                  1.5, "#ffffff", z=50)

    def drawBackground(self, painter, rect):
        """Paint the world-aligned vector grid over the entire editor workspace.

        The white paper is only a camera/output boundary. Off-camera objects
        remain editable, and the grid continues behind them. No per-cell Qt
        graphics items are created. Grid spacing is always in *scene pixels*.
        """
        super().drawBackground(painter, rect)
        project = self.editor.project
        camera = project.scene["camera"]
        width = project.width / camera["zoom"]
        height = project.height / camera["zoom"]
        left = camera["center"][0] - width / 2
        top = camera["center"][1] - height / 2
        paper = QRectF(left, top, width, height)
        painter.save()
        painter.fillRect(paper, QColor("#ffffff"))
        settings = grid_settings(project)
        if settings["enabled"] and settings["visible"]:
            step = settings["spacing"]
            # Hide excessive fine detail only at tiny zoom values. Snap always
            # uses the full step irrespective of this visual LOD.
            scale = max(0.01, abs(self.transform().m11()))
            stride = max(1, math.ceil(6.0 / (step * scale)))
            displayed_step = step * stride
            # Only traverse the actual exposed scene rectangle, not the full
            # 6000x6000 scene or camera. This stays fast even at 1 px spacing.
            x_start = math.floor(rect.left() / displayed_step)
            x_end = math.ceil(rect.right() / displayed_step)
            y_start = math.floor(rect.top() / displayed_step)
            y_end = math.ceil(rect.bottom() / displayed_step)
            pen_minor = QPen(QColor("#d0d8e2"), 0)
            pen_major = QPen(QColor("#9daec2"), 0)
            pen_minor.setCosmetic(True)
            pen_major.setCosmetic(True)
            for i in range(x_start, x_end + 1):
                painter.setPen(pen_major if (i * stride) % 5 == 0 else pen_minor)
                x = i * displayed_step
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            for i in range(y_start, y_end + 1):
                painter.setPen(pen_major if (i * stride) % 5 == 0 else pen_minor)
                y = i * displayed_step
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        # Border is drawn last so it remains unambiguous when grid is visible.
        border = QPen(QColor("#3d485b"), 2)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(paper)
        painter.restore()

    def refresh_grid(self, *, force=False):
        """Invalidate the grid only when its appearance/camera actually changes.

        The editor refreshes the canvas for every pose and frame; rebuilding
        the entire background on every such operation causes unnecessary work.
        Undo/redo and project load are handled by comparing the paint signature.
        """
        project = self.editor.project
        grid = grid_settings(project)
        camera = project.scene["camera"]
        signature = (
            project.width, project.height,
            camera["center"][0], camera["center"][1], camera["zoom"],
            grid["enabled"], grid["visible"], grid["spacing"]
        )
        if not force and signature == getattr(self, "_grid_paint_signature", None):
            return
        self._grid_paint_signature = signature
        self.graphics.invalidate(self.graphics.sceneRect(),
                                 QGraphicsScene.SceneLayer.BackgroundLayer)
        self.viewport().update()

    def redraw(self) -> None:
        self.graphics.clear()
        self._preview_items.clear()
        project = self.editor.project
        cam = project.scene["camera"]
        width, height = project.width / cam["zoom"], project.height / cam["zoom"]
        x, y = cam["center"]
        left, top = x - width / 2, y - height / 2
        # Camera paper and grid are rendered directly by drawBackground().
        if self.editor.onion_check.isChecked() and self.editor.frame > 0 and not self.editor.playing:
            for e in visible_entities(project, self.editor.frame - 1):
                if e["kind"] == "stickman":
                    self._render_entity(e, self.editor.frame - 1, z=-10, opacity=0.32, ghost=True)
        for index, e in enumerate(visible_entities(project, self.editor.frame)):
            self._render_entity(e, self.editor.frame, z=index)
        if self.shape_start and self.shape_end:
            kind = self.editor.tool
            if kind in ("line", "rectangle", "circle"):
                demo = make_shape(kind, list(self.shape_start), list(self.shape_end))
                self._render_entity(demo, self.editor.frame, z=60, opacity=0.5)
        self.update_placement_preview()
        if len(self.editor.selected_ids) > 1 and not self.editor.playing:
            try:
                left, top, right, bottom = selection_bounds(
                    self.editor.project, self.editor.frame, self.editor.selected_ids)
                self._rect((left - 7, top - 7, right - left + 14, bottom - top + 14),
                           "#2679bf", 1.6, z=74)
            except ValueError:
                pass
        if self.marquee_start is not None and self.marquee_end is not None:
            x1, y1 = self.marquee_start
            x2, y2 = self.marquee_end
            rectangle = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
            pen = QPen(QColor("#2679bf"), 1.2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self.graphics.addRect(rectangle, pen,
                                  QBrush(QColor(50, 145, 230, 36))).setZValue(120)
        self.render_paste_preview()

    def _pick(self, point: tuple[float, float], *, segment=False, cycle=True, handles=True):
        """Choose the nearest handle, not the first joint in rig order.

        At small viewport zoom the hit radii of knees/ankles can overlap.
        Prefer the actually closest joint; repeated clicks cycle only through
        near-identical locations when their screen-space positions coincide.
        """
        scale = max(0.05, abs(self.transform().m11()))
        radius = 12.0 / scale
        near_equal_radius = 2.0 / scale
        handles = []
        bodies = []
        entities = reversed(list(visible_entities(self.editor.project, self.editor.frame)))
        for order, e in enumerate(entities):
            if get_layer(self.editor.project, e["layer_id"])["locked"]:
                continue
            ident = e["id"]
            if e["kind"] == "stickman":
                points, _ = joints_at(self.editor.project, e, self.editor.frame)
                if not segment and handles:
                    for joint, xy in points.items():
                        if (e.get("rig_style") == "simple" and
                                joint in SIMPLE_HIDDEN_LINKS and joint != "head"):
                            continue
                        distance = math.dist(point, xy)
                        if distance <= radius:
                            handles.append((distance, order, ident, joint))
                for joint, a, b, bend in stickman_strokes(e, points):
                    samples = bezier_points(a, b, bend)
                    distance = min(_distance_segment(point, samples[i], samples[i + 1])
                                   for i in range(len(samples) - 1))
                    if distance <= radius:
                        bodies.append((distance, order, ident,
                                       joint if segment else None))
            elif e["kind"] == "line":
                if not segment and handles:
                    for endpoint in ("a", "b"):
                        distance = math.dist(point, e[endpoint])
                        if distance <= radius:
                            handles.append((distance, order, ident, endpoint))
                samples = bezier_points(tuple(e["a"]), tuple(e["b"]), e.get("bend", 0))
                distance = min(_distance_segment(point, samples[i], samples[i + 1])
                               for i in range(len(samples) - 1))
                if distance <= radius:
                    bodies.append((distance, order, ident, None))
            else:
                a, b = e["a"], e["b"]
                x1, x2 = sorted((a[0], b[0]))
                y1, y2 = sorted((a[1], b[1]))
                if (x1 - radius <= point[0] <= x2 + radius and
                        y1 - radius <= point[1] <= y2 + radius):
                    bodies.append((radius, order, ident, None))
        if handles:
            handles.sort(key=lambda hit: (hit[0], hit[1]))
            nearest = handles[0][0]
            similar = [hit for hit in handles if hit[0] <= nearest + near_equal_radius]
            current = (self.editor.selected_id, self.editor.selected_joint)
            if cycle and len(similar) > 1 and (similar[0][2], similar[0][3]) == current:
                for hit in similar[1:]:
                    if (hit[2], hit[3]) != current:
                        return hit[2], hit[3]
            return handles[0][2], handles[0][3]
        if bodies:
            bodies.sort(key=lambda hit: (hit[0], hit[1]))
            return bodies[0][2], bodies[0][3]
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
        raw_point = self.to_world(event)
        point = self.snap(raw_point)
        tool = self.editor.tool
        if tool == "stickman":
            self.editor.add_stickman(point)
            return
        if tool in ("line", "rectangle", "circle"):
            self.shape_start = point
            self.shape_end = point
            self.redraw()
            return
        eid, joint = self._pick(
            raw_point, segment=bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        )
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

    def mouseDoubleClickEvent(self, event):
        """Double-click a joint handle to restore its original segment length.

        Hit-test in un-snapped world coordinates and never reset a segment by
        double-clicking empty space, a stickman's line, or another shape.
        Bypass repeated-click selection cycling for this precise gesture.
        """
        if (event.button() != Qt.MouseButton.LeftButton or
                self.editor.playing or self.editor.tool != "select"):
            return super().mouseDoubleClickEvent(event)
        ident, joint = self._pick(self.to_world(event), cycle=False)
        if ident is None or joint is None:
            return super().mouseDoubleClickEvent(event)
        entity = get_entity(self.editor.project, ident)
        if (entity["kind"] != "stickman" or
                joint == "pelvis" or
                math.hypot(*rig_offsets(entity)[joint][1]) < 1e-6):
            event.accept()
            return
        # Qt delivers the second mouse-press before its double-click event.
        # Do not leave that press's unfinished drag transaction active.
        self.drag_id = self.drag_joint = self.drag_start = None
        self.drag_origin = self.drag_before = None
        self.editor.select_entity(ident, joint)
        self.editor.reset_selected_length()
        event.accept()

    def mouseMoveEvent(self, event):
        raw_point = self.to_world(event)
        point = self.snap(raw_point)
        self.editor.update_canvas_coordinates(raw_point)
        if self.panning and self.pan_start is not None:
            current = event.position().toPoint()
            delta = current - self.pan_start
            self.pan_pixels(delta.x(), delta.y())
            self.pan_start = current
            return
        if self.shape_start is not None:
            self.shape_end = point
            self.redraw()
            return
        if self.drag_id is not None and self.drag_start is not None:
            e = get_entity(self.editor.project, self.drag_id)
            if e["kind"] == "stickman" and self.drag_joint and self.drag_joint != "pelvis":
                joint = self.drag_joint
                if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    points, _ = joints_at(self.editor.project, e, self.editor.frame)
                    parent = rig_offsets(e)[joint][0]
                    if parent:
                        set_segment_bend(e, joint,
                                         bend_for_drag(points[parent], points[joint], point))
                else:
                    resizing = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    if resizing:
                        points, _ = joints_at(self.editor.project, e, self.editor.frame)
                        parent = rig_offsets(e)[joint][0]
                        if parent and math.hypot(*rig_offsets(e)[joint][1]) > 1e-6:
                            set_segment_scale(e, joint, segment_scale_for_drag(
                                e, joint, points[parent], raw_point))
                    # Shift length editing intentionally bypasses grid snapping.
                    # Regular joint posing continues to respect Snap.
                    target = raw_point if resizing else point
                    angle = angle_for_drag(self.editor.project, e, self.editor.frame, joint, target)
                    set_key(self.editor.project, self.drag_id,
                            f"pose.{joint}", self.editor.frame, angle)
            elif e["kind"] == "stickman":
                assert self.drag_origin is not None
                dx, dy = point[0] - self.drag_start[0], point[1] - self.drag_start[1]
                set_key(self.editor.project, self.drag_id, "position", self.editor.frame,
                        [self.drag_origin[0] + dx, self.drag_origin[1] + dy])
            elif e["kind"] == "line" and self.drag_joint in ("a", "b"):
                e[self.drag_joint] = [point[0], point[1]]
            elif e["kind"] == "line" and event.modifiers() & Qt.KeyboardModifier.AltModifier:
                e["bend"] = bend_for_drag(tuple(e["a"]), tuple(e["b"]), point)
            else:
                before = next((entry for entry in self.drag_before["scenes"][0]["entities"]
                               if entry["id"] == self.drag_id), None)
                assert before is not None
                dx, dy = point[0] - self.drag_start[0], point[1] - self.drag_start[1]
                e["a"] = [before["a"][0] + dx, before["a"][1] + dy]
                e["b"] = [before["b"][0] + dx, before["b"][1] + dy]
            self.redraw()
            return
        self._hover_position = point
        self.update_placement_preview()
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
                step = grid_settings(self.editor.project)["spacing"]
                point = (start[0] + step * 8,
                         start[1] + (0 if self.editor.tool == "line" else step * 6))
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
    def __init__(self, path: str | None = None, *, settings: QSettings | None = None):
        super().__init__()
        self.project = Project()
        self.history = History()
        self.frame = 0
        self.path: Path | None = None
        self.saved_snapshot = self.project.to_dict()
        self.selected_id: str | None = None
        self.selected_joint: str | None = None
        self.selected_ids: set[str] = set()
        self._copy_buffer = None
        self.tool = "select"
        self.playing = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)
        self.setWindowTitle("StickRig Studio")
        self.resize(1380, 850)
        # Application-level window state is intentionally independent of each
        # project's QMainWindow dock layout (which lives in the project JSON).
        self._window_settings = (
            settings if settings is not None else QSettings("RawsTourix", "StickRig Studio")
        )
        self._restore_maximized = False
        self._docks = []
        self._restoring_layout = False
        self._thumb_epoch = 0
        self._thumb_pending = False
        self._thumb_cache = OrderedDict()
        self._frame_focus_request = 0
        self._focused_frame_row = None
        self._cursor_world = None
        self._building = True
        self.build_ui()
        self._building = False
        if path:
            self.load_file(Path(path))
        self.refresh()
        if not path:
            self.restore_workspace()
        self.restore_window_state()
        QTimer.singleShot(0, self.canvas.frame_camera)

    def build_ui(self):
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Choose Stickman, then click the canvas. Use Select to drag joints.")
        self._build_menus()
        self.setDockNestingEnabled(True)
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
        grid = grid_settings(self.project)
        self.grid_check = QCheckBox("Grid")
        self.grid_check.setToolTip("Enable or disable the grid, preserving preferences")
        self.grid_check.setChecked(grid["enabled"])
        toolbar.addWidget(self.grid_check)
        self.grid_visible_check = QCheckBox("Show")
        self.grid_visible_check.setChecked(grid["visible"])
        self.grid_visible_check.setToolTip("Hide grid without changing snap settings")
        toolbar.addWidget(self.grid_visible_check)
        self.snap_check = QCheckBox("Snap")
        self.snap_check.setChecked(grid["snap"])
        self.snap_check.setToolTip("Snap points to grid spacing")
        toolbar.addWidget(self.snap_check)
        toolbar.addWidget(QLabel(" Step "))
        self.grid_step = QSpinBox()
        self.grid_step.setObjectName("grid_spacing")
        self.grid_step.setRange(1, 200)
        self.grid_step.setSuffix(" px")
        self.grid_step.setValue(grid["spacing"])
        toolbar.addWidget(self.grid_step)
        self.grid_check.toggled.connect(self.change_grid)
        self.grid_visible_check.toggled.connect(self.change_grid)
        self.snap_check.toggled.connect(self.change_grid)
        self.grid_step.valueChanged.connect(self.change_grid)
        self.onion_check = QCheckBox("Onion skin")
        self.onion_check.setChecked(True)
        self.onion_check.stateChanged.connect(lambda _: self.canvas.redraw())
        toolbar.addWidget(self.onion_check)
        toolbar.addSeparator()
        camera_button = QPushButton("Frame camera")
        camera_button.clicked.connect(lambda: self.canvas.frame_camera())
        toolbar.addWidget(camera_button)

        self.canvas = Canvas(self)
        # The canvas has a separate, thin information strip. It is a permanent
        # part of the central editor, not a movable dock or an overlay.
        center = QWidget(self)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.canvas, 1)
        self.canvas_info = QFrame(center)
        self.canvas_info.setObjectName("canvas_info_bar")
        self.canvas_info.setStyleSheet(
            "QFrame#canvas_info_bar {background: #f6f8fa; "
            "border-top: 1px solid #c2cbd4;} "
            "QLabel {color: #364554; padding: 0 4px;}"
        )
        info_layout = QHBoxLayout(self.canvas_info)
        info_layout.setContentsMargins(8, 2, 8, 2)
        info_layout.setSpacing(10)
        self.cursor_coords_label = QLabel("Cursor: —")
        self.selection_coords_label = QLabel("Selected: —")
        info_layout.addWidget(self.cursor_coords_label)
        info_layout.addWidget(self.selection_coords_label)
        info_layout.addStretch(1)
        self.canvas.zoom_hud.setParent(self.canvas_info)
        self.canvas.zoom_hud.setStyleSheet(
            "#viewportZoomHud {background: transparent; border: 0;}"
        )
        info_layout.addWidget(self.canvas.zoom_hud)
        self.canvas.zoom_hud.show()
        center_layout.addWidget(self.canvas_info)
        self.setCentralWidget(center)
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
        self.segment_box = QGroupBox("Selected segment")
        segment_layout = QVBoxLayout(self.segment_box)
        segment_layout.addWidget(QLabel("Visible length"))
        self.segment_length = QDoubleSpinBox(self.segment_box)
        self.segment_length.setRange(20, 250)
        self.segment_length.setSingleStep(5)
        self.segment_length.setSuffix(" %")
        self.segment_length.valueChanged.connect(self.change_segment_length)
        segment_layout.addWidget(self.segment_length)
        segment_layout.addWidget(QLabel("Curve / bend"))
        self.segment_bend_spin = QDoubleSpinBox(self.segment_box)
        self.segment_bend_spin.setRange(-100, 100)
        self.segment_bend_spin.setSingleStep(5)
        self.segment_bend_spin.setSuffix(" %")
        self.segment_bend_spin.valueChanged.connect(self.change_segment_bend)
        segment_layout.addWidget(self.segment_bend_spin)
        self.segment_box.setVisible(False)
        select_layout.addWidget(self.segment_box)
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
        self.frames_list = FrameStrip(self)
        self.frames_list.setIconSize(QSize(128, 72))
        self.frames_list.setSpacing(6)
        self.frames_list.setMinimumWidth(265)
        self.frames_list.currentRowChanged.connect(
            lambda row: self.set_frame(row) if row >= 0 else None
        )
        self.frames_list.verticalScrollBar().valueChanged.connect(
            lambda _: self.schedule_thumbnails()
        )
        self.frames_list.viewport().installEventFilter(self)
        frame_layout.addWidget(self.frames_list, 1)
        # A single plus action creates a blank animation frame. Duplication is
        # available on the frame's quick-action buttons and context menu.
        self.frame_plus = QToolButton(frames)
        self.frame_plus.setObjectName("frame_add_blank")
        self.frame_plus.setText("+")
        self.frame_plus.setToolTip("Add a blank frame after the current frame")
        self.frame_plus.setFixedHeight(34)
        self.frame_plus.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.frame_plus.setToolTip("Add blank frame after the current frame")
        self.frame_plus.clicked.connect(self.add_blank_frame)
        # Stretch across the entire dock width; resize with the Frames panel.
        frame_layout.addWidget(self.frame_plus)
        self.frames_dock = self.make_dock(
            "Frames", "frames_dock", frames, Qt.DockWidgetArea.LeftDockWidgetArea
        )

        # QToolBar cannot be nested vertically with other bottom toolbars.
        # A movable QDockWidget *can*, so Playback is a true dock above Elements.
        playback_body = QFrame(self)
        playback_body.setObjectName("playback_toolbar")
        playback_body.setFixedHeight(40)
        playback_row = QHBoxLayout(playback_body)
        playback_row.setContentsMargins(6, 3, 6, 3)
        playback_row.setSpacing(4)
        playback_row.addStretch(1)
        for label, handler in (("|◀", self.first_frame), ("◀", self.prev_frame),
                               ("▶ Play", self.toggle_play), ("▶|", self.next_frame)):
            button = QPushButton(label)
            button.setMaximumHeight(28)
            button.clicked.connect(handler)
            playback_row.addWidget(button)
            if label == "▶ Play":
                self.play_btn = button
        playback_row.addSpacing(12)
        playback_row.addWidget(QLabel("Frame "))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(1, self.project.duration)
        self.frame_spin.valueChanged.connect(lambda value: self.set_frame(value - 1))
        playback_row.addWidget(self.frame_spin)
        self.frame_total = QLabel("/ 1")
        playback_row.addWidget(self.frame_total)
        playback_row.addSpacing(12)
        playback_row.addWidget(QLabel("FPS "))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.valueChanged.connect(self.change_fps)
        playback_row.addWidget(self.fps_spin)
        playback_row.addStretch(1)
        self.playback_toolbar = playback_body  # Legacy test-friendly reference.
        self.playback_dock = self.make_dock(
            "Playback", "playback_dock", playback_body,
            Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.playback_dock.setMaximumHeight(92)

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
        # Both widgets live in the same nested bottom dock area, one above the
        # other. Either can be moved, floated or re-stacked by its title bar.
        self.splitDockWidget(
            self.playback_dock, self.library_dock, Qt.Orientation.Vertical
        )

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

    def restore_window_state(self):
        """Restore size, screen position and maximize preference across launches."""
        geometry = self._window_settings.value("window/geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        self._restore_maximized = self._window_settings.value(
            "window/maximized", False, type=bool
        )
        if self._restore_maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def persist_window_state(self):
        """Persist only on successful close; a cancelled close writes nothing."""
        self._window_settings.setValue("window/geometry", self.saveGeometry())
        self._window_settings.setValue("window/maximized", self.isMaximized())
        self._window_settings.sync()

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
        if (hasattr(self, "frames_list") and
                watched is self.frames_list.viewport() and
                event.type() == QEvent.Type.Resize):
            QTimer.singleShot(0, self.schedule_thumbnails)
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
        """Find viewport rows without depending on a hit in the item margin.

        indexAt(5, 5) can return an invalid index when the first pixel falls
        in a frame-card gap. Its previous fallback silently rendered rows 0..9
        even if the viewport had scrolled to frames 11+. Rect-based binary
        search works for offscreen QListWidget items as well as visible ones.
        """
        strip = self.frames_list
        count = strip.count()
        if not count:
            return range(0)
        viewport_height = strip.viewport().height()
        if viewport_height <= 1:
            return range(0, min(count, 3))

        def bounds(row):
            return strip.visualItemRect(strip.item(row))

        lo, hi = 0, count
        while lo < hi:
            mid = (lo + hi) // 2
            if bounds(mid).bottom() < 0:
                lo = mid + 1
            else:
                hi = mid
        first = lo

        lo, hi = first, count
        while lo < hi:
            mid = (lo + hi) // 2
            if bounds(mid).top() <= viewport_height:
                lo = mid + 1
            else:
                hi = mid
        last_exclusive = lo
        return range(max(0, min(first, count) - 1),
                     min(count, max(first + 1, last_exclusive) + 1))

    def keep_active_frame_visible(self):
        """Reveal the selected frame after Qt finishes relaying out the list."""
        row = self.frame
        strip = self.frames_list
        if not (0 <= row < strip.count()):
            return
        item = strip.item(row)
        rect = strip.visualItemRect(item)
        viewport = strip.viewport().rect()
        if rect.top() < viewport.top() or rect.bottom() > viewport.bottom():
            strip.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
        self.schedule_thumbnails()

    def request_active_frame_focus(self):
        self._frame_focus_request += 1
        request = self._frame_focus_request
        # Reveal immediately when possible, and again after Qt recomputes
        # item geometry following insertion/duplicate/deletion of a frame.
        self.keep_active_frame_visible()

        def deferred():
            if request == self._frame_focus_request:
                self.keep_active_frame_visible()

        QTimer.singleShot(0, deferred)

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
                old_item = self.frames_list.item(old)
                if old_item is not None:
                    old_item.setIcon(QIcon())
                    old_item.setData(Qt.ItemDataRole.UserRole + 1, -1)
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
        recreated = self.frames_list.count() != self.project.duration
        if recreated:
            self.frames_list.blockSignals(True)
            self.frames_list.clear()
            self._thumb_cache.clear()
            # Item rows remain lightweight; thumbnail bitmaps are created lazily.
            for row in range(self.project.duration):
                item = QListWidgetItem(str(row + 1))
                item.setSizeHint(QSize(255, 154))
                self.frames_list.addItem(item)
            self.frames_list.blockSignals(False)
            self._displayed_hidden_frames = set()
            content_changed = True
        if content_changed:
            self._thumb_epoch += 1
            for row in self._thumb_cache:
                item = self.frames_list.item(row)
                if item:
                    item.setIcon(QIcon())
                    item.setData(Qt.ItemDataRole.UserRole + 1, -1)
            self._thumb_cache.clear()
            hidden = set(self.project.scene.get("hidden_frames", []))
            previous = getattr(self, "_displayed_hidden_frames", set())
            # Only rows whose visibility changed need text/flag repaint.
            for row in hidden ^ previous:
                item = self.frames_list.item(row)
                if item is not None:
                    item.setText(f"{row + 1}" + ("  (hidden)" if row in hidden else ""))
                    item.setData(Qt.ItemDataRole.UserRole + 2, row in hidden)
                    item.setForeground(QColor("#8894a3" if row in hidden else "#20252d"))
            self._displayed_hidden_frames = hidden
        if self.frames_list.currentRow() != self.frame:
            self.frames_list.blockSignals(True)
            self.frames_list.setCurrentRow(self.frame)
            self.frames_list.blockSignals(False)
        changed_selection = self._focused_frame_row != self.frame
        if recreated or changed_selection:
            self._focused_frame_row = self.frame
            self.request_active_frame_focus()
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
        self.escape_action = QAction("Return to Select / Move", self)
        self.escape_action.setShortcut(QKeySequence("Esc"))
        self.escape_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.escape_action.triggered.connect(self.escape_to_select)
        edit_menu.addAction(self.escape_action)
        for title, callback, shortcut in [("Undo", self.undo, "Ctrl+Z"),
                                          ("Redo", self.redo, "Ctrl+Y"),
                                          ("Copy selected", self.copy_selected, "Ctrl+C"),
                                          ("Paste at cursor…", self.start_paste, "Ctrl+V"),
                                          ("Paste at original position", self.paste_in_place, "Ctrl+Shift+V"),
                                          ("Delete selected objects", self.delete_selected, "Delete")]:
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

    def escape_to_select(self):
        """Esc cancels pending paste/marquee; otherwise leaves placement mode."""
        if self.canvas._paste_clipboard is not None:
            self.canvas.cancel_paste()
            return
        self.canvas.marquee_start = self.canvas.marquee_end = None
        self.canvas.shape_start = self.canvas.shape_end = None
        self.canvas.clear_placement_preview()
        self.select_tool("select")
        self.canvas.redraw()

    def select_tool(self, key):
        if key != "select" and self.canvas._paste_clipboard is not None:
            self.canvas.cancel_paste()
        self.tool = key
        self.canvas.update_placement_preview()
        for name, action in self.tool_actions.items():
            action.setChecked(key == name)
        self.statusBar().showMessage({"select": "Drag joint to pose; Shift+joint to shorten; Alt+bone to bend; Esc exits placement.",
                                      "stickman": "Click anywhere to add a stickman.",
                                      "line": "Drag to draw a line.", "rectangle": "Drag to draw a rectangle.",
                                      "circle": "Drag to draw an ellipse."}[key])

    def select_entity(self, entity_id, joint=None):
        self.selected_ids = {entity_id} if entity_id else set()
        self.selected_id = entity_id
        self.selected_joint = joint
        self.refresh_inspector()
        self.update_canvas_coordinates(self._cursor_world)

    def select_entities(self, ids):
        """Multiple selected objects share one group transform, not joint handles."""
        selected = set(ids)
        if len(selected) == 1:
            self.select_entity(next(iter(selected)))
            return
        self.selected_ids = selected
        self.selected_id = self.selected_joint = None
        self.refresh_inspector()
        self.update_canvas_coordinates(self._cursor_world)

    def copy_selected(self):
        if not self.selected_ids or self.playing:
            return
        try:
            self._copy_buffer = copy_entities(self.project, self.frame, self.selected_ids)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self.statusBar().showMessage(
            f"Copied {len(self._copy_buffer['entities'])} object(s). Ctrl+V to place.", 4000
        )

    def start_paste(self):
        """Ctrl+V previews at the mouse or visible centre; LMB commits, Esc cancels."""
        if self._copy_buffer is None or self.playing:
            return
        try:
            # Materialization validates layers before showing an invalid preview.
            materialize_paste(self.project, self.frame, self._copy_buffer,
                              tuple(self._copy_buffer["bounds"][:2]))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self.select_tool("select")
        self.canvas.start_paste_preview(self._copy_buffer)
        self.statusBar().showMessage("Move to place copied objects; click to confirm, Esc to cancel.")

    def paste_in_place(self):
        if self._copy_buffer is None or self.playing:
            return
        self.canvas.cancel_paste()
        self.commit_paste(self._copy_buffer, tuple(self._copy_buffer["bounds"][:2]))

    def commit_paste(self, clipboard, top_left):
        try:
            clones = materialize_paste(self.project, self.frame, clipboard, top_left)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return False
        self.mutate(lambda: self.project.scene["entities"].extend(clones))
        self.select_entities([e["id"] for e in clones])
        self.canvas.redraw()
        return True

    def update_canvas_coordinates(self, point):
        """Inspector-style coordinates in scene units, independent of viewport zoom."""
        self._cursor_world = point
        if point is None:
            self.cursor_coords_label.setText("Cursor: —")
        else:
            self.cursor_coords_label.setText(f"Cursor  X: {point[0]:.1f}  Y: {point[1]:.1f}")
        if len(self.selected_ids) > 1:
            self.selection_coords_label.setText(f"{len(self.selected_ids)} objects selected")
            return
        if not self.selected_id:
            self.selection_coords_label.setText("Selected: —")
            return
        try:
            e = get_entity(self.project, self.selected_id)
            if e["kind"] == "stickman":
                if self.selected_joint:
                    p = joints_at(self.project, e, self.frame)[0][self.selected_joint]
                else:
                    p = value_at(self.project, e, "position", self.frame)
            else:
                p = [(e["a"][0] + e["b"][0]) / 2,
                     (e["a"][1] + e["b"][1]) / 2]
            name = self.selected_joint or e["kind"]
            self.selection_coords_label.setText(
                f"Selected ({name})  X: {p[0]:.1f}  Y: {p[1]:.1f}"
            )
        except (ValueError, KeyError):
            self.selection_coords_label.setText("Selected: —")

    def can_edit_layer(self, layer_id):
        if get_layer(self.project, layer_id)["locked"]:
            self.statusBar().showMessage(f"Layer '{layer_id}' is locked", 4000)
            return False
        return True

    def add_stickman(self, point):
        if not self.can_edit_layer("actors"):
            return
        obj = make_stickman(*point, frame_index=self.frame,
                            rig_unit=grid_settings(self.project)["spacing"])
        def add_actor():
            self.project.scene["entities"].append(obj)
        self.mutate(add_actor)
        self.select_entity(obj["id"])

    def add_shape(self, kind, a, b):
        # Drawing a line/shape does not make it a persistent background.
        if not self.can_edit_layer("actors"):
            return
        obj = make_shape(kind, list(a), list(b),
                         layer_id="actors", frame_index=self.frame)
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
        grid = grid_settings(self.project)
        for checkbox, value in (
            (self.grid_check, grid["enabled"]),
            (self.grid_visible_check, grid["visible"]),
            (self.snap_check, grid["snap"]),
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(value)
            checkbox.blockSignals(False)
        self.grid_step.blockSignals(True)
        self.grid_step.setValue(grid["spacing"])
        self.grid_step.blockSignals(False)
        self.grid_visible_check.setEnabled(grid["enabled"])
        self.snap_check.setEnabled(grid["enabled"])
        # A frame-local selection cannot silently edit an object belonging to
        # another frame. Clear stale selection after insert, move or delete.
        visible_ids = {e["id"] for e in visible_entities(self.project, self.frame)}
        stale = self.selected_ids - visible_ids
        if stale:
            self.select_entities(self.selected_ids & visible_ids)
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
        self.canvas.refresh_grid()
        self.update_canvas_coordinates(self._cursor_world)
        self.refresh_title()

    def change_grid(self, *_):
        if self._building:
            return
        new = {"enabled": self.grid_check.isChecked(),
               "visible": self.grid_visible_check.isChecked(),
               "snap": self.snap_check.isChecked(),
               "spacing": self.grid_step.value()}
        if grid_settings(self.project) != new:
            self.mutate(lambda: self.project.scene.__setitem__("grid", new))
        else:
            self.canvas.refresh_grid()

    def reset_selected_length(self):
        """Undoable exact rest-length reset, invoked by double-clicking a joint."""
        if not self.selected_id or not self.selected_joint:
            return
        entity = get_entity(self.project, self.selected_id)
        if (entity["kind"] != "stickman" or self.selected_joint == "pelvis" or
                math.hypot(*rig_offsets(entity)[self.selected_joint][1]) < 1e-6):
            return
        joint = self.selected_joint
        if abs(entity.get("segment_scales", {}).get(joint, 1.0) - 1.0) > 1e-9:
            self.mutate(lambda: set_segment_scale(entity, joint, 1.0))
        else:
            self.segment_length.blockSignals(True)
            self.segment_length.setValue(100)
            self.segment_length.blockSignals(False)

    def change_segment_length(self, value):
        if not self.selected_id or not self.selected_joint:
            return
        joint = self.selected_joint
        entity = get_entity(self.project, self.selected_id)
        if entity["kind"] != "stickman":
            return
        factor = value / 100
        if abs(entity.get("segment_scales", {}).get(joint, 1.0) - factor) > 1e-6:
            self.mutate(lambda: set_segment_scale(entity, joint, factor))

    def change_segment_bend(self, value):
        if not self.selected_id:
            return
        entity = get_entity(self.project, self.selected_id)
        new = value / 100
        if entity["kind"] == "stickman" and self.selected_joint:
            joint = self.selected_joint
            if abs(entity.get("segment_bends", {}).get(joint, 0) - new) > 1e-6:
                self.mutate(lambda: set_segment_bend(entity, joint, new))
        elif entity["kind"] == "line":
            if abs(entity.get("bend", 0) - new) > 1e-6:
                self.mutate(lambda: entity.__setitem__("bend", new))

    def refresh_inspector(self):
        try:
            e = get_entity(self.project, self.selected_id) if self.selected_id else None
        except ValueError:
            e = None
            self.selected_id = self.selected_joint = None
        if len(self.selected_ids) > 1:
            self.info_label.setText(f"{len(self.selected_ids)} objects selected — move as a group")
        elif not e:
            self.info_label.setText("Nothing selected")
        else:
            self.info_label.setText(f"{e['kind']} · {e['id']}\nLayer: {e['layer_id']}\nColor: {e['color']}" +
                                    (f"\nJoint: {self.selected_joint}" if self.selected_joint else ""))
        self.fill_head.blockSignals(True)
        self.fill_head.setVisible(bool(e and e["kind"] == "stickman"))
        self.fill_head.setChecked(bool(e and e.get("head_filled", False)))
        self.fill_head.blockSignals(False)
        joint = self.selected_joint
        show_segment = bool(e and (
            (e["kind"] == "stickman" and joint and joint != "pelvis" and
             math.hypot(*segment_offset(e, joint)) > 0.001) or
            e["kind"] == "line"))
        self.segment_box.setVisible(show_segment)
        if show_segment:
            values = (e.get("segment_scales", {}).get(joint, 1.0) * 100,
                      e.get("segment_bends", {}).get(joint, 0) * 100) if e["kind"] == "stickman" else (100, e.get("bend", 0) * 100)
            self.segment_length.setEnabled(e["kind"] == "stickman")
            for control, value in ((self.segment_length, values[0]),
                                   (self.segment_bend_spin, values[1])):
                control.blockSignals(True)
                control.setValue(value)
                control.blockSignals(False)

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
        if not self.selected_ids:
            return
        ids = self.selected_ids.copy()
        if any(not self.can_edit_layer(get_entity(self.project, ident)["layer_id"])
               for ident in ids):
            return
        def op():
            self.project.scene["entities"] = [e for e in self.project.scene["entities"]
                                              if e["id"] not in ids]
            self.project.scene["tracks"] = [t for t in self.project.scene["tracks"]
                                            if t["target_id"] not in ids]
        self.select_entities([])
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
        visible_ids = {e["id"] for e in visible_entities(self.project, self.frame)}
        if not self.selected_ids.issubset(visible_ids):
            self.select_entities(self.selected_ids & visible_ids)
        if self.playing and self.frame in self._play_sequence:
            self._play_cursor = self._play_sequence.index(self.frame)
        self.refresh(content_changed=False)

    def add_frame(self):
        """Ctrl+D keeps the current pose (explicit duplicate)."""
        self.insert_frame_after(self.frame)

    def add_blank_frame(self):
        self.insert_blank_frame_after(self.frame)

    def insert_blank_frame_after(self, source):
        self.stop_playback()
        self.mutate(lambda: setattr(self, "frame",
                                    insert_blank_frame(self.project, source)))

    def append_blank_frame(self):
        self.insert_blank_frame_after(self.project.duration - 1)

    def insert_frame_after(self, source):
        self.stop_playback()
        self.mutate(lambda: setattr(self, "frame", insert_frame(self.project, source)))

    def duplicate_frame_at(self, source):
        # Hold mode: inserting after a frame duplicates its visual state.
        self.insert_frame_after(source)

    def append_frame(self):
        self.insert_frame_after(self.project.duration - 1)

    def remove_frame(self):
        self.delete_frame_at(self.frame)

    def delete_frame_at(self, source):
        self.stop_playback()
        try:
            self.mutate(lambda: setattr(self, "frame", delete_frame(self.project, source)))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 3500)

    def move_frame_to(self, source, target):
        self.stop_playback()
        try:
            self.mutate(lambda: setattr(self, "frame", move_frame(self.project, source, target)))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 3500)

    def toggle_frame_visibility(self, frame):
        self.stop_playback()
        hidden = frame in self.project.scene.get("hidden_frames", [])
        try:
            self.mutate(lambda: set_frame_hidden(self.project, frame, not hidden))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 3500)

    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self._play_sequence = visible_frame_indices(self.project)
            if self.frame not in self._play_sequence:
                self.frame = self._play_sequence[0]
            self._play_cursor = self._play_sequence.index(self.frame)
            self.timer.start(max(1, round(1000 / self.project.fps)))
            self.play_btn.setText("⏸ Pause")
            self.refresh(content_changed=False)
        else:
            self.timer.stop()
            self.play_btn.setText("▶ Play")
        self.canvas.redraw()
        if not self.playing:
            self.schedule_thumbnails()

    def advance_frame(self):
        if not self.playing:
            return
        self._play_cursor = (self._play_cursor + 1) % len(self._play_sequence)
        self.set_frame(self._play_sequence[self._play_cursor])

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
        self.selected_ids.clear()
        self.selected_id = self.selected_joint = None
        self.canvas.cancel_paste()
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
        self.selected_ids.clear()
        self.selected_id = self.selected_joint = None
        self.canvas.cancel_paste()
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
        count = len(visible_frame_indices(self.project))
        progress = QProgressDialog("Rendering frames…", "Cancel", 0, count, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        try:
            def update(frame, total):
                progress.setValue(frame)
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise RuntimeError("Export cancelled")
            export_mp4(self.project, path, progress=update)
            progress.setValue(count)
            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
        finally:
            progress.close()

    def stop_playback(self):
        if self.playing:
            self.toggle_play()

    def about(self):
        QMessageBox.information(self, "StickRig Studio 0.5.2",
                                "StickRig Studio 0.5.2 — prototype\n\n"
                                "Esc: leave placement and return to Select / Move.\n"
                                "Drag joint to pose; Shift+drag adjusts length independently of Snap.\n"
                                "Reset length to 100% in Inspector; near 100% Shift drag snaps back.\n"
                                "Alt+drag a bone bends it; Inspector edits length and curvature.\n"
                                "Drag line endpoints to rotate/shorten a staff; Alt+drag line to curve.\n"
                                "Grid / Show / Snap / Step: separate scene-pixel grid controls.\n"
                                "Tools: click to add a stickman; drag to draw shapes.\n"
                                "Frames: + adds blank frame; Ctrl+D duplicates pose; active frame stays in view.\n"
                                "Playback is a movable bottom dock above Elements.\n"
                                "Elements follow the pointer as a transparent placement preview.\n"
                                "Camera: static X/Y/zoom, Frame camera (Ctrl+0).\n"
                                "Undo/Redo: Ctrl+Z / Ctrl+Y. Save: Ctrl+S.\n"
                                "Pan: touchpad swipe / wheel / middle mouse; adaptive sensitivity.\n"
                                "Zoom and coordinates: strip below the canvas.\n"
                                "Docks: drag, resize, close, reopen from View menu.\n\n"
                                "Project is editable JSON; experimental AI patching via stickrig-cli.")

    def closeEvent(self, event):
        self.capture_workspace(force=True)
        if self.check_unsaved():
            self.persist_window_state()
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    window = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    if window._restore_maximized:
        window.showMaximized()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
