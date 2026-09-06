from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import Qt

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
    two_nodes_only: bool = False,
):
    source = tmp_path / "scan.png"
    PIL.new("L", (240, 190), 225).save(source)

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
    service.add_depth_calibration_point(pixel_y=180.0, depth=170.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=230.0,
        value=150.0,
    )

    if two_nodes_only:
        controls = (
            TracePoint(50.0, 25.0),
            TracePoint(60.0, 105.0),
        )
    else:
        controls = (
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            TracePoint(60.0, 85.0),
            TracePoint(78.0, 115.0),
        )

    if automatic:
        section = TraceSection.create_automatic(
            raw_samples=(
                TracePoint(50.0, 25.0),
                TracePoint(56.0, 40.0),
                TracePoint(70.0, 55.0),
                TracePoint(66.0, 70.0),
                TracePoint(60.0, 85.0),
                TracePoint(69.0, 100.0),
                TracePoint(78.0, 115.0),
            ),
            control_points=controls,
            smoothing_strength=0.35,
            simplification_tolerance_px=1.25,
        )
    else:
        section = TraceSection.create_manual(controls)

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


def enter_editing_select_section_and_node(
    qtbot,
    window: MainWindow,
    curve_id: str,
    section_id: str,
    node_index: int,
) -> None:
    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section_id,
    )
    window.viewer.saved_digitized_curves.request_node_selection(
        curve_id,
        section_id,
        node_index,
    )
    assert window.manual_digitization_controller.selected_section_id == section_id
    assert window.manual_digitization_controller.selected_node_index == node_index


def test_delete_selected_node_persists_and_keeps_section_selected(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
    )
    enter_editing_select_section_and_node(
        qtbot,
        window,
        curve_id,
        section.section_id,
        1,
    )

    assert window.digitization_panel.delete_node_button.isEnabled()
    qtbot.mouseClick(
        window.digitization_panel.delete_node_button,
        Qt.MouseButton.LeftButton,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    points = persisted.get_section(section.section_id).control_points_px
    assert [(p.x_px, p.y_px) for p in points] == [
        (50.0, 25.0),
        (60.0, 85.0),
        (78.0, 115.0),
    ]
    assert service.dirty is True
    assert window.manual_digitization_controller.selected_section_id == section.section_id
    assert window.manual_digitization_controller.selected_node_index is None
    assert len(window.viewer.saved_digitized_curves.node_items) == 3
    assert window.digitization_panel.delete_node_button.isEnabled() is False


def test_two_node_section_cannot_delete_a_control_node(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
        two_nodes_only=True,
    )
    enter_editing_select_section_and_node(
        qtbot,
        window,
        curve_id,
        section.section_id,
        0,
    )

    assert window.digitization_panel.delete_node_button.isEnabled() is False

    # Exercise the controller guard directly as well as the disabled GUI state.
    window.digitization_panel.nodeDeleteRequested.emit()

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 2
    assert service.dirty is False
    assert window.manual_digitization_controller.selected_node_index == 0


def test_delete_requires_an_explicit_selected_node(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section.section_id,
    )

    assert window.manual_digitization_controller.selected_node_index is None
    assert window.digitization_panel.delete_node_button.isEnabled() is False
    window.digitization_panel.nodeDeleteRequested.emit()

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 4
    assert service.dirty is False


def test_automatic_node_delete_preserves_raw_trace_and_processing_settings(
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

    enter_editing_select_section_and_node(
        qtbot,
        window,
        curve_id,
        section.section_id,
        2,
    )
    qtbot.mouseClick(
        window.digitization_panel.delete_node_button,
        Qt.MouseButton.LeftButton,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    edited = persisted.get_section(section.section_id)
    assert len(edited.control_points_px) == 3
    assert edited.raw_samples_px == raw_before
    assert edited.smoothing_strength == pytest.approx(0.35)
    assert edited.simplification_tolerance_px == pytest.approx(1.25)
    assert service.dirty is True
