from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.gui.las_curve_preview import LasCurvePreviewDialog


def _service_with_two_curves() -> tuple[
    ProjectService,
    CurveDefinition,
    CurveDefinition,
]:
    service = ProjectService()
    project = LogProject.create(name="LAS preview", well_name="BH-01")
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
    )
    project.add_curve(gr)
    project.add_curve(sp)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="image.png",
    )
    project.add_segment(segment)
    service.project = project
    service.active_segment_id = segment.segment_id

    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=10.0)

    for curve, value in ((gr, 25.0), (sp, 70.0)):
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=0.0,
            value=0.0,
        )
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=100.0,
            value=100.0,
        )
        service.set_digitized_curve(
            DigitizedCurve(
                curve_id=curve.curve_id,
                sections=[
                    TraceSection.create_manual(
                        (
                            TracePoint(value, 0.0),
                            TracePoint(value, 100.0),
                        )
                    )
                ],
            )
        )

    service.dirty = False
    return service, gr, sp


def _service_with_six_sample_gap() -> tuple[
    ProjectService,
    CurveDefinition,
]:
    service = ProjectService()
    project = LogProject.create(name="Gap preview", well_name="BH-GAP")
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(gr)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="image.png",
    )
    project.add_segment(segment)
    service.project = project
    service.active_segment_id = segment.segment_id

    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=200.0, depth=20.0)
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=gr.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(40.0, 0.0),
                        TracePoint(40.0, 40.0),  # depth 4
                    )
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(80.0, 110.0),  # depth 11 => NULL 5..10
                        TracePoint(80.0, 200.0),
                    )
                ),
            ],
        )
    )
    service.dirty = False
    return service, gr


def test_preview_uses_final_las_plan_and_can_select_curves(qtbot) -> None:
    service, gr, sp = _service_with_two_curves()
    service.set_last_las_step(1.0)
    service.dirty = False

    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.current_plan is not None
    assert dialog.current_plan.sampled.curve_ids == (
        gr.curve_id,
        sp.curve_id,
    )
    assert dialog.curve_combo.count() == 2
    assert dialog.curve_combo.itemText(0) == "GR"
    assert dialog.curve_combo.itemText(1) == "SP"
    assert "Gamma Ray" in str(
        dialog.curve_combo.itemData(
            0,
            Qt.ItemDataRole.ToolTipRole,
        )
    )
    assert dialog.controls_layout.rowCount() == 2
    assert dialog.minimumWidth() == 640
    assert dialog.samples_value.text() == "11"
    assert dialog.step_value.text() == "1 m"

    dialog.curve_combo.setCurrentIndex(1)

    assert dialog.curve_combo.currentData() == sp.curve_id
    assert dialog.curve_value.text() == "SP (MV)"
    assert dialog.curve_view.segment_count == 1
    assert dialog.curve_view._curve_color.name().lower() == (
        sp.display_color.lower()
    )


def test_null_samples_are_drawn_as_breaks_and_depth_increases_downward(
    qtbot,
) -> None:
    service, gr = _service_with_six_sample_gap()
    service.set_last_las_step(1.0)
    service.dirty = False

    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.current_plan is not None
    values = dialog.current_plan.sampled.values_for(gr.curve_id)
    assert sum(value is None for value in values) == 6
    assert dialog.curve_view.segment_count == 2

    start_y = dialog.curve_view.scene_y_for_depth(
        dialog.current_plan.start_depth
    )
    stop_y = dialog.curve_view.scene_y_for_depth(
        dialog.current_plan.stop_depth
    )
    assert start_y is not None
    assert stop_y is not None
    assert stop_y > start_y


def test_depth_stretch_and_step_changes_are_persisted(qtbot) -> None:
    service, _gr, _sp = _service_with_two_curves()
    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    initial_stretch = dialog.curve_view.depth_stretch
    dialog.stretch_button.click()
    assert dialog.curve_view.depth_stretch > initial_stretch

    dialog.compress_button.click()
    assert dialog.curve_view.depth_stretch == pytest.approx(initial_stretch)

    dialog.step_spin.setValue(0.5)
    assert service.get_last_las_step() == pytest.approx(0.5)
    assert dialog.current_plan is not None
    assert dialog.current_plan.step_depth == pytest.approx(0.5)
