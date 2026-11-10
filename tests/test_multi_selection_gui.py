"""Qt smoke tests of the v0.6 selection/clipboard interaction contract."""
import os

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stickrig.app import MainWindow
from stickrig.model import get_entity
from stickrig.render import visible_entities


QT_APP = None


def setup():
    global QT_APP
    if QT_APP is None:
        QT_APP = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1350, 850)
    window.show()
    QT_APP.processEvents()
    window.canvas.frame_camera()
    QT_APP.processEvents()
    return window


def close(window):
    window._layout_monitor.stop()
    window.frames_list._scroll_timer.stop()
    window.frames_list._gap_timer.stop()
    window.capture_workspace(force=True)
    window.saved_snapshot = window.project.to_dict()
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def move_with_button(view, pixel, modifier=Qt.KeyboardModifier.NoModifier):
    viewport = view.viewport()
    absolute = QPointF(viewport.mapToGlobal(pixel))
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pixel), absolute,
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                        modifier)
    QCoreApplication.sendEvent(viewport, event)
    QT_APP.processEvents()


def drag_world(view, start, end, modifier=Qt.KeyboardModifier.NoModifier):
    a = view.mapFromScene(QPointF(*start))
    b = view.mapFromScene(QPointF(*end))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, modifier, a)
    move_with_button(view, b, modifier)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, modifier, b)
    QT_APP.processEvents()


def select_two_shapes(window):
    window.add_shape("line", (100, 120), (160, 120))
    first = window.selected_id
    window.add_shape("rectangle", (210, 160), (270, 210))
    second = window.selected_id
    window.add_stickman((550, 350))
    third = window.selected_id
    window.select_tool("select")
    return first, second, third


def test_drag_empty_area_marquee_selects_only_objects_inside_rectangle():
    w = setup()
    try:
        first, second, third = select_two_shapes(w)
        drag_world(w.canvas, (70, 75), (300, 240))
        assert w.selected_ids == {first, second}
        assert w.selected_id is None
        assert w.selected_joint is None
        assert len(w.canvas.graphics.items()) > 0

        # Plain click on the empty backdrop deselects all.
        p = w.canvas.mapFromScene(QPointF(750, 400))
        QTest.mouseClick(w.canvas.viewport(), Qt.MouseButton.LeftButton, pos=p)
        assert not w.selected_ids
        assert w.selected_id is None
    finally:
        close(w)


def test_multi_selection_group_move_and_single_undo():
    w = setup()
    try:
        first, second, third = select_two_shapes(w)
        drag_world(w.canvas, (70, 75), (300, 240))
        before = (list(get_entity(w.project, first)["a"]),
                  list(get_entity(w.project, second)["a"]),
                  list(get_entity(w.project, third)["position"]))
        previous_undo = len(w.history.undo_stack)
        drag_world(w.canvas, (130, 120), (180, 160))
        assert w.selected_ids == {first, second}
        now = (get_entity(w.project, first)["a"],
               get_entity(w.project, second)["a"],
               get_entity(w.project, third)["position"])
        assert now[0] == [before[0][0] + 50, before[0][1] + 40]
        assert now[1] == [before[1][0] + 50, before[1][1] + 40]
        assert now[2] == before[2]  # unselected actor stays put
        assert len(w.history.undo_stack) == previous_undo + 1
        w.undo()
        assert get_entity(w.project, first)["a"] == before[0]
        assert get_entity(w.project, second)["a"] == before[1]
        w.redo()
        assert get_entity(w.project, first)["a"] == now[0]
    finally:
        close(w)


def test_copy_paste_ghost_to_blank_frame_without_source_changes():
    w = setup()
    try:
        first, second, _ = select_two_shapes(w)
        w.select_entities({first, second})
        w.copy_selected()
        assert len(w._copy_buffer["entities"]) == 2
        w.frame_plus.click()
        assert w.frame == 1
        assert not list(visible_entities(w.project, 1))
        w.start_paste()
        assert w.canvas._paste_clipboard is not None
        assert len(w.project.scene["entities"]) == 3
        # The cursor is inside the viewport now; moving previews the anchor.
        pos = w.canvas.mapFromScene(QPointF(360, 220))
        QTest.mouseMove(w.canvas.viewport(), pos)
        QT_APP.processEvents()
        assert w.canvas._paste_top_left == (360, 220)
        assert not list(visible_entities(w.project, 1))
        QTest.mouseClick(w.canvas.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        QT_APP.processEvents()
        assert w.canvas._paste_clipboard is None
        pasted = list(visible_entities(w.project, 1))
        assert len(pasted) == 2
        assert {x["id"] for x in pasted}.isdisjoint({first, second})
        assert {x["id"] for x in visible_entities(w.project, 0)} == {first, second,
                                                                      w.project.scene["entities"][2]["id"]}
        assert w.selected_ids == {x["id"] for x in pasted}
        w.undo()
        assert not list(visible_entities(w.project, 1))
        w.redo()
        assert len(list(visible_entities(w.project, 1))) == 2
    finally:
        close(w)


def test_escape_cancels_paste_and_paste_in_place_keeps_coordinates():
    w = setup()
    try:
        first, second, _ = select_two_shapes(w)
        w.select_entities({first, second})
        w.copy_selected()
        w.frame_plus.click()
        before = w.project.to_dict()
        w.start_paste()
        assert w.canvas._paste_clipboard is not None
        w.escape_action.trigger()
        assert w.canvas._paste_clipboard is None
        assert w.project.to_dict() == before
        w.paste_in_place()
        assert len(list(visible_entities(w.project, 1))) == 2
        source = [get_entity(w.project, ident) for ident in (first, second)]
        dest = list(visible_entities(w.project, 1))
        assert dest[0]["a"] == source[0]["a"]
        assert dest[1]["a"] == source[1]["a"]
    finally:
        close(w)


def test_ctrl_toggle_and_shift_add_do_not_start_unintended_drag():
    w = setup()
    try:
        first, second, _ = select_two_shapes(w)
        w.select_entity(first)
        q = w.canvas.mapFromScene(QPointF(240, 180))
        before = w.project.to_dict()
        QTest.mouseClick(w.canvas.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, q)
        assert w.selected_ids == {first, second}
        assert w.project.to_dict() == before
        QTest.mouseClick(w.canvas.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, q)
        assert w.selected_ids == {first}
        assert w.project.to_dict() == before
    finally:
        close(w)


def test_select_all_and_escape_abort_marquee_without_losing_previous_selection():
    w = setup()
    try:
        first, second, third = select_two_shapes(w)
        w.select_all()
        assert w.selected_ids == {first, second, third}
        w.select_entity(first)
        start = w.canvas.mapFromScene(QPointF(80, 80))
        end = w.canvas.mapFromScene(QPointF(300, 240))
        QTest.mousePress(w.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_button(w.canvas, end)
        assert w.canvas.marquee_end is not None
        w.escape_action.trigger()
        assert w.selected_ids == {first}
        assert w.canvas.marquee_start is None
        QTest.mouseRelease(w.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert w.selected_ids == {first}
    finally:
        close(w)
