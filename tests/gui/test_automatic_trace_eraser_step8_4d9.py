from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.gui.main_window import MainWindow


def make_window(qtbot, tmp_path: Path):
    source = tmp_path / "eraser.png"
    PIL.new("L", (140, 130), 235).save(source)

    service = ProjectService()
    service.new_project(
        name="BH-ERASER",
        project_directory=tmp_path / "project",
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
    service.add_depth_calibration_point(pixel_y=125.0, depth=120.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=130.0,
        value=150.0,
    )

    raw = tuple(
        TracePoint(50.0, float(y_px))
        for y_px in range(15, 116)
    )
    manual = TraceSection.create_manual(
        (
            TracePoint(70.0, 45.0),
            TracePoint(75.0, 55.0),
            TracePoint(68.0, 65.0),
        )
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve.curve_id,
            [TraceSection.create_automatic(raw_samples=raw), manual],
        )
    )
    service.save()

    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(950, 700)
    window.show()
    window._load_active_segment()
    panel = window.digitization_panel
    panel.curve_combo.setCurrentIndex(
        panel.curve_combo.findData(curve.curve_id)
    )
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve.curve_id
    )
    return window, service, curve.curve_id, raw, manual


def viewport_position(window: MainWindow, x_px: float, y_px: float):
    scene_point = window.viewer.image_to_scene(QPointF(x_px, y_px))
    return window.viewer.mapFromScene(scene_point)


def test_drag_eraser_removes_only_auto_range_and_is_undoable(
    qtbot,
    tmp_path: Path,
) -> None:
    window, service, curve_id, raw, original_manual = make_window(
        qtbot,
        tmp_path,
    )
    panel = window.digitization_panel
    controller = window.manual_digitization_controller

    assert panel.automatic_erase_button.isEnabled() is True
    qtbot.mouseClick(
        panel.automatic_erase_button,
        Qt.MouseButton.LeftButton,
    )
    assert controller.erasing_automatic is True
    assert panel.automatic_erase_button.isChecked() is True
    assert panel.curve_combo.isEnabled() is False

    start = viewport_position(window, 50.0, 30.0)
    end = viewport_position(window, 85.0, 75.0)
    qtbot.mousePress(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=start,
    )
    qtbot.mouseMove(window.viewer.viewport(), pos=end)
    selected_range = controller.automatic_trace_eraser.range_y_px
    assert selected_range is not None
    qtbot.mouseRelease(
        window.viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=end,
    )

    erased = service.get_digitized_curve(curve_id)
    assert erased is not None
    automatic_sections = [
        section
        for section in erased.sections
        if section.source is TraceSource.AUTOMATIC
    ]
    manual_sections = [
        section
        for section in erased.sections
        if section.source is TraceSource.MANUAL
    ]
    assert len(automatic_sections) == 2
    assert all(
        point.y_px < selected_range[0]
        for point in automatic_sections[0].raw_samples_px
    )
    assert all(
        point.y_px > selected_range[1]
        for point in automatic_sections[1].raw_samples_px
    )
    assert len(manual_sections) == 1
    assert manual_sections[0].section_id == original_manual.section_id
    assert manual_sections[0].control_points_px == (
        original_manual.control_points_px
    )
    assert controller.undo_stack.undoText() == "Erase automatic range"
    assert controller.automatic_trace_eraser.range_y_px is None

    controller.undo()
    restored = service.get_digitized_curve(curve_id)
    assert restored is not None
    restored_automatic = [
        section
        for section in restored.sections
        if section.source is TraceSource.AUTOMATIC
    ]
    assert len(restored_automatic) == 1
    assert restored_automatic[0].raw_samples_px == raw

    controller.redo()
    redone = service.get_digitized_curve(curve_id)
    assert redone is not None
    assert len(
        [
            section
            for section in redone.sections
            if section.source is TraceSource.AUTOMATIC
        ]
    ) == 2

    qtbot.mouseClick(
        panel.automatic_erase_button,
        Qt.MouseButton.LeftButton,
    )
    assert controller.erasing_automatic is False
    assert panel.curve_combo.isEnabled() is True
