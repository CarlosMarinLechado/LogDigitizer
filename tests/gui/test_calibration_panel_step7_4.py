from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from logdigitizer.domain.calibration import (
    CurveDefinition,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibrationPoint,
    ScaleType,
)
from logdigitizer.domain.states import CurveCalibrationReadiness
from logdigitizer.gui.calibration_panel import CalibrationPanel


def make_curve():
    return CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )


def test_depth_table_is_compact_and_keeps_vertical_scroll(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    points = tuple(
        DepthCalibrationPoint(100.0 * index, 20.0 * index)
        for index in range(1, 9)
    )
    panel.set_points(points, DepthUnit.METERS)

    assert panel.table.rowCount() == 8
    assert panel.table.maximumHeight() == panel.DEPTH_TABLE_MAX_HEIGHT
    assert (
        panel.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )


def test_x_table_is_fixed_compact_for_two_points(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_project_available(True)
    panel.set_image_available(True)
    curve = make_curve()
    panel.set_curves((curve,), curve.curve_id)
    panel.set_curve_presence(True, segment_available=True)
    panel.set_horizontal_points(
        (
            HorizontalCalibrationPoint(20.0, 0.0),
            HorizontalCalibrationPoint(220.0, 150.0),
        ),
        curve,
    )

    assert panel.x_table.rowCount() == 2
    assert panel.x_table.maximumHeight() == panel.X_TABLE_MAX_HEIGHT
    assert panel.x_table.maximumHeight() < panel.table.maximumHeight()


def test_absent_curve_disables_x_work_without_deleting_underlying_state(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_project_available(True)
    panel.set_image_available(True)
    curve = make_curve()
    points = (
        HorizontalCalibrationPoint(20.0, 0.0),
        HorizontalCalibrationPoint(220.0, 150.0),
    )
    panel.set_curves((curve,), curve.curve_id)
    panel.set_curve_presence(False, segment_available=True)
    panel.set_horizontal_points(points, curve, curve_present=False)

    assert panel.curve_present_check.isChecked() is False
    assert panel.x_table.rowCount() == 0
    assert panel.x_capture_check.isEnabled() is False
    assert "not present" in panel.x_validation_label.text().lower()
    assert "not required" in panel.x_validation_label.text().lower()


def test_qc_summary_reports_ready_missing_and_not_present(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    curve = make_curve()

    panel.set_calibration_check(
        depth_valid=True,
        curve=curve,
        readiness=CurveCalibrationReadiness.READY,
        depth_point_count=4,
        x_point_count=2,
    )
    assert "✓ Valid" in panel.qc_depth_label.text()
    assert "✓ Valid" in panel.qc_curve_label.text()
    assert "Ready for digitization" in panel.qc_ready_label.text()

    panel.set_calibration_check(
        depth_valid=True,
        curve=curve,
        readiness=CurveCalibrationReadiness.X_NOT_CALIBRATED,
        depth_point_count=4,
        x_point_count=1,
    )
    assert "1/2" in panel.qc_curve_label.text()
    assert "Calibrate GR" in panel.qc_ready_label.text()

    panel.set_calibration_check(
        depth_valid=True,
        curve=curve,
        readiness=CurveCalibrationReadiness.NOT_PRESENT,
        depth_point_count=4,
        x_point_count=2,
    )
    assert "Not present" in panel.qc_curve_label.text()
    assert "No calibration required" in panel.qc_ready_label.text()


def test_presence_toggle_emits_curve_specific_signal(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_project_available(True)
    curve = make_curve()
    panel.set_curves((curve,), curve.curve_id)
    panel.set_curve_presence(True, segment_available=True)

    with qtbot.waitSignal(panel.curvePresenceChanged, timeout=1000) as blocker:
        panel.curve_present_check.setChecked(False)

    curve_id, present = blocker.args
    assert curve_id == curve.curve_id
    assert present is False
