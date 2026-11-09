"""Regression: maximized window persistence, full-workspace grid, and deselect."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def make_window(monkeypatch, tmp_path):
    import stickrig.app as module

    # Isolate real-user application settings from headless tests.
    settings_file = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        module, "QSettings",
        lambda organization, application: QSettings(settings_file, QSettings.Format.IniFormat),
    )
    app = QApplication.instance() or QApplication([])
    window = module.MainWindow()
    window.show()
    app.processEvents()
    return app, window, settings_file


def close_unmodified(window):
    window.capture_workspace(force=True)
    window.saved_snapshot = window.project.to_dict()
    window.close()


def test_window_geometry_and_maximize_survive_clean_restart(monkeypatch, tmp_path):
    app, first, location = make_window(monkeypatch, tmp_path)
    try:
        first.resize(1000, 700)
        first.move(45, 55)
        first.showMaximized()
        app.processEvents()
        assert first.isMaximized()
    finally:
        close_unmodified(first)

    settings = QSettings(location, QSettings.Format.IniFormat)
    assert settings.value("window/maximized", False, type=bool) is True
    assert not settings.value("window/geometry").isEmpty()

    from stickrig.app import MainWindow
    second = MainWindow()
    try:
        second.showMaximized() if second._restore_maximized else second.show()
        app.processEvents()
        assert second.isMaximized()
    finally:
        close_unmodified(second)


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="Offscreen Qt clamps restored normal geometry unpredictably; test natively on Windows",
)
def test_normal_window_geometry_and_preference_are_persisted(monkeypatch, tmp_path):
    app, window, location = make_window(monkeypatch, tmp_path)
    try:
        window.resize(980, 710)
        app.processEvents()
        assert not window.isMaximized()
    finally:
        close_unmodified(window)
    settings = QSettings(location, QSettings.Format.IniFormat)
    assert settings.value("window/maximized", True, type=bool) is False
    next_window = __import__("stickrig.app", fromlist=["MainWindow"]).MainWindow()
    try:
        assert not next_window._restore_maximized
        screen = app.primaryScreen().availableGeometry()
        # Qt constrains restored geometry to the available screen; in CI the
        # offscreen screen is ~800x600. Verify exact size only when the
        # viewport is big enough to accommodate it without OS margins.
        assert 0 < next_window.width() <= 980
        assert 0 < next_window.height() <= 710
        if screen.width() >= 1020:
            assert abs(next_window.width() - 980) < 40
        if screen.height() >= 750:
            assert abs(next_window.height() - 710) < 40
    finally:
        close_unmodified(next_window)


def test_grid_extends_beyond_camera_and_repaints_on_spacing_change(monkeypatch, tmp_path):
    app, window, _ = make_window(monkeypatch, tmp_path)
    try:
        view = window.canvas
        view.frame_camera()
        app.processEvents()
        # Outside the camera paper x=0..960, still inside the viewport
        # thanks to the 40 scene-unit margin in frame_camera.
        coordinate = view.mapFromScene(QPointF(-20, 200))
        assert 0 <= coordinate.x() < view.viewport().width()
        assert 0 <= coordinate.y() < view.viewport().height()

        def snapshot():
            app.processEvents()
            return view.viewport().grab().toImage()

        window.grid_check.setChecked(True)
        window.grid_visible_check.setChecked(True)
        window.grid_step.setValue(10)
        initial = snapshot()
        outside = initial.pixelColor(coordinate).rgba()
        window.grid_visible_check.setChecked(False)
        invisible = snapshot()
        assert invisible.pixelColor(coordinate).rgba() != outside

        window.grid_visible_check.setChecked(True)
        window.grid_step.setValue(13)
        different = snapshot()
        assert bytes(initial.bits()) != bytes(different.bits())
        assert window.canvas.snap((19, 31)) == (13, 26)
        window.grid_step.setValue(6)
        third = snapshot()
        assert bytes(different.bits()) != bytes(third.bits())
        assert window.canvas.snap((19, 31)) == (18, 30)
    finally:
        close_unmodified(window)


def test_click_object_uses_raw_coordinate_but_editing_stays_grid_snapped(monkeypatch, tmp_path):
    app, window, _ = make_window(monkeypatch, tmp_path)
    try:
        view = window.canvas
        window.select_tool("select")
        # Not aligned to a grid node. Hit testing must use unsnapped pointer.
        window.add_shape("line", (123, 115), (183, 115))
        window.select_entity(None)
        view.frame_camera()
        app.processEvents()
        world = QPointF(153, 115)
        canvas_click = view.mapFromScene(world)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=canvas_click)
        assert window.selected_id == window.project.scene["entities"][0]["id"]

        empty = view.mapFromScene(QPointF(420, 400))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=empty)
        assert window.selected_id is None
        assert window.selected_joint is None
        assert window.canvas.snap((153, 115)) == (150, 120)
    finally:
        close_unmodified(window)
