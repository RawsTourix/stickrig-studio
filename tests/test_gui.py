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
