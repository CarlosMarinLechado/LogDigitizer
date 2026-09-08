from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from logdigitizer.domain.calibration import CurveDefinition, ScaleType
from logdigitizer.gui.automatic_digitization_panel import AutomaticDigitizationPanel
from logdigitizer.gui.digitization_panel import DigitizationPanel


def _curve() -> CurveDefinition:
    return CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        scale_type=ScaleType.LINEAR,
        display_color="#FF8C00",
    )


def test_digitization_curve_mnemonic_uses_project_curve_color(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)
    curve = _curve()

    panel.set_curves((curve,), curve.curve_id)

    assert panel.curve_mnemonic_label.text() == "SP"
    assert "#FF8C00" in panel.curve_mnemonic_label.styleSheet().upper()
    assert panel.curve_combo.currentText() == "Spontaneous Potential [MV]"
    assert panel.curve_color_preview.styleSheet()


def test_manual_inner_groups_are_borderless_without_redundant_titles(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    assert panel.start_manual_group.title() == ""
    assert panel.edit_manual_group.title() == ""
    assert "border: none" in panel.start_manual_group.styleSheet()
    assert "border: none" in panel.edit_manual_group.styleSheet()


def test_nonzero_scale_offset_is_visually_emphasized(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)
    curve = _curve()
    panel.set_curves((curve,), curve.curve_id)

    panel.set_scale_offset(1)
    assert "#FFF3CD" in panel.scale_offset_spin.styleSheet().upper()

    panel.set_scale_offset(0)
    assert panel.scale_offset_spin.styleSheet() == ""


def test_automatic_status_keeps_established_text_with_semantic_visual_state(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)
    panel.set_context(
        project_available=True,
        segment_available=True,
        image_ready=True,
        readiness_message="Ready",
        saved_section_count=0,
        can_start=True,
    )

    assert panel.automatic_status_label.text() == "● Ready to start"
    assert "#438A5E" in panel.automatic_status_label.styleSheet().upper()


def test_advanced_tracker_settings_control_is_visually_lightweight(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    assert panel.automatic_settings_button.text() == "Advanced tracker settings"
    style = panel.automatic_settings_button.styleSheet()
    assert "border: none" in style
    assert "text-align: left" in style
