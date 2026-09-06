from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.domain.calibration import (
    DepthCalibrationPoint,
    DepthUnit,
)
from logdigitizer.gui.calibration_panel import (
    CalibrationPanel,
    DepthValueDialog,
)


def test_add_dialog_has_no_intelligent_depth_prefill(qtbot) -> None:
    dialog = DepthValueDialog(DepthUnit.METERS)
    qtbot.addWidget(dialog)
    assert dialog.depth_edit.text() == ""


def test_panel_disables_unit_change_after_first_point(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.set_points(
        (DepthCalibrationPoint(100.0, 200.0),),
        DepthUnit.METERS,
    )

    assert panel.table.rowCount() == 1
    assert panel.unit_combo.isEnabled() is False
    assert "at least one more" in panel.validation_label.text()


def test_panel_reports_valid_y_calibration_from_two_points(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.set_points(
        (
            DepthCalibrationPoint(100.0, 200.0),
            DepthCalibrationPoint(300.0, 220.0),
        ),
        DepthUnit.METERS,
    )

    assert panel.table.rowCount() == 2
    assert "valid" in panel.validation_label.text().lower()
