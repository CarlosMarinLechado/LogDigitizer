from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QToolButton

from logdigitizer.domain.calibration import (
    CurveDefinition,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibrationPoint,
    ScaleType,
)
from logdigitizer.gui.calibration_panel import (
    CalibrationPanel,
    CurveColorComboBox,
)


def _curve() -> CurveDefinition:
    return CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )


def test_curve_mnemonic_uses_curve_display_color_in_calibration_ui(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    curve = _curve()

    panel.set_project_available(True)
    panel.set_curves((curve,), curve.curve_id)

    assert isinstance(panel.curve_combo, CurveColorComboBox)
    assert panel.curve_combo.currentData(
        CurveColorComboBox.CURVE_MNEMONIC_ROLE
    ) == "GR"
    assert panel.curve_combo.currentData(
        CurveColorComboBox.CURVE_COLOR_ROLE
    ) == "#00B8D9"

    # Keep the established plain-text compatibility API while the visible
    # mnemonic is rendered separately in the project curve color.
    assert panel.curve_details_label.text() == "GR · API · Linear"
    assert panel.curve_mnemonic_label.text() == "GR"
    assert "#00B8D9" in panel.curve_mnemonic_label.styleSheet().upper()
    assert panel.curve_metadata_label.text() == " · API · Linear"


def test_calibration_edit_delete_clear_actions_are_compact_icon_buttons(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    buttons = (
        panel.edit_button,
        panel.delete_button,
        panel.clear_button,
        panel.x_edit_button,
        panel.x_delete_button,
        panel.x_clear_button,
    )
    for button in buttons:
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.width() == 30
        assert button.height() == 30
        assert not button.icon().isNull()
        assert button.toolTip()

    assert panel.edit_button.icon().cacheKey() != panel.delete_button.icon().cacheKey()
    assert panel.delete_button.icon().cacheKey() != panel.clear_button.icon().cacheKey()


def test_point_capture_controls_keep_explicit_text(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    assert isinstance(panel.capture_check, QCheckBox)
    assert isinstance(panel.x_capture_check, QCheckBox)
    assert panel.capture_check.text() == "Add Y Point"
    assert panel.x_capture_check.text() == "Add X Point"


def test_valid_calibration_status_keeps_established_text_contract(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    curve = _curve()

    panel.set_image_available(True)
    panel.set_points(
        (
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(300.0, 100.0),
        ),
        DepthUnit.METERS,
    )
    assert panel.validation_label.text() == "✓ Y calibration valid (2 points)"

    panel.set_horizontal_points(
        (
            HorizontalCalibrationPoint(50.0, 0.0),
            HorizontalCalibrationPoint(250.0, 100.0),
        ),
        curve,
        curve_present=True,
    )
    assert panel.x_validation_label.text() == (
        "✓ GR X calibration valid (2 points)"
    )
