from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.preprocess_panel import PreprocessPanel


def ready_panel(qtbot) -> PreprocessPanel:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_rectification_stage()
    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(200, 500),
    )
    return panel


def test_multipoint_mode_is_available_only_after_valid_outer_corners(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)

    assert not panel.multipoint_check.isEnabled()

    panel.set_selection_state(point_count=4, is_valid=True, estimated_size=(200, 500))

    assert panel.multipoint_check.isEnabled()
    assert panel.preview_button.isEnabled()
    assert panel.apply_button.isEnabled()


def test_advanced_mode_requires_at_least_one_valid_intermediate_pair(qtbot) -> None:
    panel = ready_panel(qtbot)
    panel.multipoint_check.setChecked(True)

    assert panel.add_control_pair_button.isEnabled()
    assert not panel.preview_button.isEnabled()
    assert not panel.apply_button.isEnabled()

    panel.set_multipoint_state(control_pair_count=1, is_valid=True)

    assert panel.remove_control_pair_button.isEnabled()
    assert panel.preview_button.isEnabled()
    assert panel.apply_button.isEnabled()
    assert "1" in panel.control_pairs_label.text()
    assert "valid" in panel.control_pairs_label.text()


def test_turning_advanced_mode_off_keeps_standard_four_point_workflow_ready(qtbot) -> None:
    panel = ready_panel(qtbot)
    panel.multipoint_check.setChecked(True)
    panel.set_multipoint_state(control_pair_count=1, is_valid=True)
    panel.multipoint_check.setChecked(False)

    assert panel.preview_button.isEnabled()
    assert panel.apply_button.isEnabled()
    assert not panel.add_control_pair_button.isEnabled()


def test_add_and_remove_pair_buttons_emit_requests(qtbot) -> None:
    panel = ready_panel(qtbot)
    panel.multipoint_check.setChecked(True)
    panel.set_multipoint_state(control_pair_count=1, is_valid=True)

    with qtbot.waitSignal(panel.addControlPairRequested, timeout=1000):
        panel.add_control_pair_button.click()

    with qtbot.waitSignal(panel.removeControlPairRequested, timeout=1000):
        panel.remove_control_pair_button.click()
