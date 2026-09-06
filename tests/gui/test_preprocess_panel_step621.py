import pytest
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.preprocess_panel import PreprocessPanel


def test_valid_geometry_does_not_enable_enhancement_before_apply(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_rectification_stage()
    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(100, 200),
    )

    assert panel.apply_button.isEnabled()
    assert not panel.enhancement_group.isEnabled()


def test_enhancement_stage_enables_live_controls(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage("preprocessed/log_rectified.png")

    assert panel.enhancement_group.isEnabled()
    assert not panel.mode_check.isEnabled()


def test_separate_enhancement_preview_button_is_removed(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "enhancement_preview_button")
