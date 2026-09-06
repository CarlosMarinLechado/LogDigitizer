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
):
    source = tmp_path / "scan.png"
    PIL.new("L", (260, 220), 225).save(source)

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
    service.add_depth_calibration_point(pixel_y=210.0, depth=200.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=250.0,
        value=150.0,
    )

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
                TracePoint(64.0, 70.0),
                TracePoint(60.0, 85.0),
                TracePoint(68.0, 100.0),
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


def test_cut_button_requires_section_and_can_be_cancelled(
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
    assert window.digitization_panel.cut_section_button.isEnabled() is False

    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section.section_id,
    )
    assert window.digitization_panel.cut_section_button.isEnabled() is True

    qtbot.mouseClick(
        window.digitization_panel.cut_section_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.manual_digitization_controller.cutting is True
    assert window.digitization_panel.cut_section_button.text() == "Cancel Cut"
    assert service.dirty is False

    qtbot.mouseClick(
        window.digitization_panel.cut_section_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.manual_digitization_controller.cutting is False
    assert window.digitization_panel.cut_section_button.text() == "Cut Selected Section"
    assert service.dirty is False


def test_cut_splits_manual_section_and_keeps_original_id_for_upper_half(
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
        window.digitization_panel.cut_section_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.manual_digitization_controller.cutting is True

    # This is the same signal emitted by a click on the selected PCHIP path.
    window.viewer.saved_digitized_curves.request_node_insert(
        curve_id,
        section.section_id,
        65.0,
        70.0,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 2
    upper, lower = persisted.sections

    assert upper.section_id == section.section_id
    assert lower.section_id != section.section_id
    assert upper.source is TraceSource.MANUAL
    assert lower.source is TraceSource.MANUAL
    assert [(p.x_px, p.y_px) for p in upper.control_points_px] == [
        (50.0, 25.0),
        (70.0, 55.0),
        (65.0, 70.0),
    ]
    assert [(p.x_px, p.y_px) for p in lower.control_points_px] == [
        (65.0, 70.0),
        (60.0, 85.0),
        (78.0, 115.0),
    ]
    assert upper.control_points_px[-1] == lower.control_points_px[0]
    assert service.dirty is True
    assert window.manual_digitization_controller.editing is True
    assert window.manual_digitization_controller.cutting is False
    assert window.manual_digitization_controller.selected_section_id == section.section_id
    assert window.digitization_panel.section_count_label.text() == "Saved sections: 2"


def test_cut_too_close_to_existing_node_is_rejected_and_stays_armed(
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
        window.digitization_panel.cut_section_button,
        Qt.MouseButton.LeftButton,
    )

    # 55.1 is only 0.1 px from the existing node at y=55, below the 0.25 px guard.
    window.viewer.saved_digitized_curves.request_node_insert(
        curve_id,
        section.section_id,
        69.0,
        55.1,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert persisted.sections[0].control_points_px == section.control_points_px
    assert service.dirty is False
    assert window.manual_digitization_controller.cutting is True


def test_automatic_cut_partitions_raw_samples_without_modifying_them(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, section = make_window_with_saved_section(
        qtbot,
        tmp_path,
        automatic=True,
    )
    raw_before = tuple(section.raw_samples_px)
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        section.section_id,
    )
    qtbot.mouseClick(
        window.digitization_panel.cut_section_button,
        Qt.MouseButton.LeftButton,
    )

    # 72 px deliberately falls between raw tracker samples, so the two raw
    # partitions together must be exactly the original sequence without duplication.
    window.viewer.saved_digitized_curves.request_node_insert(
        curve_id,
        section.section_id,
        63.0,
        72.0,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 2
    upper, lower = persisted.sections

    assert upper.source is TraceSource.AUTOMATIC
    assert lower.source is TraceSource.AUTOMATIC
    assert upper.raw_samples_px + lower.raw_samples_px == raw_before
    assert upper.smoothing_strength == pytest.approx(0.35)
    assert lower.smoothing_strength == pytest.approx(0.35)
    assert upper.simplification_tolerance_px == pytest.approx(1.25)
    assert lower.simplification_tolerance_px == pytest.approx(1.25)
    assert upper.control_points_px[-1] == lower.control_points_px[0]
    assert service.dirty is True
