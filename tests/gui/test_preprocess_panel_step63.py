from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.preprocess_panel import PreprocessPanel
from logdigitizer.processing.enhancement import EnhancementSettings


def test_apply_enhancement_button_exists_and_emits(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage("preprocessed/rectified.png")

    with qtbot.waitSignal(
        panel.enhancementApplyRequested,
        timeout=1000,
    ):
        panel.apply_enhancement_button.click()


def test_enhancement_stage_restores_saved_working_path(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)

    panel.enter_enhancement_stage(
        "preprocessed/rectified.png",
        "preprocessed/working.png",
    )

    assert panel.enhancement_group.isEnabled()
    assert "working.png" in panel.enhancement_result_label.text()


def test_live_change_marks_working_image_as_pending(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage(
        "preprocessed/rectified.png",
        "preprocessed/working.png",
    )

    panel.enhancement_controls.black_slider.setValue(10)

    assert "changes not applied" in panel.enhancement_result_label.text()


def test_programmatic_restore_does_not_mark_pending(qtbot):
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage(
        "preprocessed/rectified.png",
        "preprocessed/working.png",
    )

    panel.set_enhancement_settings(
        EnhancementSettings(
            black_point=20,
            midtones=0.85,
            white_point=225,
        )
    )

    assert "working.png" in panel.enhancement_result_label.text()
