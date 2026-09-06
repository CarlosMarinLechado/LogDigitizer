from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from logdigitizer.gui.calibration_panel import CalibrationPanel


def test_curve_presence_uses_image_wording(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)

    assert panel.curve_present_check.text() == "Curve present in this image"
    assert "current image" in panel.curve_present_check.toolTip().lower()
    assert "segment" not in panel.curve_present_check.text().lower()
