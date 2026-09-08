from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.gui.quality_control_dialog import QualityControlDialog


def _service_with_gapped_gr() -> tuple[
    ProjectService,
    CurveDefinition,
    Segment,
]:
    service = ProjectService()
    project = LogProject.create(name="Gap QC", well_name="BH-GAP")
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
    service.add_depth_calibration_point(pixel_y=100.0, depth=10.0)
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
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 40.0),
                    )
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 60.0),
                        TracePoint(25.0, 100.0),
                    )
                ),
            ],
        )
    )
    service.dirty = False
    return service, gr, segment


def test_quality_control_is_step_aware_and_summarizes_curve(qtbot) -> None:
    service, gr, _segment = _service_with_gapped_gr()
    dialog = QualityControlDialog(service)
    qtbot.addWidget(dialog)

    dialog.step_spin.setValue(1.0)
    assert dialog.current_preview is not None
    assert dialog.current_preview.internal_gaps == ()
    assert len(dialog.current_preview.interpolated_gaps) == 1
    assert dialog.summary_table.rowCount() == 1
    assert dialog.summary_table.item(0, 0).text() == "GR"
    assert dialog.summary_table.item(0, 1).text() == "0"
    assert dialog.summary_table.item(0, 2).text() == "1"
    assert dialog.summary_table.item(0, 3).text() == "Interpolate"

    dialog.step_spin.setValue(0.25)
    assert dialog.current_preview is not None
    assert len(dialog.current_preview.internal_gaps) == 1
    assert dialog.current_preview.internal_gaps[0].missing_sample_count == 7
    assert dialog.summary_table.item(0, 1).text() == "1"
    assert dialog.summary_table.item(0, 2).text() == "0"
    assert dialog.summary_table.item(0, 3).text() == "Review"
    assert dialog.issue_table.rowCount() == 1
    assert dialog.issue_table.item(0, 0).text() == "Review"
    assert dialog.issue_table.item(0, 1).text() == gr.mnemonic
    for column in range(dialog.issue_table.columnCount()):
        assert dialog.issue_table.item(0, column).background().color() == QColor(
            "#FFF4CC"
        )


def test_step_is_shared_immediately_with_export_settings(qtbot) -> None:
    service, _gr, _segment = _service_with_gapped_gr()
    dialog = QualityControlDialog(service)
    qtbot.addWidget(dialog)

    dialog.step_spin.setValue(0.25)

    assert service.get_last_las_step() == pytest.approx(0.25)
    assert service.dirty


def test_go_to_interval_emits_curve_segment_and_gap_depth(qtbot) -> None:
    service, gr, segment = _service_with_gapped_gr()
    dialog = QualityControlDialog(service)
    qtbot.addWidget(dialog)
    dialog.step_spin.setValue(0.25)
    dialog.show()

    captured: list[tuple[str, str, float, float, float, bool]] = []
    dialog.navigationRequested.connect(
        lambda curve_id, segment_id, depth, focus_start, focus_stop, tight_focus: captured.append(
            (
                curve_id,
                segment_id,
                depth,
                focus_start,
                focus_stop,
                tight_focus,
            )
        )
    )

    assert dialog.issue_table.rowCount() == 1
    dialog.issue_table.selectRow(0)
    qtbot.mouseClick(
        dialog.primary_navigation_button,
        Qt.MouseButton.LeftButton,
    )

    assert len(captured) == 1
    curve_id, segment_id, depth, focus_start, focus_stop, tight_focus = captured[0]
    assert curve_id == gr.curve_id
    assert segment_id == segment.segment_id
    assert depth == pytest.approx(5.0)
    assert focus_start == pytest.approx(4.0)
    assert focus_stop == pytest.approx(6.0)
    assert tight_focus is False



def test_interpolated_gap_navigation_requests_tight_focus(qtbot) -> None:
    service, gr, segment = _service_with_gapped_gr()
    dialog = QualityControlDialog(service)
    qtbot.addWidget(dialog)

    # STEP=1 gives one missing LAS sample and therefore Interpolate.
    dialog.step_spin.setValue(1.0)
    dialog.show()

    captured: list[tuple[str, str, float, float, float, bool]] = []
    dialog.navigationRequested.connect(
        lambda curve_id, segment_id, depth, focus_start, focus_stop, tight_focus: captured.append(
            (
                curve_id,
                segment_id,
                depth,
                focus_start,
                focus_stop,
                tight_focus,
            )
        )
    )

    assert dialog.issue_table.rowCount() == 1
    assert dialog.issue_table.item(0, 0).text() == "Interpolate"
    dialog.issue_table.selectRow(0)
    qtbot.mouseClick(
        dialog.primary_navigation_button,
        Qt.MouseButton.LeftButton,
    )

    assert len(captured) == 1
    curve_id, segment_id, depth, focus_start, focus_stop, tight_focus = captured[0]
    assert curve_id == gr.curve_id
    assert segment_id == segment.segment_id
    assert depth == pytest.approx(5.0)
    assert focus_start == pytest.approx(4.0)
    assert focus_stop == pytest.approx(6.0)
    assert tight_focus is True



def test_quality_control_step_parser_preserves_decimal_with_unit_suffix(qtbot) -> None:
    service, _gr, _segment = _service_with_gapped_gr()
    dialog = QualityControlDialog(service)
    qtbot.addWidget(dialog)

    dialog.step_spin.setValue(0.25)
    dialog.step_spin.interpretText()

    assert dialog.step_spin.value() == pytest.approx(0.25)
