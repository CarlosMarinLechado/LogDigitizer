from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsView

from logdigitizer.gui.manual_digitization import (
    ManualTraceNodeItem,
    ManualTracePathItem,
)
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.processing.manual_digitization import ManualTraceDirection


def make_viewer(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "manual_trace.png"
    PIL.new("L", (500, 400), 190).save(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(900, 700)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_pan_enabled(True)
    return viewer


def click_image_point(
    qtbot,
    viewer: ImageViewer,
    x: float,
    y: float,
) -> None:
    target = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(x, y))
    )
    qtbot.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=target,
    )


def node_items(viewer: ImageViewer) -> list[ManualTraceNodeItem]:
    return sorted(
        [
            item
            for item in viewer.scene().items()
            if isinstance(item, ManualTraceNodeItem)
        ],
        key=lambda item: item.index,
    )


def path_items(viewer: ImageViewer) -> list[ManualTracePathItem]:
    return [
        item
        for item in viewer.scene().items()
        if isinstance(item, ManualTracePathItem)
    ]


def test_manual_mode_is_mutually_exclusive_and_disables_hand_pan(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_depth_calibration_enabled(True)
    assert viewer.depth_calibration_enabled is True

    viewer.set_manual_digitization_enabled(True)

    assert viewer.manual_digitization_enabled is True
    assert viewer.depth_calibration_enabled is False
    assert viewer.horizontal_calibration_enabled is False
    assert viewer.rectification_enabled is False
    assert viewer.dragMode() == QGraphicsView.DragMode.NoDrag


def test_first_click_creates_visible_node_but_no_curve(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)

    click_image_point(qtbot, viewer, 120.0, 80.0)

    points = viewer.manual_digitization_points
    assert len(points) == 1
    assert points[0].x_px == pytest.approx(120.0, abs=1.5)
    assert points[0].y_px == pytest.approx(80.0, abs=1.5)
    assert len(node_items(viewer)) == 1
    assert path_items(viewer) == []


def test_second_and_third_click_render_pchip_curve_and_nodes(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)

    click_image_point(qtbot, viewer, 100.0, 70.0)
    click_image_point(qtbot, viewer, 180.0, 170.0)

    paths_after_two = path_items(viewer)
    assert len(node_items(viewer)) == 2
    assert len(paths_after_two) == 1
    assert paths_after_two[0].sample_count > 2

    click_image_point(qtbot, viewer, 130.0, 280.0)

    paths_after_three = path_items(viewer)
    assert len(node_items(viewer)) == 3
    assert len(paths_after_three) == 1
    assert paths_after_three[0].sample_count > paths_after_two[0].sample_count
    assert viewer.manual_digitization.direction is ManualTraceDirection.DOWN


def test_manual_creation_can_run_upward(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)

    click_image_point(qtbot, viewer, 250.0, 300.0)
    click_image_point(qtbot, viewer, 210.0, 220.0)
    click_image_point(qtbot, viewer, 260.0, 120.0)

    assert len(viewer.manual_digitization_points) == 3
    assert viewer.manual_digitization.direction is ManualTraceDirection.UP
    assert len(path_items(viewer)) == 1


def test_reversing_vertical_direction_is_rejected_without_changing_overlay(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)
    click_image_point(qtbot, viewer, 100.0, 100.0)
    click_image_point(qtbot, viewer, 150.0, 200.0)

    before = viewer.manual_digitization_points
    with qtbot.waitSignal(
        viewer.manualDigitizationPointRejected,
        timeout=1000,
    ) as blocker:
        click_image_point(qtbot, viewer, 180.0, 150.0)

    assert "downward" in blocker.args[0].lower()
    assert viewer.manual_digitization_points == before
    assert len(node_items(viewer)) == 2
    assert len(path_items(viewer)) == 1


def test_disabling_manual_mode_preserves_draft_and_restores_pan(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)
    click_image_point(qtbot, viewer, 100.0, 100.0)
    click_image_point(qtbot, viewer, 160.0, 220.0)

    viewer.set_manual_digitization_enabled(False)

    assert len(viewer.manual_digitization_points) == 2
    assert len(node_items(viewer)) == 2
    assert len(path_items(viewer)) == 1
    assert viewer.dragMode() == QGraphicsView.DragMode.ScrollHandDrag


def test_clear_draft_removes_nodes_and_curve(qtbot, tmp_path: Path) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)
    click_image_point(qtbot, viewer, 100.0, 90.0)
    click_image_point(qtbot, viewer, 160.0, 210.0)

    viewer.clear_manual_digitization_draft()

    assert viewer.manual_digitization_points == ()
    assert node_items(viewer) == []
    assert path_items(viewer) == []


def test_loading_another_image_clears_transient_manual_draft(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.set_manual_digitization_enabled(True)
    click_image_point(qtbot, viewer, 100.0, 90.0)
    click_image_point(qtbot, viewer, 160.0, 210.0)

    second = tmp_path / "second.png"
    PIL.new("L", (500, 400), 170).save(second)
    viewer.load_image(second)

    assert viewer.manual_digitization_points == ()
    assert node_items(viewer) == []
    assert path_items(viewer) == []


def test_click_after_zoom_still_stores_canonical_image_pixels(
    qtbot,
    tmp_path: Path,
) -> None:
    viewer = make_viewer(qtbot, tmp_path)
    viewer.zoom_in()
    viewer.zoom_in()
    viewer.set_manual_digitization_enabled(True)

    click_image_point(qtbot, viewer, 275.0, 190.0)

    point = viewer.manual_digitization_points[0]
    assert point.x_px == pytest.approx(275.0, abs=2.0)
    assert point.y_px == pytest.approx(190.0, abs=2.0)
