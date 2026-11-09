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
        set_key(w.project, hero, "pose.elbow_l", 1, 38)
        w.after_change()
        before = [value_at(w.project, get_entity(w.project, hero),
                           "pose.elbow_l", i) for i in range(3)]
        w.move_frame_to(1, 2)
        expected = before[:]
        expected.insert(2, expected.pop(1))
        assert [value_at(w.project, get_entity(w.project, hero),
                         "pose.elbow_l", i) for i in range(3)] == expected
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
