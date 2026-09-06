from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.domain.calibration import CurveDefinition, ScaleType
from logdigitizer.domain.curve_display import CURVE_DISPLAY_PALETTE
from logdigitizer.gui.calibration_panel import CurveDefinitionDialog


def test_curve_dialog_offers_exactly_five_display_colors(qtbot) -> None:
    dialog = CurveDefinitionDialog(suggested_display_color="#FF8C00")
    qtbot.addWidget(dialog)

    assert dialog.color_combo.count() == 5
    assert dialog.color_combo.currentData() == "#FF8C00"
    assert {
        dialog.color_combo.itemData(index)
        for index in range(dialog.color_combo.count())
    } == {item.hex_color for item in CURVE_DISPLAY_PALETTE}


def test_edit_curve_dialog_restores_existing_display_color(qtbot) -> None:
    curve = CurveDefinition.create(
        name="Resistivity",
        mnemonic="RES",
        unit="OHMM",
        scale_type=ScaleType.LOGARITHMIC,
        display_color="#D946EF",
    )
    dialog = CurveDefinitionDialog(initial_curve=curve)
    qtbot.addWidget(dialog)

    assert dialog.color_combo.currentData() == "#D946EF"
