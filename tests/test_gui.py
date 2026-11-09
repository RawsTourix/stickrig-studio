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
