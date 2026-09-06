from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt

from logdigitizer.domain.calibration import HorizontalCalibrationPoint
from logdigitizer.gui.horizontal_calibration import HorizontalCalibrationHandle
from logdigitizer.gui.viewer import ImageViewer


def make_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "calibration_x.png"
    PIL.new("L", (500, 400), 180).save(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(900, 700)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_pan_enabled(False)
    return viewer


def x_handles(viewer: ImageViewer) -> list[HorizontalCalibrationHandle]:
    return sorted(
        [
            item
            for item in viewer.scene().items()
            if isinstance(item, HorizontalCalibrationHandle)
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
    handle = x_handles(viewer)[index]
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


def test_x_overlay_label_is_above_and_mark_is_short_blue(qtbot, tmp_path: Path):
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_horizontal_calibration_points(
        (
            HorizontalCalibrationPoint(100.0, 0.2),
            HorizontalCalibrationPoint(350.0, 200.0),
        ),
        "OHMM",
    )

    handles = x_handles(viewer)
    assert len(handles) == 2
    first = handles[0]
    assert first.label_rect.bottom() < 0.0
    assert first.mark_length > 0.0
    assert first.mark_length <= first.MAX_MARK_LENGTH
    assert first.mark_color.blue() >= 180
    assert first.mark_color.red() < 80


def test_x_capture_click_emits_canonical_image_coordinates(qtbot, tmp_path: Path):
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_horizontal_calibration_enabled(True)

    target = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(220.0, 135.0))
    )
    with qtbot.waitSignal(viewer.horizontalPointRequested, timeout=1000) as blocker:
        qtbot.mouseClick(
            viewer.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )

    x, y = blocker.args
    assert x == pytest.approx(220.0, abs=1.5)
    assert y == pytest.approx(135.0, abs=1.5)
    assert x_handles(viewer) == []


def test_x_handle_moves_in_both_directions_and_reports_image_coordinates(
    qtbot,
    tmp_path: Path,
):
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_horizontal_calibration_points(
        (
            HorizontalCalibrationPoint(100.0, 0.0),
            HorizontalCalibrationPoint(350.0, 100.0),
        ),
        "API",
    )

    with qtbot.waitSignal(
        viewer.horizontalCalibrationPointMoved,
        timeout=1000,
    ) as blocker:
        drag_handle(qtbot, viewer, 0, 125.0, 260.0)

    index, x, y = blocker.args
    assert index == 0
    assert x == pytest.approx(125.0, abs=2.0)
    assert y == pytest.approx(260.0, abs=2.0)


def test_x_drag_after_zoom_still_reports_pixel_x(qtbot, tmp_path: Path):
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_horizontal_calibration_points(
        (
            HorizontalCalibrationPoint(100.0, 0.0),
            HorizontalCalibrationPoint(350.0, 100.0),
        ),
        "API",
    )
    viewer.zoom_in()
    viewer.zoom_in()

    with qtbot.waitSignal(
        viewer.horizontalCalibrationPointMoved,
        timeout=1000,
    ) as blocker:
        drag_handle(qtbot, viewer, 1, 380.0, 210.0)

    _, x, y = blocker.args
    assert x == pytest.approx(380.0, abs=2.0)
    assert y == pytest.approx(210.0, abs=2.0)
