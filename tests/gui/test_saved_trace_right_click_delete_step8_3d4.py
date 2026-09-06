from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPoint, Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.gui.main_window import MainWindow


def make_window_with_saved_section(
    qtbot,
    tmp_path: Path,
    *,
    point_count: int = 4,
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

    all_points = (
        TracePoint(50.0, 25.0),
        TracePoint(70.0, 55.0),
        TracePoint(60.0, 85.0),
        TracePoint(78.0, 115.0),
    )
    section = TraceSection.create_manual(all_points[:point_count])
    service.set_digitized_curve(DigitizedCurve(curve.curve_id, [section]))
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


def enter_editing_and_select_section(
    qtbot,
    window: MainWindow,
    curve_id: str,
    section_id: str,
) -> None:
    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section_id,
    )
    assert window.manual_digitization_controller.selected_section_id == section_id
    assert window.viewer.saved_digitized_curves.node_items


def click_node(
    qtbot,
    window: MainWindow,
    node_index: int,
    button: Qt.MouseButton,
) -> None:
    node = next(
        item
        for item in window.viewer.saved_digitized_curves.node_items
        if item.node_index == node_index
    )
    target = window.viewer.mapFromScene(node.scenePos())
    qtbot.mouseClick(
        window.viewer.viewport(),
        button,
        pos=target,
    )


def test_right_click_deletes_node_without_prior_left_selection(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        section.section_id,
    )
    assert window.manual_digitization_controller.selected_node_index is None

    click_node(qtbot, window, 1, Qt.MouseButton.RightButton)

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


def test_right_click_cannot_reduce_section_below_two_nodes(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
        point_count=2,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        section.section_id,
    )

    click_node(qtbot, window, 0, Qt.MouseButton.RightButton)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 2
    assert service.dirty is False


def test_right_click_on_empty_canvas_does_not_modify_digitization(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        section.section_id,
    )

    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(5, 5),
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 4
    assert service.dirty is False


def test_left_click_still_selects_node_without_deleting_it(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        section.section_id,
    )

    click_node(qtbot, window, 2, Qt.MouseButton.LeftButton)

    assert window.manual_digitization_controller.selected_node_index == 2
    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.get_section(section.section_id).control_points_px) == 4
    assert service.dirty is False
