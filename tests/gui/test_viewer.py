from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPoint, QPointF, Qt

from logdigitizer.gui.viewer import ImageViewer


def create_test_image(path: Path, size: tuple[int, int] = (120, 80)) -> None:
    image = PIL.new("L", size, 128)
    image.save(path)


def test_viewer_load_fit_zoom_coordinates_and_clear(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "viewer.png"
    create_test_image(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(600, 400)
    viewer.show()

    viewer.load_image(path)
    assert viewer.has_image
    assert viewer.image_size == (120, 80)
    assert not viewer._pixmap_item.pixmap().isNull()

    viewer.fit_to_window()
    fit_zoom = viewer.zoom_factor
    assert fit_zoom > 0

    viewer.actual_size()
    assert viewer.zoom_factor == pytest.approx(1.0)
    viewer.zoom_in()
    assert viewer.zoom_factor > 1.0
    viewer.zoom_out()
    assert viewer.zoom_factor == pytest.approx(1.0)

    scene_point = viewer.image_to_scene(QPointF(50.25, 30.75))
    image_point = viewer.scene_to_image(scene_point)
    assert image_point is not None
    assert image_point.x() == pytest.approx(50.25)
    assert image_point.y() == pytest.approx(30.75)

    viewer.clear_image()
    assert not viewer.has_image
    assert viewer.image_path is None


def test_middle_button_drag_pans_during_manual_digitization(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "large_viewer.png"
    create_test_image(path, size=(1400, 1100))

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(360, 280)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_manual_digitization_enabled(True)
    viewer.centerOn(700.0, 550.0)

    hbar = viewer.horizontalScrollBar()
    vbar = viewer.verticalScrollBar()
    before = (hbar.value(), vbar.value())
    start = QPoint(viewer.viewport().width() // 2, viewer.viewport().height() // 2)
    end = start - QPoint(45, 35)

    qtbot.mousePress(
        viewer.viewport(),
        Qt.MouseButton.MiddleButton,
        pos=start,
    )
    assert viewer.viewport().cursor().shape() == Qt.CursorShape.ClosedHandCursor

    qtbot.mouseMove(viewer.viewport(), pos=end)
    after = (hbar.value(), vbar.value())

    assert after[0] > before[0]
    assert after[1] > before[1]
    assert viewer.manual_digitization_points == ()

    qtbot.mouseRelease(
        viewer.viewport(),
        Qt.MouseButton.MiddleButton,
        pos=end,
    )
    assert viewer.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
