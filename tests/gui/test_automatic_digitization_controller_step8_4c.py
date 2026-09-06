from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsView

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
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
    return service, curve.curve_id


def click_image(qtbot, window: MainWindow, x: float, y: float) -> None:
    scene_point = window.viewer.image_to_scene(QPointF(x, y))
    viewport_point = window.viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


def test_stop_click_resume_keeps_one_transient_raw_trace(qtbot, tmp_path: Path) -> None:
    service, curve_id = make_service(tmp_path)
    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(900, 650)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    panel = window.digitization_panel
    index = panel.curve_combo.findData(curve_id)
    panel.curve_combo.setCurrentIndex(index)
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    assert panel.automatic_start_button.isEnabled()
    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)

    click_image(qtbot, window, 39.0, 5.0)
    controller = window.automatic_digitization_controller
    assert controller.waiting_for_resume is True
    assert (
        window.viewer.viewport().cursor().shape()
        == Qt.CursorShape.CrossCursor
    )
    assert window.viewer.dragMode() == QGraphicsView.DragMode.NoDrag
    before = controller.raw_samples_px

    click_image(qtbot, window, 49.0, 55.0)
    assert controller.complete is True
    assert controller.raw_samples_px[: len(before)] == before
    assert len(controller.raw_samples_px) > len(before)
    assert any(
        point.y_px == pytest.approx(50.0, abs=1.0)
        for point in controller.raw_samples_px
    )
    assert controller.last_correction_point_px is not None
    assert controller.last_correction_point_px.y_px == pytest.approx(
        55.0,
        abs=1.0,
    )

    # A completed draft keeps canvas capture active so an earlier click can
    # replace a deviated provisional tail before Finish.
    click_image(qtbot, window, 49.0, 80.0)
    assert controller.complete is True
    assert controller.last_correction_point_px is not None
    assert controller.last_splice_point_px is not None
    assert controller.last_splice_point_px.y_px < 80.0
    assert controller.last_correction_point_px.y_px == pytest.approx(
        80.0,
        abs=1.0,
    )

    # Step 8.4C intentionally remains transient: persistence comes after
    # smoothing/simplification has produced editable control points.
    assert service.get_digitized_curve(curve_id) is None


def test_clear_automatic_draft_restores_inactive_state(qtbot, tmp_path: Path) -> None:
    service, curve_id = make_service(tmp_path)
    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)

    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(panel.curve_combo.findData(curve_id))
    window.manual_digitization_controller.refresh(selected_curve_id=curve_id)
    qtbot.mouseClick(panel.automatic_start_button, Qt.MouseButton.LeftButton)

    assert window.automatic_digitization_controller.active is True
    qtbot.mouseClick(panel.automatic_cancel_button, Qt.MouseButton.LeftButton)

    assert window.automatic_digitization_controller.active is False
    assert window.automatic_digitization_controller.raw_samples_px == ()
