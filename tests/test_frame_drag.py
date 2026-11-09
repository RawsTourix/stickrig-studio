"""Headless GUI regression tests for animated Frames drag affordances.

Native QDrag must still be evaluated manually on Windows precision touchpads.
Tests exercise the same paint/target/scroll/settle state machines directly.
"""
import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)


def _window_with_frames(count=7):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from stickrig.app import MainWindow
    from stickrig.model import insert_frame

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1120, 800)
    for _ in range(count - 1):
        insert_frame(window.project, window.project.duration - 1)
    window.refresh()
    window.show()
    app.processEvents()
    return window


def _close(window):
    window.frames_list._scroll_timer.stop()
    window.frames_list._gap_animation.stop()
    window.frames_list._cancel_settle()
    window.frames_list._drag_active = False
    window.capture_workspace(force=True)
    window.saved_snapshot = window.project.to_dict()
    window.close()


def test_drag_ghost_and_animated_gap_do_not_mutate_project():
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from stickrig.frames_ui import CARD_HEIGHT
    w = _window_with_frames()
    try:
        strip = w.frames_list
        ghost = strip._drag_pixmap(0)
        assert not ghost.isNull()
        assert ghost.height() >= CARD_HEIGHT
        state = w.project.to_dict()
        strip._drag_active = True
        strip._active_source = 0
        strip._drop_target = 0
        target = strip.visualItemRect(strip.item(1))
        # Inserting after frame 2, moving frame 1 to final index 2.
        strip._update_target(QPoint(target.center().x(), target.center().y() + 7))
        assert strip._drop_slot == 2
        assert strip._drop_target == 1
        assert strip._insertion_line_y() is not None
        strip._gap_animation.setCurrentTime(140)
        assert strip._gap_offsets[1] < 0
        assert abs(strip._gap_offsets[1]) <= CARD_HEIGHT + strip.spacing()
        assert w.project.to_dict() == state
        strip._finish_drag()  # cancellation, no drop
        assert strip._drop_slot is None
        assert strip._gap_offsets == {}
        assert w.project.to_dict() == state
        QApplication.processEvents()
    finally:
        _close(w)


def test_animated_settle_returns_to_normal_card():
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    w = _window_with_frames(4)
    try:
        strip = w.frames_list
        pix = strip._drag_pixmap(0)
        assert not pix.isNull()
        strip._pending_settle = (QPoint(20, 20), 1, pix)
        strip._animate_settle()
        assert strip._settle_row == 1
        assert strip._settle_overlay is not None
        assert strip._settle_animation is not None
        QTest.qWait(230)
        assert strip._settle_row == -1
        assert strip._settle_overlay is None
        assert strip._settle_progress == 1.0
    finally:
        _close(w)


def test_auto_scroll_advances_while_pointer_stays_near_edge():
    from PySide6.QtCore import QPoint
    w = _window_with_frames(110)
    try:
        strip = w.frames_list
        scroll = strip.verticalScrollBar()
        assert scroll.maximum() > 0
        scroll.setValue(0)
        strip._drag_active = True
        strip._active_source = 0
        strip._drop_target = 0
        stationary_pointer = QPoint(24, strip.viewport().height() - 2)
        strip._auto_scroll(stationary_pointer)
        assert scroll.value() > 0
        assert strip._drop_slot is not None
        assert strip._drop_target is not None
        strip._finish_drag()
        assert not strip._scroll_timer.isActive()
    finally:
        _close(w)


def test_large_jump_only_animates_visible_rows():
    w = _window_with_frames(1000)
    try:
        strip = w.frames_list
        strip._drag_active = True
        strip._active_source = 0
        targets = strip._offset_targets(999)
        assert 1 <= len(targets) < 30
        assert all(value < 0 for value in targets.values())
        strip._finish_drag()
    finally:
        _close(w)
