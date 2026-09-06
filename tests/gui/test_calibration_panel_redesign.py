from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QToolButton

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
        description="Natural gamma-ray curve.",
    )


def test_calibration_panel_removes_permanent_help_and_legacy_qc(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    assert panel.instructions.isHidden()
    assert panel.curve_help.isHidden()
    assert panel.qc_group.isHidden()


def test_curve_management_is_compact_icon_only(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    for button in (
        panel.add_curve_button,
        panel.edit_curve_button,
        panel.remove_curve_button,
        panel.curve_help_button,
        panel.depth_help_button,
    ):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()
        assert button.toolTip()


def test_depth_status_is_compact_and_semantic(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    panel.set_image_available(True)
    assert panel.availability_label.text() == "✓ Rectified image ready"

    panel.set_points(
        (
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(300.0, 200.0),
        ),
        DepthUnit.METERS,
    )
    assert panel.validation_label.text() == "✓ Y calibration valid (2 points)"


def test_curve_metadata_is_single_compact_line(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    curve = make_curve()
    panel.set_project_available(True)
    panel.set_curves((curve,), curve.curve_id)

    assert panel.curve_details_label.text() == "GR · API · Linear"
    assert "\n" not in panel.curve_details_label.text()
    assert "Natural gamma-ray curve." in panel.curve_combo.toolTip()


def test_integrated_x_status_replaces_visible_qc_summary(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    curve = make_curve()
    panel.set_project_available(True)
    panel.set_image_available(True)
    panel.set_curves((curve,), curve.curve_id)
    panel.set_curve_presence(True, segment_available=True)
    panel.set_horizontal_points((), curve)

    panel.set_calibration_check(
        depth_valid=True,
        curve=curve,
        readiness=CurveCalibrationReadiness.X_NOT_CALIBRATED,
        depth_point_count=2,
        x_point_count=0,
    )
    assert panel.x_validation_label.text() == (
        "⚠ Calibrate GR before digitization"
    )

    panel.set_horizontal_points(
        (
            HorizontalCalibrationPoint(20.0, 0.0),
            HorizontalCalibrationPoint(220.0, 150.0),
        ),
        curve,
    )
    panel.set_calibration_check(
        depth_valid=True,
        curve=curve,
        readiness=CurveCalibrationReadiness.READY,
        depth_point_count=2,
        x_point_count=2,
    )
    assert panel.x_validation_label.text() == (
        "✓ GR X calibration valid (2 points)"
    )


def test_help_buttons_open_contextual_dialogs(qtbot, monkeypatch) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    calls: list[tuple[str, str]] = []

    def fake_information(_parent, title, text):
        calls.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)

    qtbot.mouseClick(panel.depth_help_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.curve_help_button, Qt.MouseButton.LeftButton)

    assert calls[0][0] == "Depth calibration (Y)"
    assert "Only pixel Y is used" in calls[0][1]
    assert calls[1][0] == "Curve calibration (X)"
    assert "X calibration is independent for each segment" in calls[1][1]
