from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import (
    CurveDefinition,
    ScaleType,
)
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.gui.las_curve_preview import LasCurvePreviewDialog


def _service_with_scale_and_offset_transitions():
    service = ProjectService()
    project = LogProject.create(name="Transition preview")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#FF8C00",
    )
    project.add_curve(curve)
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

    secondary = service.add_horizontal_scale(curve.curve_id)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
        value=0.0,
        scale_id=secondary.scale_id,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=100.0,
        value=1000.0,
        scale_id=secondary.scale_id,
    )

    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 40.0),
                    ),
                    scale_id="default",
                    scale_offset=0,
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 42.0),
                        TracePoint(25.0, 70.0),
                    ),
                    scale_id=secondary.scale_id,
                    scale_offset=0,
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 72.0),
                        TracePoint(25.0, 100.0),
                    ),
                    scale_id=secondary.scale_id,
                    scale_offset=1,
                ),
            ],
        )
    )
    service.dirty = False
    return service, curve, secondary



def _service_with_review_gap() -> tuple[ProjectService, CurveDefinition]:
    service = ProjectService()
    project = LogProject.create(name="Gap review preview")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )
    project.add_curve(curve)
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
                        TracePoint(20.0, 0.0),
                        TracePoint(30.0, 40.0),
                    )
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(70.0, 60.0),
                        TracePoint(80.0, 100.0),
                    )
                ),
            ],
        )
    )
    service.dirty = False
    return service, curve

def test_service_reports_scale_and_offset_seams_without_modifying_curve() -> None:
    service, curve, secondary = _service_with_scale_and_offset_transitions()

    transitions = service.get_las_curve_transform_transitions(curve.curve_id)

    assert len(transitions) == 2

    scale_change = transitions[0]
    assert scale_change.depth == pytest.approx(4.1)
    assert scale_change.scale_changed
    assert not scale_change.offset_changed
    assert scale_change.previous_scale_id == "default"
    assert scale_change.next_scale_id == secondary.scale_id
    assert "0\u2013100 API" in scale_change.label
    assert "0\u20131000 API" in scale_change.label

    offset_change = transitions[1]
    assert offset_change.depth == pytest.approx(7.1)
    assert not offset_change.scale_changed
    assert offset_change.offset_changed
    assert "Offset: 0 \u2192 +1" in offset_change.label

    # Visual-QC metadata must not mutate the persisted digitization.
    digitized = service.get_digitized_curve(curve.curve_id)
    assert digitized is not None
    assert [section.scale_offset for section in digitized.sections] == [0, 0, 1]


def test_las_curve_preview_uses_circle_and_diamond_with_one_master_toggle(
    qtbot,
) -> None:
    service, curve, _secondary = _service_with_scale_and_offset_transitions()
    service.set_last_las_step(0.1)
    service.dirty = False

    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.current_plan is not None
    assert dialog.current_qc_preview is not None
    assert dialog.curve_combo.currentData() == curve.curve_id
    assert dialog.show_qc_markers_check.isChecked()
    assert dialog.qc_legend_label.isVisible() is False or (
        "Offset change" in dialog.qc_legend_label.text()
    )

    assert dialog.curve_view.transition_marker_count == 2
    assert dialog.curve_view.scale_marker_count == 1
    assert dialog.curve_view.offset_marker_count == 1
    assert dialog.curve_view.gap_connector_count == 0

    dialog.show_qc_markers_check.setChecked(False)

    assert dialog.curve_view.transition_marker_count == 0
    assert dialog.curve_view.scale_marker_count == 0
    assert dialog.curve_view.offset_marker_count == 0
    assert dialog.curve_view.gap_connector_count == 0
    assert dialog.qc_legend_label.isHidden()

    dialog.show_qc_markers_check.setChecked(True)

    assert dialog.curve_view.transition_marker_count == 2
    assert dialog.curve_view.scale_marker_count == 1
    assert dialog.curve_view.offset_marker_count == 1


def test_unresolved_gap_is_one_thick_red_connector_but_interpolated_gap_is_hidden(
    qtbot,
) -> None:
    service, curve = _service_with_review_gap()

    # STEP=0.25 leaves seven missing LAS rows: this gap requires review.
    service.set_last_las_step(0.25)
    service.dirty = False
    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.current_qc_preview is not None
    assert len(dialog.current_qc_preview.internal_gaps) == 1
    assert dialog.current_qc_preview.interpolated_gaps == ()
    assert dialog.curve_view.gap_connector_count == 1
    assert len(dialog.curve_view.gap_connector_endpoints) == 1

    start, stop = dialog.curve_view.gap_connector_endpoints[0]
    assert stop.y() > start.y()

    # STEP=1.0 yields one missing LAS row, which is auto-interpolated and
    # therefore intentionally receives no QC marker in the curve preview.
    dialog.step_spin.setValue(1.0)

    assert dialog.current_qc_preview is not None
    assert dialog.current_qc_preview.internal_gaps == ()
    assert len(dialog.current_qc_preview.interpolated_gaps) == 1
    assert dialog.curve_view.gap_connector_count == 0



def test_transition_marker_uses_arrow_cursor_and_double_click_emits_navigation(
    qtbot,
) -> None:
    service, curve, _secondary = _service_with_scale_and_offset_transitions()
    service.set_last_las_step(0.1)
    service.dirty = False

    dialog = LasCurvePreviewDialog(service)
    qtbot.addWidget(dialog)

    assert len(dialog.curve_view.transition_marker_items) == 2
    marker = dialog.curve_view.transition_marker_items[0]
    transition = service.get_las_curve_transform_transitions(
        curve.curve_id
    )[0]

    assert marker.cursor().shape() is Qt.CursorShape.ArrowCursor

    class _DoubleClickEvent:
        def __init__(self) -> None:
            self.accepted = False

        def button(self):
            return Qt.MouseButton.LeftButton

        def accept(self) -> None:
            self.accepted = True

    event = _DoubleClickEvent()
    with qtbot.waitSignal(
        dialog.navigationRequested,
        timeout=1000,
    ) as blocker:
        marker.mouseDoubleClickEvent(event)

    assert blocker.args[0] == curve.curve_id
    assert blocker.args[1] == transition.segment_id
    assert blocker.args[2] == pytest.approx(transition.depth)
    assert event.accepted is True
