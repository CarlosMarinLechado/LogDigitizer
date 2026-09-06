from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsView

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.gui.main_window import MainWindow


def make_window_with_saved_sections(qtbot, tmp_path: Path):
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

    upper = TraceSection.create_manual(
        (
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            TracePoint(60.0, 85.0),
        )
    )
    lower = TraceSection.create_manual(
        (
            TracePoint(120.0, 105.0),
            TracePoint(145.0, 135.0),
            TracePoint(130.0, 165.0),
        )
    )
    service.set_digitized_curve(
        DigitizedCurve(curve.curve_id, [upper, lower])
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
    return window, service, curve.curve_id, upper, lower


def click_path_item(qtbot, window: MainWindow, item) -> None:
    local_point = item.path().pointAtPercent(0.5)
    scene_point = item.mapToScene(local_point)
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def click_node_item(qtbot, window: MainWindow, item) -> None:
    viewport_point = window.viewer.mapFromScene(item.scenePos())
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def test_edit_button_requires_saved_section(qtbot, tmp_path: Path) -> None:
    window, _service, _curve_id, _upper, _lower = make_window_with_saved_sections(
        qtbot, tmp_path
    )

    assert window.digitization_panel.edit_button.isEnabled() is True
    assert window.digitization_panel.done_edit_button.isEnabled() is False


def test_start_edit_selection_and_select_saved_section(qtbot, tmp_path: Path) -> None:
    window, service, curve_id, upper, _lower = make_window_with_saved_sections(
        qtbot, tmp_path
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )

    controller = window.manual_digitization_controller
    assert controller.editing is True
    assert controller.editing_curve_id == curve_id
    assert window.viewer.saved_trace_selection_enabled is True
    assert window.viewer.dragMode() == QGraphicsView.DragMode.NoDrag
    assert window.digitization_panel.curve_combo.isEnabled() is False
    assert window.digitization_panel.start_button.isEnabled() is False
    assert window.digitization_panel.done_edit_button.isEnabled() is True
    assert window.viewer.saved_digitized_curves.node_items == ()

    path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.section_id == upper.section_id
    )
    assert path.selectable is True
    click_path_item(qtbot, window, path)

    assert controller.selected_section_id == upper.section_id
    assert controller.selected_node_index is None
    assert len(window.viewer.saved_digitized_curves.node_items) == 3
    selected_paths = [
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.selected
    ]
    assert len(selected_paths) == 1
    assert selected_paths[0].section_id == upper.section_id
    assert service.dirty is False


def test_selecting_node_highlights_it_without_modifying_geometry(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, _curve_id, upper, _lower = make_window_with_saved_sections(
        qtbot, tmp_path
    )
    original = tuple(upper.control_points_px)

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.section_id == upper.section_id
    )
    click_path_item(qtbot, window, path)

    node = window.viewer.saved_digitized_curves.node_items[1]
    click_node_item(qtbot, window, node)

    controller = window.manual_digitization_controller
    assert controller.selected_section_id == upper.section_id
    assert controller.selected_node_index == 1
    selected_nodes = [
        item
        for item in window.viewer.saved_digitized_curves.node_items
        if item.selected
    ]
    assert len(selected_nodes) == 1
    assert selected_nodes[0].node_index == 1

    persisted = service.get_digitized_curve(controller.editing_curve_id)
    assert persisted is not None
    assert persisted.get_section(upper.section_id).control_points_px == original
    assert service.dirty is False


def test_done_editing_clears_selection_nodes_without_dirtying_project(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, _curve_id, upper, _lower = make_window_with_saved_sections(
        qtbot, tmp_path
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    path = next(
        item
        for item in window.viewer.saved_digitized_curves.path_items
        if item.section_id == upper.section_id
    )
    click_path_item(qtbot, window, path)
    assert window.viewer.saved_digitized_curves.node_items

    qtbot.mouseClick(
        window.digitization_panel.done_edit_button,
        Qt.MouseButton.LeftButton,
    )

    controller = window.manual_digitization_controller
    assert controller.editing is False
    assert window.viewer.saved_trace_selection_enabled is False
    assert controller.selected_section_id is None
    assert controller.selected_node_index is None
    assert window.viewer.saved_digitized_curves.node_items == ()
    assert service.dirty is False
