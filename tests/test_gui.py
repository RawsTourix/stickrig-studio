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
        assert len(w.project.scene["entities"]) == 1
        assert w.project.duration == 2
    finally:
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
        w.saved_snapshot = w.project.to_dict()
        w.close()


def test_canvas_zoom_hover_frame_controls_and_playback():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    from stickrig.frame_ui import FrameDelegate
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert window.preview_toolbar.objectName() == "preview_toolbar"
        assert window.canvas.zoom_percent.text().endswith("%")
        window.canvas.zoom_by(1.2)
        assert 115 <= window.canvas.transform().m11() * 100 <= 125
        window.canvas.reset_view_zoom()
        assert round(window.canvas.transform().m11() * 100) == 100
        window.select_tool("stickman")
        window.canvas._hover_point = (200, 200)
        window.canvas._update_hover()
        assert window.canvas._hover_item is not None
        assert len(window.project.scene["entities"]) == 0
        window.canvas.clear_hover()
        assert window.canvas._hover_item is None
        window.add_stickman((120, 100))
        window.add_frame()
        window.add_frame()
        assert window.frames_list.count() == 3
        window.toggle_frame_visibility(1)
        assert window.project.scene["hidden_frames"] == [1]
        assert window.frames_list.item(1).data(Qt.ItemDataRole.UserRole + 3)
        window.set_frame(0)
        window.toggle_play()
        window.advance_frame()
        assert window.frame == 2
        window.toggle_play()
        window.move_frame_to(0, 2)
        assert window.frame == 2
        window.project.validate()
        window.duplicate_frame_at(0)
        assert window.project.duration == 4
        window.delete_frame_at(0)
        assert window.project.duration == 3
        delegate = FrameDelegate()
        rect = QRect(0, 0, 245, 112)
        assert delegate.hit_action(rect, FrameDelegate.grip_rect(rect).center()) == "drag"
        for name, area in delegate.action_rects(rect).items():
            assert delegate.hit_action(rect, area.center()) == name
    finally:
        window.stop_playback()
        window.capture_workspace(force=True)
        window.saved_snapshot = window.project.to_dict()
        window.close()
