from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.enhancement_controls import EnhancementControls
from logdigitizer.processing.enhancement import EnhancementSettings


def test_neutral_gui_settings_match_scientific_defaults(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    assert controls.settings == EnhancementSettings()


def test_controls_map_to_scientific_settings(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    controls.black_slider.setValue(25)
    controls.midtones_slider.setValue(75)
    controls.white_slider.setValue(220)

    settings = controls.settings

    assert settings.black_point == 25
    assert settings.midtones == pytest.approx(0.75)
    assert settings.white_point == 220


def test_clahe_controls_are_removed(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    assert not hasattr(controls, "local_contrast_check")
    assert not hasattr(controls, "strength_slider")
    assert not hasattr(controls, "strength_value")


def test_black_and_white_sliders_cannot_cross(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    controls.white_slider.setValue(100)
    assert controls.black_slider.maximum() == 99

    controls.black_slider.setValue(99)
    assert controls.white_slider.minimum() == 100
    assert controls.settings.black_point < controls.settings.white_point


def test_programmatic_settings_restore_all_levels(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    settings = EnhancementSettings(
        black_point=120,
        midtones=1.35,
        white_point=180,
    )
    controls.set_settings(settings)

    assert controls.settings == settings
    assert controls.black_slider.maximum() == 179
    assert controls.white_slider.minimum() == 121


def test_reset_restores_neutral_settings(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    controls.set_settings(
        EnhancementSettings(
            black_point=30,
            midtones=0.70,
            white_point=210,
        )
    )

    controls.reset()

    assert controls.settings == EnhancementSettings()
