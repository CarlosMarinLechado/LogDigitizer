from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsScene

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition, ScaleType
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.gui.automatic_digitization_panel import AutomaticDigitizationPanel
from logdigitizer.gui.digitized_curves import SavedDigitizedCurvesOverlay
from logdigitizer.gui.main_window import MainWindow
from logdigitizer.gui.overlays import OverlayManager


def make_window(qtbot, tmp_path: Path) -> tuple[MainWindow, ProjectService, str]:
    source = tmp_path / "wrap.png"
    image = PIL.new("L", (120, 120), 240)
    pixels = image.load()
    for y_px in range(120):
        for x_px in range(38, 41):
            pixels[x_px, y_px] = 15
    image.save(source)

    service = ProjectService()
    service.new_project(
        name="BH-WRAP",
        project_directory=tmp_path / "project",
        well_name="BH-WRAP",
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
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=110.0,
        value=150.0,
    )
    service.save()

    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(900, 650)
    window.show()
    window._load_active_segment()
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(
        panel.curve_combo.findData(curve.curve_id)
    )
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve.curve_id
    )
    return window, service, curve.curve_id


def click_image(qtbot, window: MainWindow, x_px: float, y_px: float) -> None:
    scene_point = window.viewer.image_to_scene(QPointF(x_px, y_px))
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def test_offset_control_is_visible_explicit_and_locked_during_capture(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    panel.set_curves((curve,), curve.curve_id)
    panel.set_context(
        project_available=True,
        segment_available=True,
        image_ready=True,
        readiness_message="Ready",
        saved_section_count=0,
        can_start=True,
    )

    panel.scale_offset_spin.setValue(1)
    assert panel.scale_offset == 1
    assert panel.scale_offset_spin.text() == "+1"
    assert panel.scale_offset_badge.text() == "GR · +1 scale"
    assert panel.scale_offset_badge.isVisibleTo(panel)

    panel.set_draft_state(True, 0)
    assert panel.scale_offset_spin.isEnabled() is False
    panel.set_draft_state(False, 0)
    assert panel.scale_offset_spin.isEnabled() is True

    panel.set_automatic_state(True)
    assert panel.scale_offset_spin.isEnabled() is False


def test_logarithmic_curve_does_not_enable_offset_control(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)
    curve = CurveDefinition.create(
        name="Resistivity",
        mnemonic="RES",
        unit="OHM.M",
        scale_type=ScaleType.LOGARITHMIC,
    )
    panel.set_curves((curve,), curve.curve_id)
    panel.set_context(
        project_available=True,
        segment_available=True,
        image_ready=True,
        readiness_message="Ready",
        saved_section_count=0,
        can_start=True,
    )

    assert panel.scale_offset_spin.isEnabled() is False


def test_manual_and_automatic_sections_capture_selected_offset(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    panel = window.digitization_panel

    panel.scale_offset_spin.setValue(1)
    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    assert panel.scale_offset_spin.isEnabled() is False
    assert window.viewer.manual_digitization.add_point(20.0, 15.0)
    assert window.viewer.manual_digitization.add_point(25.0, 35.0)
    qtbot.mouseClick(panel.finish_button, Qt.MouseButton.LeftButton)

    manual = service.get_digitized_curve(curve_id)
    assert manual is not None
    assert manual.sections[0].scale_offset == 1
    assert panel.scale_offset == 1

    panel.scale_offset_spin.setValue(-1)
    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)
    click_image(qtbot, window, 39.0, 45.0)
    qtbot.mouseClick(panel.automatic_finish_button, Qt.MouseButton.LeftButton)

    saved = service.get_digitized_curve(curve_id)
    assert saved is not None
    assert any(
        section.scale_offset == -1 and section.raw_samples_px
        for section in saved.sections
    )


def test_selected_section_offset_edit_is_undoable(qtbot, tmp_path: Path) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    section = TraceSection.create_manual(
        (TracePoint(20.0, 20.0), TracePoint(25.0, 40.0))
    )
    service.set_digitized_curve(DigitizedCurve(curve_id, [section]))
    controller = window.manual_digitization_controller
    controller.refresh(selected_curve_id=curve_id)
    controller.start_saved_trace_selection()
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section.section_id,
    )

    panel = window.digitization_panel
    assert panel.scale_offset_spin.isEnabled() is True
    panel.scale_offset_spin.setValue(1)
    assert service.get_digitized_curve(curve_id).sections[0].scale_offset == 1
    assert controller.undo_stack.undoText() == "Change scale offset"

    controller.undo()
    assert service.get_digitized_curve(curve_id).sections[0].scale_offset == 0
    controller.redo()
    assert service.get_digitized_curve(curve_id).sections[0].scale_offset == 1


def test_cursor_uses_saved_current_and_active_draft_offsets(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id = make_window(qtbot, tmp_path)
    backup = TraceSection.create_manual(
        (TracePoint(20.0, 20.0), TracePoint(30.0, 40.0)),
        scale_offset=1,
    )
    service.set_digitized_curve(DigitizedCurve(curve_id, [backup]))
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve_id
    )

    # X=60 maps to 75 API on the normal 0..150 scale. The saved backup
    # section adds one complete width while preserving the image X.
    window._update_coordinates(60.0, 30.0, True)
    assert window.curve_value_status.text() == "GR · +1 scale: 225 API"

    # Outside saved geometry, the control previews the scale of the next
    # section before capture starts.
    panel = window.digitization_panel
    panel.scale_offset_spin.setValue(-1)
    window._update_coordinates(60.0, 70.0, True)
    assert window.curve_value_status.text() == "GR · -1 scale: -75 API"

    # An active draft owns the cursor scale even while crossing a saved range.
    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    window._update_coordinates(60.0, 30.0, True)
    assert window.curve_value_status.text() == "GR · -1 scale: -75 API"
    qtbot.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)


def test_saved_overlay_draws_only_short_different_offset_connectors(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#00B8D9",
    )
    normal = TraceSection.create_manual(
        (TracePoint(95.0, 10.0), TracePoint(108.0, 30.0)),
        scale_offset=0,
    )
    backup = TraceSection.create_manual(
        (TracePoint(12.0, 32.0), TracePoint(20.0, 50.0)),
        scale_offset=1,
    )
    distant_normal = TraceSection.create_manual(
        (TracePoint(100.0, 90.0), TracePoint(105.0, 110.0)),
        scale_offset=0,
    )
    digitized = DigitizedCurve(
        curve.curve_id,
        [normal, backup, distant_normal],
    )

    overlay.set_curves(
        (curve,),
        (digitized,),
        active_curve_id=curve.curve_id,
    )

    assert len(overlay.path_items) == 3
    assert len(overlay.connector_items) == 1
    connector = overlay.connector_items[0]
    assert connector.upper_offset == 0
    assert connector.lower_offset == 1
    assert connector.pen().style() == Qt.PenStyle.DashLine
    assert connector.opacity() < 1.0
