from __future__ import annotations

from pathlib import Path
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsEllipseItem
from logdigitizer.gui.rectification import RectificationHandle
from logdigitizer.gui.viewer import ImageViewer


def make_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "source.png"
    PIL.new("L", (300, 220), 128).save(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(850, 650)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_rectification_enabled(True)
    viewer.set_rectification_points([
        (30.0, 30.0), (270.0, 35.0), (260.0, 190.0), (35.0, 195.0)
    ])
    return viewer


def handles(viewer: ImageViewer) -> list[RectificationHandle]:
    return sorted(
        [item for item in viewer.scene().items() if isinstance(item, RectificationHandle)],
        key=lambda item: item.index,
    )


def drag(qtbot, viewer: ImageViewer, index: int, x: float, y: float) -> None:
    handle = handles(viewer)[index]
    start = viewer.mapFromScene(handle.scenePos())
    target = viewer.mapFromScene(viewer.image_to_scene(QPointF(x, y)))
    qtbot.mousePress(viewer.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(viewer.viewport(), pos=target)
    qtbot.mouseRelease(viewer.viewport(), Qt.MouseButton.LeftButton, pos=target)


def test_handles_are_direct_scene_items(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    hs = handles(viewer)
    assert len(hs) == 4
    assert all(handle.parentItem() is None for handle in hs)
    # Keep backward-compatible visible grouped markers.
    group = viewer.overlays.layer("rectification")
    assert sum(isinstance(item, QGraphicsEllipseItem) for item in group.childItems()) == 4


def test_drag_after_four_points_changes_coordinate(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    drag(qtbot, viewer, 0, 60.0, 65.0)
    point = viewer.rectification_points[0]
    assert point.x == pytest.approx(60.0, abs=1.5)
    assert point.y == pytest.approx(65.0, abs=1.5)


def test_drag_after_zoom_keeps_image_coordinates(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.zoom_in()
    viewer.zoom_in()
    drag(qtbot, viewer, 2, 230.0, 170.0)
    point = viewer.rectification_points[2]
    assert point.x == pytest.approx(230.0, abs=1.5)
    assert point.y == pytest.approx(170.0, abs=1.5)


def test_clear_removes_direct_handles(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    assert len(handles(viewer)) == 4
    viewer.clear_rectification_points()
    assert handles(viewer) == []
