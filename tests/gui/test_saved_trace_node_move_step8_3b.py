from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.gui.main_window import MainWindow


def make_window_with_saved_section(
    qtbot,
    tmp_path: Path,
    *,
    automatic: bool = False,
):
    source = tmp_path / "scan.png"
    PIL.new("L", (220, 180), 225).save(source)

    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
    )
    segment = service.add_images([source])[0]
    segment.extensions["preprocess"] = {"derived_path": segment.source_path}

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )
    service.add_depth_calibration_point(pixel_y=10.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=170.0, depth=160.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=210.0,
        value=150.0,
    )

    if automatic:
        section = TraceSection.create_automatic(
            raw_samples=(
                TracePoint(50.0, 25.0),
                TracePoint(56.0, 40.0),
                TracePoint(70.0, 55.0),
                TracePoint(64.0, 70.0),
                TracePoint(60.0, 85.0),
            ),
            control_points=(
                TracePoint(50.0, 25.0),
                TracePoint(70.0, 55.0),
                TracePoint(60.0, 85.0),
            ),
            smoothing_strength=0.35,
            simplification_tolerance_px=1.25,
        )
    else:
        section = TraceSection.create_manual(
            (
                TracePoint(50.0, 25.0),
                TracePoint(70.0, 55.0),
                TracePoint(60.0, 85.0),
            )
        )

    service.set_digitized_curve(
        DigitizedCurve(curve.curve_id, [section])
    )
    service.save()
    assert service.dirty is False

    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(1000, 720)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve.curve_id
    )
    return window, service, curve.curve_id, section


def click_path_item(qtbot, window: MainWindow, item) -> None:
    local_point = item.path().pointAtPercent(0.5)
    scene_point = item.mapToScene(local_point)
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def enter_editing_and_select_section(
    qtbot,
    window: MainWindow,
    section_id: str,
) -> None:
    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.section_id == section_id
    )
    click_path_item(qtbot, window, path)
    assert len(window.viewer.saved_digitized_curves.node_items) == 3


def drag_node(
    qtbot,
    window: MainWindow,
    node_index: int,
    *,
    target_x: float,
    target_y: float,
    release: bool = True,
) -> None:
    viewer = window.viewer
    node = next(
        item
        for item in viewer.saved_digitized_curves.node_items
        if item.node_index == node_index
    )
    start = viewer.mapFromScene(node.scenePos())
    target = viewer.mapFromScene(
        viewer.image_to_scene(QPointF(target_x, target_y))
    )
    qtbot.mousePress(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=start,
    )
    qtbot.mouseMove(viewer.viewport(), pos=target, delay=15)
    if release:
        qtbot.mouseRelease(
            viewer.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )


def test_drag_previews_pchip_without_persisting_until_mouse_release(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot, tmp_path
    )
    enter_editing_and_select_section(qtbot, window, section.section_id)

    selected_path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.selected
    )
    before_right = selected_path.path().boundingRect().right()

    drag_node(
        qtbot,
        window,
        1,
        target_x=110.0,
        target_y=65.0,
        release=False,
    )

    preview_node = next(
        item
        for item in window.viewer.saved_digitized_curves.node_items
        if item.node_index == 1
    )
    preview_path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.selected
    )
    assert preview_node.point_px.x_px == pytest.approx(110.0, abs=1.5)
    assert preview_node.point_px.y_px == pytest.approx(65.0, abs=1.5)
    assert preview_path.path().boundingRect().right() > before_right

    persisted_before_release = service.get_digitized_curve(curve_id)
    assert persisted_before_release is not None
    assert persisted_before_release.get_section(section.section_id).control_points_px[1] == (
        TracePoint(70.0, 55.0)
    )
    assert service.dirty is False

    target = window.viewer.mapFromScene(
        window.viewer.image_to_scene(QPointF(110.0, 65.0))
    )
    qtbot.mouseRelease(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=target,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    moved = persisted.get_section(section.section_id).control_points_px[1]
    assert moved.x_px == pytest.approx(110.0, abs=1.5)
    assert moved.y_px == pytest.approx(65.0, abs=1.5)
    assert service.dirty is True
    assert window.manual_digitization_controller.selected_node_index == 1


def test_drag_cannot_cross_vertical_neighbour(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot, tmp_path
    )
    enter_editing_and_select_section(qtbot, window, section.section_id)

    # Attempt to drag middle node below the next node (85 px).
    drag_node(
        qtbot,
        window,
        1,
        target_x=95.0,
        target_y=120.0,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    points = persisted.get_section(section.section_id).control_points_px
    assert points[0].y_px < points[1].y_px < points[2].y_px
    assert points[1].y_px < 85.0
    assert points[1].y_px > 25.0
    assert points[1].x_px == pytest.approx(95.0, abs=1.5)


def test_clicking_node_without_moving_does_not_dirty_project(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, _curve_id, section = make_window_with_saved_section(
        qtbot, tmp_path
    )
    enter_editing_and_select_section(qtbot, window, section.section_id)

    node = window.viewer.saved_digitized_curves.node_items[1]
    pos = window.viewer.mapFromScene(node.scenePos())
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=pos,
    )

    assert window.manual_digitization_controller.selected_node_index == 1
    assert service.dirty is False


def test_dragging_automatic_control_node_preserves_raw_trace_and_processing(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
        automatic=True,
    )
    assert section.source is TraceSource.AUTOMATIC
    raw_before = tuple(section.raw_samples_px)
    enter_editing_and_select_section(qtbot, window, section.section_id)

    drag_node(
        qtbot,
        window,
        1,
        target_x=105.0,
        target_y=60.0,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    edited = persisted.get_section(section.section_id)
    assert edited.raw_samples_px == raw_before
    assert edited.smoothing_strength == pytest.approx(0.35)
    assert edited.simplification_tolerance_px == pytest.approx(1.25)
    assert edited.control_points_px[1].x_px == pytest.approx(105.0, abs=1.5)
    assert edited.control_points_px[1].y_px == pytest.approx(60.0, abs=1.5)
