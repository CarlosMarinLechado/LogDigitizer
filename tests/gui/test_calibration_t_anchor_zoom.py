from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from logdigitizer.domain.calibration import (
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibrationPoint,
)
from logdigitizer.gui.depth_calibration import DepthCalibrationHandle
from logdigitizer.gui.horizontal_calibration import HorizontalCalibrationHandle
from logdigitizer.gui.viewer import ImageViewer


def _make_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "calibration_t_anchor.png"
    PIL.new("L", (900, 700), 180).save(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(700, 520)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_pan_enabled(False)
    return viewer


def test_depth_t_intersection_is_exact_anchor_and_stays_after_zoom(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = _make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_points(
        (
            DepthCalibrationPoint(180.0, 100.0),
            DepthCalibrationPoint(420.0, 200.0),
        ),
        DepthUnit.METERS,
        preferred_x_by_depth={100.0: 300.0, 200.0: 300.0},
    )

    handle = viewer.depth_calibration.handles[0]
    assert isinstance(handle, DepthCalibrationHandle)
    assert handle.anchor_local_point == QPointF(0.0, 0.0)
    assert (
        handle.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    )

    expected_scene = viewer.image_to_scene(QPointF(300.0, 180.0))
    assert handle.scenePos().x() == pytest.approx(expected_scene.x())
    assert handle.scenePos().y() == pytest.approx(expected_scene.y())

    viewer.zoom_in()
    viewer.zoom_in()

    expected_view = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(300.0, 180.0))
    )
    actual_view = viewer.mapFromScene(handle.scenePos())
    assert actual_view.x() == pytest.approx(expected_view.x(), abs=1)
    assert actual_view.y() == pytest.approx(expected_view.y(), abs=1)

    assert handle.label_rect.right() < -handle.mark_length


def test_horizontal_t_faces_inward_by_pixel_x_not_physical_value(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = _make_viewer(qtbot, tmp_path)

    points = (
        HorizontalCalibrationPoint(180.0, 100.0),
        HorizontalCalibrationPoint(620.0, 0.0),
    )
    viewer.set_horizontal_calibration_points(
        points,
        "API",
        preferred_y_by_value={100.0: 220.0, 0.0: 220.0},
    )

    handles = viewer.horizontal_calibration.handles
    assert len(handles) == 2
    assert all(isinstance(h, HorizontalCalibrationHandle) for h in handles)
    assert handles[0].arm_direction == 1
    assert handles[1].arm_direction == -1
    assert all(h.anchor_local_point == QPointF(0.0, 0.0) for h in handles)


def test_horizontal_t_anchor_stays_on_calibrated_pixel_after_zoom(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = _make_viewer(qtbot, tmp_path)
    points = (
        HorizontalCalibrationPoint(180.0, 0.0),
        HorizontalCalibrationPoint(620.0, 100.0),
    )
    viewer.set_horizontal_calibration_points(
        points,
        "API",
        preferred_y_by_value={0.0: 220.0, 100.0: 220.0},
    )

    first = viewer.horizontal_calibration.handles[0]
    viewer.zoom_out()
    viewer.zoom_out()

    expected_view = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(180.0, 220.0))
    )
    actual_view = viewer.mapFromScene(first.scenePos())
    assert actual_view.x() == pytest.approx(expected_view.x(), abs=1)
    assert actual_view.y() == pytest.approx(expected_view.y(), abs=1)


def test_existing_curve_color_support_is_preserved(qtbot, tmp_path: Path) -> None:
    viewer = _make_viewer(qtbot, tmp_path)
    viewer.horizontal_calibration.set_mark_color("#FF8C00")
    viewer.set_horizontal_calibration_points(
        (
            HorizontalCalibrationPoint(180.0, 0.0),
            HorizontalCalibrationPoint(620.0, 100.0),
        ),
        "API",
    )
    assert all(
        handle.mark_color.name().upper() == "#FF8C00"
        for handle in viewer.horizontal_calibration.handles
    )
