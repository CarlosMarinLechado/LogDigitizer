from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPoint, Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.gui.main_window import MainWindow


def make_service(
    tmp_path: Path,
    *,
    complete_x_calibration: bool = True,
) -> tuple[ProjectService, str]:
    source = tmp_path / "external.png"
    PIL.new("L", (120, 120), 220).save(source)

    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
        well_name="BH-01",
    )
    segment = service.add_images([source])[0]

    # Step 8.2C needs a rectified-image context. Reusing the copied PNG keeps
    # this GUI/controller test focused on digitization rather than rectification.
    segment.extensions["preprocess"] = {
        "derived_path": segment.source_path,
    }

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    service.add_depth_calibration_point(pixel_y=10.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=110.0, depth=100.0)

    if complete_x_calibration:
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=10.0,
            value=0.0,
        )
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=110.0,
            value=150.0,
        )

    return service, curve.curve_id


def make_window(qtbot, service: ProjectService) -> MainWindow:
    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(900, 650)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh()
    return window


def test_main_window_has_separate_digitization_dock(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.digitization_dock.objectName() == "digitizationDock"
    assert window.digitization_dock.widget() is window.digitization_panel
    assert window.manual_digitization_controller is not None


def test_ready_curve_can_start_manual_trace(qtbot, tmp_path: Path) -> None:
    service, curve_id = make_service(tmp_path)
    window = make_window(qtbot, service)

    index = window.digitization_panel.curve_combo.findData(curve_id)
    window.digitization_panel.curve_combo.setCurrentIndex(index)
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    assert window.digitization_panel.start_button.isEnabled()
    assert window.digitization_panel.readiness_label.text() == (
        "✓ GR ready for manual digitization."
    )
    qtbot.mouseClick(
        window.digitization_panel.start_button,
        Qt.MouseButton.LeftButton,
    )

    assert window.manual_digitization_controller.tracing is True
    assert window.viewer.manual_digitization_enabled is True
    assert window.digitization_panel.cancel_button.isEnabled()
    assert window.digitization_panel.finish_button.isEnabled() is False


def test_finish_persists_manual_section_and_marks_project_dirty(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    service.save()
    assert service.dirty is False

    window = make_window(qtbot, service)
    index = window.digitization_panel.curve_combo.findData(curve_id)
    window.digitization_panel.curve_combo.setCurrentIndex(index)
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(
        window.digitization_panel.start_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.viewer.manual_digitization.add_point(30.0, 25.0)
    assert window.viewer.manual_digitization.add_point(45.0, 75.0)
    assert window.digitization_panel.finish_button.isEnabled()

    qtbot.mouseClick(
        window.digitization_panel.finish_button,
        Qt.MouseButton.LeftButton,
    )

    digitized = service.get_digitized_curve(curve_id)
    assert digitized is not None
    assert len(digitized.sections) == 1
    section = digitized.sections[0]
    assert section.source is TraceSource.MANUAL
    assert [(p.x_px, p.y_px) for p in section.control_points_px] == [
        (30.0, 25.0),
        (45.0, 75.0),
    ]
    assert service.dirty is True
    assert window.manual_digitization_controller.tracing is False
    assert window.viewer.manual_digitization_enabled is False


def test_right_click_saves_manual_section_and_keeps_session_active(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    service.save()
    window = make_window(qtbot, service)
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    assert window.viewer.manual_digitization.add_point(30.0, 25.0)
    assert window.viewer.manual_digitization.add_point(45.0, 75.0)

    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert persisted.sections[0].source is TraceSource.MANUAL
    assert window.manual_digitization_controller.tracing is True
    assert window.viewer.manual_digitization_enabled is True
    assert window.viewer.manual_digitization_points == ()
    assert panel.finish_button.isEnabled()
    assert service.dirty is True


def test_continuous_manual_session_starts_second_section_without_start_button(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    window = make_window(qtbot, service)
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    assert window.viewer.manual_digitization.add_point(30.0, 25.0)
    assert window.viewer.manual_digitization.add_point(45.0, 55.0)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    # No second Start click: the next left-clicks build a fresh section.
    assert window.viewer.manual_digitization.add_point(55.0, 80.0)
    assert window.viewer.manual_digitization.add_point(65.0, 110.0)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    manual_sections = [
        section
        for section in persisted.sections
        if section.source is TraceSource.MANUAL
    ]
    assert len(manual_sections) == 2
    assert window.manual_digitization_controller.tracing is True
    assert window.viewer.manual_digitization_enabled is True
    assert window.viewer.manual_digitization_points == ()


def test_finish_button_ends_continuous_session_after_right_click_save(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    window = make_window(qtbot, service)
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    assert window.viewer.manual_digitization.add_point(30.0, 25.0)
    assert window.viewer.manual_digitization.add_point(45.0, 75.0)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    assert panel.finish_button.isEnabled()
    qtbot.mouseClick(panel.finish_button, Qt.MouseButton.LeftButton)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert window.manual_digitization_controller.tracing is False
    assert window.viewer.manual_digitization_enabled is False


def test_right_click_on_empty_manual_session_is_noop(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    window = make_window(qtbot, service)
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    assert service.get_digitized_curve(curve_id) is None
    assert window.manual_digitization_controller.tracing is True
    assert window.viewer.manual_digitization_enabled is True


def test_manual_trace_replaces_automatic_y_range_as_one_undoable_edit(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    raw = tuple(
        TracePoint(40.0 + y_px / 10.0, float(y_px))
        for y_px in range(15, 106)
    )
    automatic = TraceSection.create_automatic(raw_samples=raw)
    service.set_digitized_curve(DigitizedCurve(curve_id, [automatic]))
    service.save()

    window = make_window(qtbot, service)
    index = window.digitization_panel.curve_combo.findData(curve_id)
    window.digitization_panel.curve_combo.setCurrentIndex(index)
    controller = window.manual_digitization_controller
    controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(
        window.digitization_panel.start_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.viewer.manual_digitization.add_point(70.0, 45.0)
    assert window.viewer.manual_digitization.add_point(75.0, 60.0)
    assert window.viewer.manual_digitization.add_point(68.0, 75.0)
    qtbot.mouseClick(
        window.digitization_panel.finish_button,
        Qt.MouseButton.LeftButton,
    )

    corrected = service.get_digitized_curve(curve_id)
    assert corrected is not None
    assert [section.source for section in corrected.sections] == [
        TraceSource.AUTOMATIC,
        TraceSource.MANUAL,
        TraceSource.AUTOMATIC,
    ]
    upper, manual, lower = corrected.sections
    assert upper.raw_samples_px[-1] == manual.control_points_px[0]
    assert lower.raw_samples_px[0] == manual.control_points_px[-1]
    assert all(
        point.y_px < 45.0 or point == manual.control_points_px[0]
        for point in upper.raw_samples_px
    )
    assert all(
        point.y_px > 75.0 or point == manual.control_points_px[-1]
        for point in lower.raw_samples_px
    )
    assert controller.undo_stack.undoText() == "Apply manual correction"

    controller.undo()
    restored = service.get_digitized_curve(curve_id)
    assert restored is not None
    assert len(restored.sections) == 1
    assert restored.sections[0].section_id == automatic.section_id
    assert restored.sections[0].raw_samples_px == raw

    controller.redo()
    redone = service.get_digitized_curve(curve_id)
    assert redone is not None
    assert [section.source for section in redone.sections] == [
        TraceSource.AUTOMATIC,
        TraceSource.MANUAL,
        TraceSource.AUTOMATIC,
    ]


def test_cancel_discards_draft_without_persisting(qtbot, tmp_path: Path) -> None:
    service, curve_id = make_service(tmp_path)
    service.save()
    window = make_window(qtbot, service)

    index = window.digitization_panel.curve_combo.findData(curve_id)
    window.digitization_panel.curve_combo.setCurrentIndex(index)
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    qtbot.mouseClick(
        window.digitization_panel.start_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.viewer.manual_digitization.add_point(25.0, 20.0)
    assert window.viewer.manual_digitization.add_point(40.0, 70.0)

    qtbot.mouseClick(
        window.digitization_panel.cancel_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.get_digitized_curve(curve_id) is None
    assert service.dirty is False
    assert window.viewer.manual_digitization_points == ()
    assert window.manual_digitization_controller.tracing is False


def test_start_is_blocked_until_selected_curve_is_calibrated(
    qtbot,
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(
        tmp_path,
        complete_x_calibration=False,
    )
    window = make_window(qtbot, service)

    index = window.digitization_panel.curve_combo.findData(curve_id)
    window.digitization_panel.curve_combo.setCurrentIndex(index)
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    assert window.digitization_panel.start_button.isEnabled() is False
    assert window.digitization_panel.readiness_label.text() == (
        "⚠ Calibrate before digitizing"
    )
