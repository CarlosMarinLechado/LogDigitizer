from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from logdigitizer.domain.calibration import HorizontalCalibrationPoint
from logdigitizer.gui.horizontal_calibration import HorizontalCalibrationSelection
from logdigitizer.gui.overlays import OverlayManager


def test_horizontal_calibration_mark_uses_requested_curve_color(qtbot) -> None:
    scene = QGraphicsScene()
    overlays = OverlayManager(scene)

    selection = HorizontalCalibrationSelection(
        overlays,
        image_to_scene=lambda point: QPointF(point),
        scene_to_image=lambda point: QPointF(point),
    )

    selection.set_mark_color("#C64545")
    selection.set_points(
        (HorizontalCalibrationPoint(100.0, 10.0),),
        "API",
        default_y=50.0,
    )

    assert len(selection.handles) == 1
    assert selection.handles[0].mark_color == QColor("#C64545")


def test_horizontal_calibration_default_mark_color_is_preserved(qtbot) -> None:
    scene = QGraphicsScene()
    overlays = OverlayManager(scene)

    selection = HorizontalCalibrationSelection(
        overlays,
        image_to_scene=lambda point: QPointF(point),
        scene_to_image=lambda point: QPointF(point),
    )
    selection.set_points(
        (HorizontalCalibrationPoint(100.0, 10.0),),
        "API",
        default_y=50.0,
    )

    assert selection.handles[0].mark_color == QColor(0, 90, 220)
