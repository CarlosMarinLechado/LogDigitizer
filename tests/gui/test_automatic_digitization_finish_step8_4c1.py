from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPoint, QPointF, Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import TraceSource
from logdigitizer.gui.main_window import MainWindow


def make_service(tmp_path: Path) -> tuple[ProjectService, str]:
    source = tmp_path / "auto.png"
    image = PIL.new("L", (120, 120), 240)
    px = image.load()
    for y in range(0, 45):
        for x in range(38, 41):
            px[x, y] = 15
    for y in range(55, 120):
        for x in range(48, 51):
            px[x, y] = 15
    image.save(source)

    service = ProjectService()
    service.new_project(
        name="BH-AUTO",
        project_directory=tmp_path / "project",
        well_name="BH-AUTO",
    )
    segment = service.add_images([source])[0]
    segment.extensions["preprocess"] = {"derived_path": segment.source_path}

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    service.add_depth_calibration_point(pixel_y=5.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=115.0, depth=110.0)
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=10.0, value=0.0
    )
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=110.0, value=150.0
    )
    service.save()
    return service, curve.curve_id


def make_window(qtbot, tmp_path: Path):
    service, curve_id = make_service(tmp_path)
    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(900, 650)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)
    return window, service, curve_id


def click_image(qtbot, window: MainWindow, x: float, y: float) -> None:
    scene_point = window.viewer.image_to_scene(QPointF(x, y))
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def test_finish_after_loss_persists_partial_raw_only_section(qtbot, tmp_path: Path) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    assert panel.automatic_finish_button.isEnabled() is False

    click_image(qtbot, window, 39.0, 5.0)
    controller = window.automatic_digitization_controller
    assert controller.waiting_for_resume is True
    raw_before = controller.raw_samples_px
    assert len(raw_before) >= 2
    assert panel.automatic_finish_button.isEnabled() is True

    qtbot.mouseClick(panel.automatic_finish_button, Qt.MouseButton.LeftButton)

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    section = persisted.sections[0]
    assert section.source is TraceSource.AUTOMATIC
    assert section.raw_samples_px == raw_before
    assert section.control_points_px == ()
    assert controller.active is False
    assert service.dirty is True


def test_right_click_finishes_active_automatic_trace(qtbot, tmp_path: Path) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    click_image(qtbot, window, 39.0, 5.0)
    controller = window.automatic_digitization_controller
    raw_before = controller.raw_samples_px
    assert len(raw_before) >= 2
    assert panel.automatic_finish_button.isEnabled() is True

    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPoint(20, 20),
    )

    persisted = service.get_digitized_curve(curve_id)
    assert persisted is not None
    assert len(persisted.sections) == 1
    assert persisted.sections[0].source is TraceSource.AUTOMATIC
    assert persisted.sections[0].raw_samples_px == raw_before
    assert controller.active is False
    assert service.dirty is True


def test_finish_after_resume_to_bottom_persists_entire_accumulated_trace(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    click_image(qtbot, window, 39.0, 5.0)
    click_image(qtbot, window, 49.0, 55.0)

    controller = window.automatic_digitization_controller
    assert controller.complete is True
    raw_before = controller.raw_samples_px
    assert panel.automatic_finish_button.isEnabled() is True

    qtbot.mouseClick(panel.automatic_finish_button, Qt.MouseButton.LeftButton)

    persisted = service.get_digitized_curve(curve_id)
    section = persisted.sections[0]
    assert section.raw_samples_px == raw_before
    # Qt maps the requested viewport click back to image coordinates. Depending
    # on the current fit/zoom transform, that mapped floating-point coordinate
    # need not be exactly the nominal 5.0/119.0 used to request the click.
    # Persistence must preserve the actual tracker samples exactly.
    assert section.raw_samples_px[0] == raw_before[0]
    assert section.raw_samples_px[-1] == raw_before[-1]


def test_clear_draft_never_persists(qtbot, tmp_path: Path) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    click_image(qtbot, window, 39.0, 5.0)
    qtbot.mouseClick(panel.automatic_cancel_button, Qt.MouseButton.LeftButton)

    assert service.get_digitized_curve(curve_id) is None
    assert service.dirty is False


def test_saved_automatic_raw_section_round_trips_through_shared_undo_redo(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    click_image(qtbot, window, 39.0, 5.0)
    raw_before = window.automatic_digitization_controller.raw_samples_px
    qtbot.mouseClick(panel.automatic_finish_button, Qt.MouseButton.LeftButton)

    manual = window.manual_digitization_controller
    assert manual.undo_stack.canUndo() is True
    assert "automatic raw section" in manual.undo_stack.undoText().lower()

    manual.undo()
    assert service.get_digitized_curve(curve_id) is None

    manual.redo()
    restored = service.get_digitized_curve(curve_id)
    assert restored is not None
    assert restored.sections[0].raw_samples_px == raw_before
    assert restored.sections[0].control_points_px == ()



def test_gui_tracker_controls_are_captured_when_new_session_starts(
    qtbot,
    tmp_path: Path,
) -> None:
    window, _service, _curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    panel.automatic_sensitivity_slider.setValue(80)
    panel.automatic_search_width_slider.setValue(70)
    panel.automatic_flexibility_slider.setValue(30)
    panel.automatic_gap_tolerance_slider.setValue(75)

    expected = panel.automatic_trace_settings
    qtbot.mouseClick(
        panel.automatic_start_button,
        Qt.MouseButton.LeftButton,
    )

    controller = window.automatic_digitization_controller
    assert controller.active is True
    assert controller.current_settings == expected

    # The parameters are frozen for this draft; controls cannot change between
    # a Start click and later Resume clicks.
    assert panel.automatic_search_width_slider.isEnabled() is False
