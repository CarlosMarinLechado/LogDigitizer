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


def make_window_with_sections(
    qtbot,
    tmp_path: Path,
    sections: list[TraceSection],
):
    source = tmp_path / "scan.png"
    PIL.new("L", (280, 240), 225).save(source)

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
    service.add_depth_calibration_point(pixel_y=230.0, depth=220.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=270.0,
        value=150.0,
    )

    service.set_digitized_curve(DigitizedCurve(curve.curve_id, sections))
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
    return window, service, curve.curve_id


def manual_cut_pair() -> tuple[TraceSection, TraceSection]:
    shared = TracePoint(65.0, 70.0)
    upper = TraceSection.create_manual(
        (
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            shared,
        )
    )
    lower = TraceSection.create_manual(
        (
            shared,
            TracePoint(60.0, 85.0),
            TracePoint(78.0, 115.0),
        )
    )
    return upper, lower


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


def test_join_button_requires_selected_section_and_can_be_cancelled(
    qtbot,
    tmp_path: Path,
) -> None:
    upper, lower = manual_cut_pair()
    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.digitization_panel.join_section_button.isEnabled() is False

    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        upper.section_id,
    )
    assert window.digitization_panel.join_section_button.isEnabled() is True

    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.manual_digitization_controller.joining is True
    assert window.digitization_panel.join_section_button.text() == "Cancel Join"
    assert window.digitization_panel.cut_section_button.isEnabled() is False
    assert service.dirty is False

    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.manual_digitization_controller.joining is False
    assert window.digitization_panel.join_section_button.text() == "Join Selected Section"
    assert service.dirty is False


def test_join_reverses_cut_and_preserves_upper_original_section_id(
    qtbot,
    tmp_path: Path,
) -> None:
    upper, lower = manual_cut_pair()
    original_upper_id = upper.section_id
    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        upper.section_id,
    )

    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        lower.section_id,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    joined = persisted.sections[0]
    assert joined.section_id == original_upper_id
    assert joined.source is TraceSource.MANUAL
    assert [(p.x_px, p.y_px) for p in joined.control_points_px] == [
        (50.0, 25.0),
        (70.0, 55.0),
        (65.0, 70.0),
        (60.0, 85.0),
        (78.0, 115.0),
    ]
    assert service.dirty is True
    assert window.manual_digitization_controller.joining is False
    assert window.manual_digitization_controller.selected_section_id == original_upper_id
    assert window.digitization_panel.section_count_label.text() == "Saved sections: 1"


def test_join_uses_shallower_section_id_even_when_lower_is_selected_first(
    qtbot,
    tmp_path: Path,
) -> None:
    upper, lower = manual_cut_pair()
    upper_id = upper.section_id
    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        lower.section_id,
    )

    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        upper.section_id,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert persisted.sections[0].section_id == upper_id
    assert window.manual_digitization_controller.selected_section_id == upper_id


def test_join_bridges_vertical_gap_between_neighboring_manual_sections(
    qtbot,
    tmp_path: Path,
) -> None:
    upper = TraceSection.create_manual(
        (
            TracePoint(45.0, 20.0),
            TracePoint(55.0, 50.0),
        )
    )
    lower = TraceSection.create_manual(
        (
            TracePoint(80.0, 90.0),
            TracePoint(72.0, 120.0),
        )
    )
    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        upper.section_id,
    )
    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        lower.section_id,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert [(p.x_px, p.y_px) for p in persisted.sections[0].control_points_px] == [
        (45.0, 20.0),
        (55.0, 50.0),
        (80.0, 90.0),
        (72.0, 120.0),
    ]
    assert service.dirty is True


def test_automatic_join_preserves_raw_samples_and_processing_settings(
    qtbot,
    tmp_path: Path,
) -> None:
    shared_control = TracePoint(65.0, 70.0)
    shared_raw = TracePoint(66.0, 70.0)
    upper = TraceSection.create_automatic(
        control_points=(
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            shared_control,
        ),
        raw_samples=(
            TracePoint(50.0, 25.0),
            TracePoint(56.0, 40.0),
            TracePoint(70.0, 55.0),
            shared_raw,
        ),
        smoothing_strength=0.35,
        simplification_tolerance_px=1.25,
    )
    lower = TraceSection.create_automatic(
        control_points=(
            shared_control,
            TracePoint(60.0, 85.0),
            TracePoint(78.0, 115.0),
        ),
        raw_samples=(
            shared_raw,
            TracePoint(64.0, 85.0),
            TracePoint(68.0, 100.0),
            TracePoint(78.0, 115.0),
        ),
        smoothing_strength=0.35,
        simplification_tolerance_px=1.25,
    )
    raw_expected = (
        TracePoint(50.0, 25.0),
        TracePoint(56.0, 40.0),
        TracePoint(70.0, 55.0),
        shared_raw,
        TracePoint(64.0, 85.0),
        TracePoint(68.0, 100.0),
        TracePoint(78.0, 115.0),
    )

    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        upper.section_id,
    )
    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        lower.section_id,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    joined = persisted.sections[0]
    assert joined.source is TraceSource.AUTOMATIC
    assert joined.raw_samples_px == raw_expected
    assert joined.smoothing_strength == pytest.approx(0.35)
    assert joined.simplification_tolerance_px == pytest.approx(1.25)
    assert service.dirty is True


def test_join_rejects_mixed_manual_and_automatic_provenance(
    qtbot,
    tmp_path: Path,
) -> None:
    upper = TraceSection.create_manual(
        (TracePoint(45.0, 20.0), TracePoint(55.0, 50.0))
    )
    lower = TraceSection.create_automatic(
        control_points=(TracePoint(80.0, 90.0), TracePoint(72.0, 120.0)),
        raw_samples=(TracePoint(80.0, 90.0), TracePoint(72.0, 120.0)),
        smoothing_strength=0.2,
        simplification_tolerance_px=1.0,
    )
    window, service, curve_id = make_window_with_sections(
        qtbot,
        tmp_path,
        [upper, lower],
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        upper.section_id,
    )
    qtbot.mouseClick(
        window.digitization_panel.join_section_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        lower.section_id,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 2
    assert service.dirty is False
    assert window.manual_digitization_controller.joining is True
