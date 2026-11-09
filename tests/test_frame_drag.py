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
    window.frames_list._gap_timer.stop()
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
        strip._update_target(QPoint(target.center().x(), target.center().y() + 15))
        assert strip._drop_slot == 2
        assert strip._drop_target == 1
        assert strip._insertion_line_y() is not None
        strip._step_gap(dt_ms=48)
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


def test_retargeting_gap_animation_never_restarts_or_snaps():
    """Rapid switching of destination must not reset the easing to zero."""
    w = _window_with_frames(8)
    try:
        strip = w.frames_list
        strip._drag_active = True
        strip._active_source = 0
        strip._drop_target = 0
        strip._animate_gap(2)
        assert strip._gap_timer.isActive()
        strip._step_gap(dt_ms=16)
        before = strip._gap_offsets[1]
        strip._animate_gap(4)
        assert strip._gap_timer.isActive()
        strip._step_gap(dt_ms=16)
        after = strip._gap_offsets[1]
        # Same direction: continue from old displacement, not from zero.
        assert after < before < 0
        strip._animate_gap(0)
        strip._step_gap(dt_ms=16)
        returning = strip._gap_offsets[1]
        assert before > returning > after
        for _ in range(24):
            strip._step_gap(dt_ms=16)
        assert strip._gap_offsets == {}
        assert not strip._gap_timer.isActive()
    finally:
        _close(w)


def test_slot_hysteresis_keeps_cursor_noise_from_flickering():
    from PySide6.QtCore import QPoint
    w = _window_with_frames(5)
    try:
        strip = w.frames_list
        midpoint = strip.visualItemRect(strip.item(1)).center()
        strip._drop_slot = 1
        assert strip._stable_slot(2, QPoint(midpoint.x(), midpoint.y() + 3)) == 1
        assert strip._stable_slot(2, QPoint(midpoint.x(), midpoint.y() + 15)) == 2
        strip._drop_slot = 2
        assert strip._stable_slot(1, QPoint(midpoint.x(), midpoint.y() - 3)) == 2
        assert strip._stable_slot(1, QPoint(midpoint.x(), midpoint.y() - 15)) == 1
        # Large, deliberate pointer jumps are not artificially delayed.
        assert strip._stable_slot(4, QPoint(midpoint.x(), midpoint.y() + 20)) == 4
    finally:
        _close(w)


def test_smooth_edge_scroll_and_no_extra_scroll_outside_panel():
    from PySide6.QtCore import QPoint
    w = _window_with_frames(120)
    try:
        strip = w.frames_list
        bar = strip.verticalScrollBar()
        strip._drag_active = True
        strip._active_source = 0
        strip._drop_target = 0
        edge = QPoint(24, strip.viewport().height() - 2)
        history = []
        for _ in range(6):
            strip._auto_scroll(edge, dt_ms=16)
            history.append(bar.value())
        assert history == sorted(history)
        assert history[-1] > history[0] >= 0
        outside = QPoint(-100, edge.y())
        strip._auto_scroll(outside, dt_ms=16)
        stop = bar.value()
        strip._auto_scroll(outside, dt_ms=16)
        assert bar.value() == stop
    finally:
        _close(w)


def test_deferred_drop_mutates_only_after_native_drag_completes():
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from stickrig.model import make_stickman, set_key, get_entity, value_at
    w = _window_with_frames(4)
    try:
        strip = w.frames_list
        hero = make_stickman(100, 200, entity_id="hero")
        w.project.scene["entities"].append(hero)
        set_key(w.project, "hero", "pose.elbow_l", 0, 10)
        set_key(w.project, "hero", "pose.elbow_l", 1, 20)
        set_key(w.project, "hero", "pose.elbow_l", 2, 30)
        w.after_change()
        before = [value_at(w.project, get_entity(w.project, "hero"),
                           "pose.elbow_l", f) for f in range(4)]
        pix = strip._drag_pixmap(0)
        strip._pending_drop = (0, 2, QPoint(10, 10), pix)
        # The old order is untouched while the native drop callback is active.
        assert value_at(w.project, get_entity(w.project, "hero"),
                        "pose.elbow_l", 0) == before[0]
        strip._commit_drop()
        expected = before[:]
        expected.insert(2, expected.pop(0))
        actual = [value_at(w.project, get_entity(w.project, "hero"),
                           "pose.elbow_l", f) for f in range(4)]
        assert actual == expected
        assert strip._pending_drop is None
        strip._commit_drop()  # duplicate finish callback must be inert
        assert [value_at(w.project, get_entity(w.project, "hero"),
                         "pose.elbow_l", f) for f in range(4)] == expected
        QTest.qWait(225)
        w.undo()
        assert [value_at(w.project, get_entity(w.project, "hero"),
                         "pose.elbow_l", f) for f in range(4)] == before
    finally:
        _close(w)


def test_insertion_indicator_eases_to_new_slot_instead_of_teleporting():
    from PySide6.QtCore import QPoint
    w = _window_with_frames(6)
    try:
        strip = w.frames_list
        strip._drag_active = True
        strip._active_source = 0
        strip._drop_target = 0
        strip._drop_slot = 1
        old_y = float(strip._slot_line_y())
        strip._line_y = old_y
        strip._line_target = old_y
        next_row = strip.visualItemRect(strip.item(1))
        strip._update_target(QPoint(next_row.center().x(), next_row.center().y() + 16))
        target_y = float(strip._slot_line_y())
        assert strip._drop_slot == 2
        assert target_y > old_y
        # Marker follows a continuous curve rather than jumping to target.
        assert strip._insertion_line_y() == old_y
        strip._step_gap(dt_ms=16)
        midway = strip._insertion_line_y()
        assert old_y < midway < target_y
        for _ in range(28):
            strip._step_gap(dt_ms=16)
        assert abs(strip._insertion_line_y() - target_y) < 1
        assert not strip._gap_timer.isActive()
    finally:
        _close(w)
