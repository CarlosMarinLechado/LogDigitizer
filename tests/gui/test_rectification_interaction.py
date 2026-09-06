from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from logdigitizer.gui.viewer import ImageViewer


def create_test_image(
    path: Path,
    size: tuple[int, int] = (240, 180),
) -> None:
    image = PIL.new("L", size, 128)
    image.save(path)


def click_image_point(
    qtbot,
    viewer: ImageViewer,
    x: float,
    y: float,
) -> None:
    scene_point = viewer.image_to_scene(QPointF(x, y))
    viewport_point = viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


@pytest.fixture
def loaded_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "rectification.png"
    create_test_image(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(800, 600)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    return viewer


def test_four_clicks_store_four_image_points_and_ignore_fifth(
    qtbot,
    loaded_viewer: ImageViewer,
) -> None:
    viewer = loaded_viewer
    viewer.set_rectification_enabled(True)

    expected = [
        (20.0, 20.0),
        (210.0, 25.0),
        (205.0, 155.0),
        (25.0, 160.0),
    ]
    for x, y in expected:
        click_image_point(qtbot, viewer, x, y)

    assert len(viewer.rectification_points) == 4
    for actual, (x, y) in zip(viewer.rectification_points, expected):
        assert actual.x == pytest.approx(x, abs=1.0)
        assert actual.y == pytest.approx(y, abs=1.0)

    click_image_point(qtbot, viewer, 100.0, 100.0)
    assert len(viewer.rectification_points) == 4


def test_four_points_render_markers_labels_and_closed_quad(
    qtbot,
    loaded_viewer: ImageViewer,
) -> None:
    viewer = loaded_viewer
    viewer.set_rectification_enabled(True)

    for point in (
        (20.0, 20.0),
        (210.0, 20.0),
        (210.0, 160.0),
        (20.0, 160.0),
    ):
        click_image_point(qtbot, viewer, *point)

    children = viewer.overlays.layer("rectification").childItems()

    assert sum(isinstance(item, QGraphicsEllipseItem) for item in children) == 4
    assert sum(isinstance(item, QGraphicsSimpleTextItem) for item in children) == 4
    assert sum(isinstance(item, QGraphicsLineItem) for item in children) == 4


def test_clear_removes_points_and_overlay_items(
    qtbot,
    loaded_viewer: ImageViewer,
) -> None:
    viewer = loaded_viewer
    viewer.set_rectification_enabled(True)

    click_image_point(qtbot, viewer, 20.0, 20.0)
    click_image_point(qtbot, viewer, 210.0, 20.0)

    assert len(viewer.rectification_points) == 2
    assert viewer.overlays.layer("rectification").childItems()

    viewer.clear_rectification_points()

    assert viewer.rectification_points == ()
    assert viewer.overlays.layer("rectification").childItems() == []


def test_selected_coordinates_are_image_coordinates_at_different_zoom_levels(
    qtbot,
    loaded_viewer: ImageViewer,
) -> None:
    viewer = loaded_viewer
    viewer.set_rectification_enabled(True)

    target = (120.0, 90.0)

    click_image_point(qtbot, viewer, *target)
    first = viewer.rectification_points[0]

    viewer.clear_rectification_points()
    viewer.zoom_in()
    viewer.zoom_in()

    click_image_point(qtbot, viewer, *target)
    second = viewer.rectification_points[0]

    assert first.x == pytest.approx(target[0], abs=1.0)
    assert first.y == pytest.approx(target[1], abs=1.0)
    assert second.x == pytest.approx(target[0], abs=1.0)
    assert second.y == pytest.approx(target[1], abs=1.0)
    assert second.x == pytest.approx(first.x, abs=1.0)
    assert second.y == pytest.approx(first.y, abs=1.0)


def test_disabling_rectification_restores_normal_pan_mode(
    qtbot,
    loaded_viewer: ImageViewer,
) -> None:
    viewer = loaded_viewer

    assert viewer.dragMode() == QGraphicsView.DragMode.ScrollHandDrag

    viewer.set_rectification_enabled(True)
    assert viewer.rectification_enabled
    assert viewer.dragMode() == QGraphicsView.DragMode.NoDrag

    click_image_point(qtbot, viewer, 50.0, 50.0)
    assert len(viewer.rectification_points) == 1

    viewer.set_rectification_enabled(False)
    assert not viewer.rectification_enabled
    assert viewer.dragMode() == QGraphicsView.DragMode.ScrollHandDrag

    click_image_point(qtbot, viewer, 80.0, 80.0)
    assert len(viewer.rectification_points) == 1


def test_loading_another_image_clears_rectification_selection(
    qtbot,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    create_test_image(first_path)
    create_test_image(second_path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(800, 600)
    viewer.show()

    viewer.load_image(first_path)
    viewer.actual_size()
    viewer.set_rectification_enabled(True)
    click_image_point(qtbot, viewer, 50.0, 50.0)
    assert len(viewer.rectification_points) == 1

    viewer.load_image(second_path)

    assert viewer.rectification_points == ()
    assert viewer.overlays.layer("rectification").childItems() == []
