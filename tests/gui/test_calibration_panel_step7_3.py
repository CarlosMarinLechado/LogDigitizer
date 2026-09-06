from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.domain.calibration import (
    CurveDefinition,
    HorizontalCalibrationPoint,
    ScaleType,
)
from logdigitizer.gui.calibration_panel import (
    CalibrationPanel,
    CurveDefinitionDialog,
    HorizontalValueDialog,
)


def test_curve_dialog_uses_catalog_for_resistivity_but_fields_remain_editable(qtbot):
    dialog = CurveDefinitionDialog()
    qtbot.addWidget(dialog)

    index = dialog.parameter_combo.findData("resistivity")
    dialog.parameter_combo.setCurrentIndex(index)

    assert dialog.name_edit.text() == "Electrical Resistivity"
    assert dialog.mnemonic_combo.findText("ILD") >= 0
    assert dialog.unit_combo.findText("OHMM") >= 0
    assert dialog.scale_combo.currentData() == ScaleType.LOGARITHMIC.value
    assert dialog.mnemonic_combo.isEditable()
    assert dialog.unit_combo.isEditable()


def test_curve_dialog_other_allows_manual_definition(qtbot):
    dialog = CurveDefinitionDialog()
    qtbot.addWidget(dialog)

    index = dialog.parameter_combo.findData("other")
    dialog.parameter_combo.setCurrentIndex(index)

    assert dialog.name_edit.text() == ""
    assert dialog.mnemonic_combo.currentText() == ""
    assert dialog.unit_combo.currentText() == ""
    assert dialog.name_edit.isEnabled()


def test_horizontal_value_dialog_has_no_prefill(qtbot):
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    dialog = HorizontalValueDialog(curve)
    qtbot.addWidget(dialog)
    assert dialog.value_edit.text() == ""


def test_panel_shows_global_curve_and_disables_add_x_after_two_points(qtbot):
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_project_available(True)
    panel.set_image_available(True)

    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    panel.set_curves((curve,), curve.curve_id)
    panel.set_horizontal_points(
        (
            HorizontalCalibrationPoint(20.0, 0.0),
            HorizontalCalibrationPoint(220.0, 150.0),
        ),
        curve,
    )

    assert panel.curve_combo.count() == 1
    assert panel.selected_curve_id == curve.curve_id
    assert panel.x_table.rowCount() == 2
    assert panel.x_capture_check.isEnabled() is False
    assert "valid" in panel.x_validation_label.text().lower()
