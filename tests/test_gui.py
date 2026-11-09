"""Executed by CI with QT_QPA_PLATFORM=offscreen and PySide6 installed."""

import os
import importlib.util

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None,
                                reason="PySide6 not installed")


def test_window_project_edit_save_and_reopen(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.project.duration == 1
        w.add_stickman((350, 400))
        assert len(w.project.scene["entities"]) == 1
        w.add_frame()
        assert w.project.duration == 2
        assert w.frame == 1
        w.undo()
        assert w.project.duration == 1
        w.redo()
        assert w.project.duration == 2
        path = tmp_path / "gui.stickrig.json"
        w.path = path
        assert w.save_file()
        w.load_file(path)
        assert len(w.project.scene["entities"]) == 2
        assert {e["frame_index"] for e in w.project.scene["entities"]} == {0, 1}
        assert w.project.duration == 2
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()

def test_frame_sidebar_and_project_layout_roundtrip(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    from stickrig.model import load_project
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.frames_list.count() == 1
        assert w.frames_list.currentRow() == 0
        assert w.frames_dock.isVisible() or not w.isVisible()
        assert w.library_list.count() >= 4
        w.add_frame()
        assert w.frames_list.count() == 2
        w.set_frame(0)
        w.render_one_thumbnail()
        assert not w.frames_list.item(0).icon().isNull()
        path = tmp_path / "layout.json"
        w.path = path
        assert w.save_file()
        stored = load_project(path)
        assert stored.workspace.get("dock_state")
        w.load_file(path)
        assert w.frames_list.count() == 2
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_preview_strip_and_frame_actions():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    from stickrig.model import get_entity, set_key, value_at
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.playback_toolbar.objectName() == "playback_toolbar"
        assert w.canvas.zoom_label.text().endswith("%")
        assert w.canvas.pan_sensitivity(0.2) > w.canvas.pan_sensitivity(2.0)
        w.add_stickman((140, 140))
        hero = w.selected_id
        w.append_frame()
        w.append_frame()
        second_id = next(e["id"] for e in w.project.scene["entities"]
                         if e.get("frame_index") == 1)
        set_key(w.project, second_id, "pose.elbow_l", 1, 38)
        w.after_change()
        w.move_frame_to(1, 2)
        assert get_entity(w.project, second_id)["frame_index"] == 2
        assert value_at(w.project, get_entity(w.project, second_id),
                        "pose.elbow_l", 2) == 38
        w.toggle_frame_visibility(1)
        assert w.project.scene["hidden_frames"] == [1]
        w.toggle_play()
        assert w._play_sequence == [0, 2]
        w.advance_frame()
        assert w.frame in (0, 2)
        w.toggle_play()
        w.duplicate_frame_at(1)
        assert w.project.duration == 4
        w.delete_frame_at(3)
        assert w.project.duration == 3
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_frame_thumbnail_generation_is_lazy():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    from stickrig.model import insert_frame
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        for _ in range(120):
            insert_frame(w.project, w.project.duration - 1)
        w.refresh()
        w.render_one_thumbnail()
        assert w.frames_list.count() == 121
        assert len(w._thumb_cache) <= 15
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_canvas_placement_ghost_never_creates_real_entity():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert len(w.project.scene["entities"]) == 0
        w.select_tool("stickman")
        w.canvas._hover_position = (220, 210)
        w.canvas.update_placement_preview()
        assert len(w.canvas._preview_items) >= 1
        assert len(w.project.scene["entities"]) == 0
        w.select_tool("select")
        assert w.canvas._preview_items == []
        before = w.canvas.transform().m11()
        w.canvas.zoom_by(1.2)
        assert w.canvas.transform().m11() > before
        assert w.canvas.zoom_label.text().endswith("%")
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_incremental_frame_badges_and_partial_viewport_update():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QGraphicsView
    from stickrig.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert (w.canvas.viewportUpdateMode() ==
                QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        w.append_frame()
        w.append_frame()
        assert [w.frames_list.item(i).text() for i in range(3)] == ["1", "2", "3"]
        w.toggle_frame_visibility(1)
        assert w.frames_list.item(1).text().endswith("(hidden)")
        assert w.frames_list.item(1).data(Qt.ItemDataRole.UserRole + 2) is True
        w.set_frame(2)
        assert w.frames_list.item(1).text().endswith("(hidden)")
        w.toggle_frame_visibility(1)
        assert w.frames_list.item(1).text() == "2"
        assert w.frames_list.item(1).data(Qt.ItemDataRole.UserRole + 2) is False
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_graphicsgale_style_docks_and_blank_frame_button():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDockWidget
    from stickrig.app import MainWindow
    from stickrig.model import get_entity, value_at
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.canvas.zoom_hud.parentWidget() is w.canvas_info
        assert w.canvas_info.objectName() == "canvas_info_bar"
        assert w.playback_toolbar.objectName() == "playback_toolbar"
        assert isinstance(w.playback_dock, QDockWidget)
        assert w.dockWidgetArea(w.playback_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
        assert w.dockWidgetArea(w.library_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
        assert w.playback_dock in w._docks
        assert w.playback_dock.toggleViewAction() in w.view_menu.actions()
        assert w.frame_plus.text() == "+"
        w.add_stickman((230, 280))
        ident = w.selected_id
        assert "X: 230.0" in w.selection_coords_label.text()
        w.update_canvas_coordinates((12.5, 30.25))
        assert "X: 12.5" in w.cursor_coords_label.text()
        w.frame_plus.click()
        assert w.project.duration == 2
        assert not value_at(w.project, get_entity(w.project, ident), "visible", 1)
        assert value_at(w.project, get_entity(w.project, ident), "visible", 0)
        w.duplicate_frame_at(0)
        assert w.project.duration == 3
        assert not value_at(w.project, get_entity(w.project, ident), "visible", 1)
        copies = [e for e in w.project.scene["entities"] if e.get("frame_index") == 1]
        assert len(copies) == 1
        assert copies[0]["id"] != ident
        w.project.validate()
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_playback_and_elements_can_swap_vertical_dock_order():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        w.show()
        app.processEvents()
        assert w.playback_dock.geometry().center().y() < w.library_dock.geometry().center().y()
        w.splitDockWidget(w.library_dock, w.playback_dock, Qt.Orientation.Vertical)
        app.processEvents()
        assert w.playback_dock.geometry().center().y() > w.library_dock.geometry().center().y()
        w.splitDockWidget(w.playback_dock, w.library_dock, Qt.Orientation.Vertical)
        app.processEvents()
        assert w.playback_dock.geometry().center().y() < w.library_dock.geometry().center().y()
    finally:
        w.capture_workspace(force=True)
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_frame_card_layout_and_bottom_actions():
    """Regression for screenshot feedback: actions below preview, grip on right."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QSizePolicy
    from stickrig.app import MainWindow
    from stickrig.frames_ui import (
        FrameDelegate, frame_action_rects, frame_drag_rect, frame_preview_rect,
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        assert window.frame_plus.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert window.frame_plus.width() >= window.frames_list.width() - 12

        item = window.frames_list.item(0)
        bounds = window.frames_list.visualItemRect(item).adjusted(3, 3, -3, -3)
        preview = frame_preview_rect(bounds)
        buttons = frame_action_rects(bounds)
        handle = frame_drag_rect(bounds)
        assert bounds.height() >= 145
        assert handle.center().x() > preview.right()
        assert handle.left() > max(button.right() for button in buttons.values())
        assert all(button.width() == button.height() for button in buttons.values())
        assert all(button.top() > preview.bottom() for button in buttons.values())
        assert len({(b.x(), b.y()) for b in buttons.values()}) == 3

        # Click via real event dispatch, not direct private helper calls.
        viewport = window.frames_list.viewport()
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                         pos=buttons["duplicate"].center())
        assert window.project.duration == 2
        app.processEvents()
        item = window.frames_list.item(0)
        bounds = window.frames_list.visualItemRect(item).adjusted(3, 3, -3, -3)
        buttons = frame_action_rects(bounds)
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                         pos=buttons["toggle"].center())
        assert 0 in window.project.scene["hidden_frames"]
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                         pos=buttons["delete"].center())
        assert window.project.duration == 1
        assert window.frames_list.item(0).sizeHint().height() >= 145
    finally:
        window.capture_workspace(force=True)
        window.saved_snapshot = window.project.to_dict()
        window.close()
