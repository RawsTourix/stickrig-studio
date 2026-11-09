"""Regression tests for long flipbooks, selected-frame focus and close joint picking."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stickrig.model import (
    Project, RIG_OFFSETS, get_entity, insert_frame, joints_at, make_stickman,
    segment_scale_for_drag, set_segment_scale,
)


_QT_APP = None  # Keep QApplication stable across all tests in this module.


@pytest.fixture(scope="module", autouse=True)
def release_qt_before_interpreter_shutdown():
    """Destroy Qt objects while PySide6 is still fully loaded, not at exit."""
    yield
    global _QT_APP
    if _QT_APP is not None:
        _QT_APP.closeAllWindows()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _QT_APP.processEvents()
        _QT_APP.quit()
        _QT_APP = None
    import gc
    gc.collect()


def prepare_window():
    from stickrig.app import MainWindow
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    app = _QT_APP
    window = MainWindow()
    window.resize(1250, 850)
    window.show()
    app.processEvents()
    return app, window


def close_window(window):
    window.frames_list._scroll_timer.stop()
    window.frames_list._gap_timer.stop()
    window.capture_workspace(force=True)
    window.saved_snapshot = window.project.to_dict()
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def drain_thumbnails(app, window, rounds=30):
    """Run the lazy one-icon-at-a-time queue without sleeping unnecessarily."""
    for _ in range(rounds):
        app.processEvents()
        window.render_one_thumbnail()
    app.processEvents()


def test_thumbnails_for_11_to_25_are_populated_after_scrolling():
    app, w = prepare_window()
    try:
        w.add_stickman((240, 270))
        for _ in range(23):
            insert_frame(w.project, w.project.duration - 1)
        w.frame = 0
        w.refresh()
        app.processEvents()
        strip = w.frames_list
        assert strip.count() == 24
        assert strip.verticalScrollBar().maximum() > 0

        strip.verticalScrollBar().setValue(strip.verticalScrollBar().maximum())
        app.processEvents()
        visible = list(w.visible_frame_rows())
        assert any(index >= 10 for index in visible)
        assert 23 in visible
        drain_thumbnails(app, w)
        assert not strip.item(23).icon().isNull()
        assert not strip.item(20).icon().isNull()

        strip.verticalScrollBar().setValue(0)
        app.processEvents()
        drain_thumbnails(app, w)
        assert not strip.item(0).icon().isNull()

        w.set_frame(14)
        app.processEvents()
        drain_thumbnails(app, w)
        assert strip.currentRow() == 14
        assert strip.visualItemRect(strip.item(14)).intersects(strip.viewport().rect())
        assert not strip.item(14).icon().isNull()
    finally:
        close_window(w)


def test_duplicating_last_of_24_frames_keeps_selection_and_scroll_together():
    app, w = prepare_window()
    try:
        w.add_stickman((240, 270))
        for _ in range(23):
            insert_frame(w.project, w.project.duration - 1)
        w.refresh()
        w.set_frame(23)
        app.processEvents()
        strip = w.frames_list
        assert strip.currentRow() == 23
        assert strip.visualItemRect(strip.item(23)).intersects(strip.viewport().rect())

        w.duplicate_frame_at(23)
        app.processEvents()
        drain_thumbnails(app, w)
        assert w.frame == 24
        assert strip.currentRow() == 24
        last_rect = strip.visualItemRect(strip.item(24))
        assert last_rect.top() >= strip.viewport().rect().top()
        assert last_rect.bottom() <= strip.viewport().rect().bottom()
        assert not strip.item(24).icon().isNull()

        w.prev_frame()
        app.processEvents()
        assert strip.currentRow() == 23
        assert strip.visualItemRect(strip.item(23)).intersects(strip.viewport().rect())
        w.next_frame()
        app.processEvents()
        assert strip.currentRow() == 24
    finally:
        close_window(w)


def test_pick_closest_of_two_ankles_at_70_percent_zoom(monkeypatch):
    import stickrig.app as editor_module

    app, w = prepare_window()
    try:
        w.add_stickman((200, 200))
        hero_id = w.selected_id
        hero = get_entity(w.project, hero_id)
        w.select_tool("select")
        w.canvas.resetTransform()
        w.canvas.scale(0.7, 0.7)

        points = {joint: (1000 + i * 100.0, 1000.0)
                  for i, joint in enumerate(RIG_OFFSETS)}
        points["ankle_l"] = (100, 100)
        points["ankle_r"] = (110, 100)
        monkeypatch.setattr(editor_module, "joints_at",
                            lambda project, entity, frame: (points, {}))
        assert w.canvas._pick((100, 100)) == (hero_id, "ankle_l")
        assert w.canvas._pick((110, 100)) == (hero_id, "ankle_r")

        # True overlap is a legitimate ambiguity: second click can cycle.
        w.select_entity(hero_id, "ankle_l")
        assert w.canvas._pick((105, 100)) == (hero_id, "ankle_r")
    finally:
        close_window(w)


def test_shift_resize_is_grid_independent_and_rest_length_can_be_reset():
    p = Project()
    hero = make_stickman(220, 220, frame_index=0, rig_unit=10)
    p.scene["entities"].append(hero)
    points, _ = joints_at(p, hero, 0)
    parent = points["hip_l"]
    target = points["knee_l"]
    delta = (target[0] - parent[0], target[1] - parent[1])
    shortened = (parent[0] + delta[0] * 0.6, parent[1] + delta[1] * 0.6)
    assert segment_scale_for_drag(hero, "knee_l", parent, shortened) == pytest.approx(0.6)
    near_rest = (parent[0] + delta[0] * 0.98,
                 parent[1] + delta[1] * 0.98)
    assert segment_scale_for_drag(hero, "knee_l", parent, near_rest) == 1.0

    app, window = prepare_window()
    try:
        window.add_stickman((250, 260))
        ident = window.selected_id
        window.select_tool("select")
        window.select_entity(ident, "knee_l")
        window.segment_length.setValue(60)
        assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 0.6
        assert not hasattr(window, "reset_length_button")
        knee = joints_at(window.project, get_entity(window.project, ident),
                         window.frame)[0]["knee_l"]
        spot = window.canvas.mapFromScene(QPointF(*knee))
        QTest.mouseDClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=spot)
        app.processEvents()
        assert window.selected_id == ident
        assert window.selected_joint == "knee_l"
        assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 1.0
        assert window.segment_length.value() == 100
        assert window.canvas.drag_id is None
        window.undo()
        assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 0.6
        window.redo()
        assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 1.0
    finally:
        close_window(window)


def test_double_click_empty_area_or_line_does_not_reset_other_limbs():
    app, window = prepare_window()
    try:
        window.add_stickman((250, 260))
        ident = window.selected_id
        window.select_tool("select")
        window.select_entity(ident, "knee_l")
        window.segment_length.setValue(45)
        assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 0.45
        # Body click is not the joint handle. Clicking the backdrop is not a
        # geometry-edit command, and neither should change the shortened limb.
        body = joints_at(window.project, get_entity(window.project, ident),
                         window.frame)[0]["spine"]
        for location in (QPointF(520, 420), QPointF(*body)):
            point = window.canvas.mapFromScene(location)
            QTest.mouseDClick(window.canvas.viewport(),
                              Qt.MouseButton.LeftButton, pos=point)
            app.processEvents()
            assert get_entity(window.project, ident)["segment_scales"]["knee_l"] == 0.45
    finally:
        close_window(window)
