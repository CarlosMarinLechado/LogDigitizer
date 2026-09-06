from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QToolButton

from logdigitizer.gui.preprocess_panel import PreprocessPanel


def test_preprocessing_visual_refresh_uses_compact_secondary_actions(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    assert panel.mode_check.text() == "Define 4 corners"

    for button in (panel.clear_button, panel.preview_button):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.size().width() == 32
        assert button.size().height() == 32
        assert not button.icon().isNull()
        assert button.toolTip()

    assert isinstance(panel.apply_button, QPushButton)
    assert panel.apply_button.text() == "Apply Rectification"


def test_enhancement_header_contains_reset_and_help(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    reset = panel.enhancement_controls.reset_button
    assert isinstance(reset, QToolButton)
    assert not reset.icon().isNull()
    assert reset.parent() is not panel.enhancement_controls

    assert isinstance(panel.enhancement_help_button, QToolButton)
    assert not panel.enhancement_help_button.icon().isNull()
    assert panel.apply_enhancement_button.text() == "Apply Enhancement"


def test_visible_enhancement_state_is_neutral_until_settings_change(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage("preprocessed/rectified.png")

    assert panel.rectification_status_label.text() == "✓ Rectification applied"
    assert panel.enhancement_status_label.text() == "No pending enhancement changes"

    panel.enhancement_controls.black_slider.setValue(10)

    assert panel.enhancement_status_label.text() == "⚠ Enhancement changes not applied"
    # Compatibility-only state remains available to the established tests.
    assert "changes not applied" in panel.enhancement_result_label.text()


def test_enhancement_values_are_right_aligned(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    for label in (
        panel.enhancement_controls.black_value,
        panel.enhancement_controls.midtones_value,
        panel.enhancement_controls.white_value,
    ):
        assert label.alignment() & Qt.AlignmentFlag.AlignRight
