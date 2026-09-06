from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt

from logdigitizer.domain.calibration import (
    DepthCalibrationPoint,
    DepthUnit,
)
from logdigitizer.gui.depth_calibration import DepthCalibrationHandle
from logdigitizer.gui.viewer import ImageViewer


def make_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "calibration.png"
    PIL.new("L", (500, 400), 180).save(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(900, 700)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_pan_enabled(False)
    return viewer


def depth_handles(viewer: ImageViewer) -> list[DepthCalibrationHandle]:
    return sorted(
        [
            item
            for item in viewer.scene().items()
            if isinstance(item, DepthCalibrationHandle)
        ],
        key=lambda item: item.index,
    )


def drag_handle(
    qtbot,
    viewer: ImageViewer,
    index: int,
    image_x: float,
    image_y: float,
) -> None:
    handle = depth_handles(viewer)[index]
    start = viewer.mapFromScene(handle.scenePos())
    target = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(image_x, image_y))
    )
    qtbot.mousePress(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=start,
    )
    qtbot.mouseMove(viewer.viewport(), pos=target)
    qtbot.mouseRelease(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=target,
    )


def test_overlay_label_is_left_and_mark_is_short_red(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_points(
        (
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(250.0, 20.0),
        ),
        DepthUnit.METERS,
    )

    handles = depth_handles(viewer)
    assert len(handles) == 2
    assert all(handle.parentItem() is None for handle in handles)

    first = handles[0]
    assert first.label_rect.right() < 0.0
    assert first.mark_length > 0.0
    assert first.mark_length <= first.MAX_MARK_LENGTH
    assert first.mark_color.red() >= 200
    assert first.mark_color.green() < 80
    assert first.mark_color.blue() < 80


def test_depth_capture_click_emits_image_coordinates_without_creating_point(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_enabled(True)

    target = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(220.0, 135.0))
    )
    with qtbot.waitSignal(viewer.depthPointRequested, timeout=1000) as blocker:
        qtbot.mouseClick(
            viewer.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )

    x, y = blocker.args
    assert x == pytest.approx(220.0, abs=1.5)
    assert y == pytest.approx(135.0, abs=1.5)
    assert depth_handles(viewer) == []


def test_handle_can_move_in_x_and_y_but_reports_canonical_image_coordinates(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_points(
        (
            DepthCalibrationPoint(100.0, 200.0),
            DepthCalibrationPoint(250.0, 220.0),
        ),
        DepthUnit.METERS,
    )

    with qtbot.waitSignal(
        viewer.depthCalibrationPointMoved,
        timeout=1000,
    ) as blocker:
        drag_handle(qtbot, viewer, 0, 310.0, 125.0)

    index, x, y = blocker.args
    assert index == 0
    assert x == pytest.approx(310.0, abs=2.0)
    assert y == pytest.approx(125.0, abs=2.0)


def test_drag_after_zoom_still_reports_image_pixel_y(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_points(
        (
            DepthCalibrationPoint(100.0, 200.0),
            DepthCalibrationPoint(250.0, 220.0),
        ),
        DepthUnit.METERS,
    )
    viewer.zoom_in()
    viewer.zoom_in()

    with qtbot.waitSignal(
        viewer.depthCalibrationPointMoved,
        timeout=1000,
    ) as blocker:
        drag_handle(qtbot, viewer, 1, 275.0, 280.0)

    _, x, y = blocker.args
    assert x == pytest.approx(275.0, abs=2.0)
    assert y == pytest.approx(280.0, abs=2.0)
