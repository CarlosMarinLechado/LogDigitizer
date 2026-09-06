from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QLabel

from logdigitizer.gui.automatic_digitization_panel import AutomaticDigitizationPanel


def test_default_tracker_controls_use_manual_trial_starting_values(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    settings = panel.automatic_trace_settings

    assert panel.automatic_sensitivity_slider.value() == 50
    assert panel.automatic_search_width_slider.value() == 50
    assert panel.automatic_flexibility_slider.value() == 50
    assert panel.automatic_gap_tolerance_slider.value() == 50
    assert settings.search_half_width_px == 28
    assert settings.max_horizontal_step_px == pytest.approx(10.0)
    assert settings.max_consecutive_misses == 2
    assert settings.min_contrast_ratio == pytest.approx(0.07)
    assert settings.min_confidence == pytest.approx(0.43)


def test_simple_controls_change_only_expected_user_preferences(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    panel.automatic_sensitivity_slider.setValue(100)
    panel.automatic_search_width_slider.setValue(100)
    panel.automatic_flexibility_slider.setValue(100)
    panel.automatic_gap_tolerance_slider.setValue(100)

    settings = panel.automatic_trace_settings

    assert settings.search_half_width_px == 56
    assert settings.max_horizontal_step_px == pytest.approx(20.0)
    assert settings.max_consecutive_misses == 4
    assert settings.min_contrast_ratio == pytest.approx(0.03)
    assert settings.min_confidence == pytest.approx(0.33)


def test_search_and_flexibility_can_move_below_medium(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    panel.automatic_search_width_slider.setValue(0)
    panel.automatic_flexibility_slider.setValue(0)

    settings = panel.automatic_trace_settings

    assert settings.search_half_width_px == 14
    assert settings.max_horizontal_step_px == pytest.approx(5.0)


def test_high_sensitivity_accepts_fainter_curves_than_low_sensitivity(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    panel.automatic_sensitivity_slider.setValue(0)
    low = panel.automatic_trace_settings

    panel.automatic_sensitivity_slider.setValue(100)
    high = panel.automatic_trace_settings

    assert low.min_contrast_ratio == pytest.approx(0.11)
    assert low.min_confidence == pytest.approx(0.53)
    assert high.min_contrast_ratio == pytest.approx(0.03)
    assert high.min_confidence == pytest.approx(0.33)
    assert high.min_contrast_ratio < low.min_contrast_ratio
    assert high.min_confidence < low.min_confidence


def test_tracker_controls_are_locked_while_automatic_draft_is_active(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    panel.set_automatic_state(True)

    assert panel.automatic_sensitivity_slider.isEnabled() is False
    assert panel.automatic_search_width_slider.isEnabled() is False
    assert panel.automatic_flexibility_slider.isEnabled() is False
    assert panel.automatic_gap_tolerance_slider.isEnabled() is False

    panel.set_automatic_state(False)

    assert panel.automatic_sensitivity_slider.isEnabled() is True


def test_line_style_selector_is_not_exposed(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    assert not hasattr(panel, "automatic_line_style_combo")
    assert all(
        "line style" not in label.text().lower()
        for label in panel.findChildren(QLabel)
    )


def test_automatic_eraser_is_checkable_and_locks_tracker_settings(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)
    panel.set_context(
        project_available=True,
        segment_available=True,
        image_ready=True,
        readiness_message="Ready",
        saved_section_count=1,
        can_start=True,
        can_edit=True,
    )

    assert panel.automatic_erase_button.isEnabled() is True
    panel.set_automatic_erase_state(True)

    assert panel.automatic_erase_active is True
    assert panel.automatic_erase_button.isChecked() is True
    assert panel.automatic_start_button.isEnabled() is False
    assert panel.automatic_search_width_slider.isEnabled() is False
    assert panel.start_button.isEnabled() is False
    assert panel.edit_button.isEnabled() is False

    panel.set_automatic_erase_state(False)

    assert panel.automatic_erase_button.isChecked() is False
    assert panel.automatic_start_button.isEnabled() is True
    assert panel.automatic_search_width_slider.isEnabled() is True


def test_detected_line_width_is_informational(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    panel.set_detected_line_width(4.25)
    assert "4.2 px" in panel.automatic_line_width_label.text()

    panel.set_detected_line_width(None)
    assert "automatically" in panel.automatic_line_width_label.text()
