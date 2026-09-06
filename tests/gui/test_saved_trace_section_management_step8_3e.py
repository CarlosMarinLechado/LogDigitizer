from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.domain.states import StageStatus
from logdigitizer.gui.main_window import MainWindow


def make_window_with_sections(
    qtbot,
    tmp_path: Path,
    *,
    section_count: int = 2,
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

    sections = [
        TraceSection.create_manual(
            (
                TracePoint(50.0, 25.0),
                TracePoint(70.0, 55.0),
                TracePoint(60.0, 85.0),
            )
        ),
        TraceSection.create_manual(
            (
                TracePoint(110.0, 120.0),
                TracePoint(125.0, 150.0),
                TracePoint(118.0, 185.0),
            )
        ),
    ][:section_count]
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
    return window, service, segment, curve.curve_id, sections


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


def test_delete_section_button_requires_selected_section(
    qtbot,
    tmp_path: Path,
) -> None:
    window, _service, _segment, curve_id, sections = make_window_with_sections(
        qtbot,
        tmp_path,
    )

    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.digitization_panel.delete_section_button.isEnabled() is False

    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        sections[0].section_id,
    )
    assert window.digitization_panel.delete_section_button.isEnabled() is True


def test_confirmed_delete_removes_only_selected_section_and_stays_editing(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window, service, _segment, curve_id, sections = make_window_with_sections(
        qtbot,
        tmp_path,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        sections[0].section_id,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    qtbot.mouseClick(
        window.digitization_panel.delete_section_button,
        Qt.MouseButton.LeftButton,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert [section.section_id for section in persisted.sections] == [
        sections[1].section_id
    ]
    assert service.dirty is True
    assert window.manual_digitization_controller.editing is True
    assert window.manual_digitization_controller.selected_section_id is None
    assert window.digitization_panel.delete_section_button.isEnabled() is False


def test_cancelled_section_delete_changes_nothing(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window, service, _segment, curve_id, sections = make_window_with_sections(
        qtbot,
        tmp_path,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        sections[0].section_id,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )

    qtbot.mouseClick(
        window.digitization_panel.delete_section_button,
        Qt.MouseButton.LeftButton,
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert [section.section_id for section in persisted.sections] == [
        section.section_id for section in sections
    ]
    assert service.dirty is False
    assert window.manual_digitization_controller.editing is True
    assert (
        window.manual_digitization_controller.selected_section_id
        == sections[0].section_id
    )


def test_deleting_last_section_removes_curve_digitization_and_exits_edit_mode(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window, service, segment, curve_id, sections = make_window_with_sections(
        qtbot,
        tmp_path,
        section_count=1,
    )
    enter_editing_and_select_section(
        qtbot,
        window,
        curve_id,
        sections[0].section_id,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    qtbot.mouseClick(
        window.digitization_panel.delete_section_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.get_digitized_curve(curve_id) is None
    assert segment.digitization_status is StageStatus.NOT_STARTED
    assert service.dirty is True
    assert window.manual_digitization_controller.editing is False
    assert window.viewer.saved_trace_selection_enabled is False
    assert window.digitization_panel.section_count_label.text() == "Saved sections: 0"
    assert window.digitization_panel.edit_button.isEnabled() is False
