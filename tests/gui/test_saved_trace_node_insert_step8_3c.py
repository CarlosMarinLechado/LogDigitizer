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


def click_path_item(qtbot, window: MainWindow, item, percent: float = 0.5):
    local_point = item.path().pointAtPercent(percent)
    scene_point = item.mapToScene(local_point)
    image_point = window.viewer.scene_to_image(scene_point)
    assert image_point is not None
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )
    return image_point


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
    assert window.manual_digitization_controller.selected_section_id == section_id
    assert len(window.viewer.saved_digitized_curves.node_items) == 3


def test_first_click_selects_section_without_inserting(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot, tmp_path
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.section_id == section.section_id
    )
    click_path_item(qtbot, window, path)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 3
    assert service.dirty is False
    assert window.manual_digitization_controller.selected_section_id == section.section_id


def test_clicking_selected_pchip_inserts_and_selects_new_node(
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
    clicked = click_path_item(qtbot, window, selected_path, percent=0.25)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    points = persisted.get_section(section.section_id).control_points_px
    assert len(points) == 4
    assert service.dirty is True

    inserted_index = window.manual_digitization_controller.selected_node_index
    assert inserted_index is not None
    inserted = points[inserted_index]
    assert inserted.x_px == pytest.approx(clicked.x(), abs=1.5)
    assert inserted.y_px == pytest.approx(clicked.y(), abs=1.5)
    assert points[inserted_index - 1].y_px < inserted.y_px < points[inserted_index + 1].y_px
    assert len(window.viewer.saved_digitized_curves.node_items) == 4

    selected_nodes = [
        item
        for item in window.viewer.saved_digitized_curves.node_items
        if item.selected
    ]
    assert len(selected_nodes) == 1
    assert selected_nodes[0].node_index == inserted_index


def test_insertion_too_close_to_existing_node_is_rejected_without_dirtying(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot, tmp_path
    )
    enter_editing_and_select_section(qtbot, window, section.section_id)

    # Bypass hit-testing to exercise the scientific guard directly: 25.1 px is
    # only 0.1 px below the first existing point, less than the 0.25 px margin.
    window.viewer.saved_digitized_curves.request_node_insert(
        curve_id,
        section.section_id,
        51.0,
        25.1,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 3
    assert service.dirty is False


def test_automatic_insertion_preserves_raw_trace_and_processing_settings(
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

    selected_path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.selected
    )
    click_path_item(qtbot, window, selected_path, percent=0.25)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    edited = persisted.get_section(section.section_id)
    assert len(edited.control_points_px) == 4
    assert edited.raw_samples_px == raw_before
    assert edited.smoothing_strength == pytest.approx(0.35)
    assert edited.simplification_tolerance_px == pytest.approx(1.25)
    assert service.dirty is True
