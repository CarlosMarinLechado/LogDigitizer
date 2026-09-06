from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.main_window import MainWindow


def test_add_control_pair_button_arms_one_pair_capture(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.preprocess_panel.set_image_available(True)
    window.viewer.set_rectification_points(
        [
            (20.0, 20.0),
            (220.0, 25.0),
            (215.0, 160.0),
            (25.0, 165.0),
        ]
    )
    window.preprocess_panel.multipoint_check.setChecked(True)

    assert window.preprocess_panel.add_control_pair_button.isEnabled()
    window.preprocess_panel.add_control_pair_button.click()

    assert window.preprocess_panel.mode_check.isChecked()
    assert window.viewer.rectification_control_pair_capture_enabled


def test_preview_selects_multipoint_algorithm_when_advanced_mode_is_checked(
    qtbot,
    monkeypatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.preprocess_panel.set_image_available(True)
    window.viewer.set_rectification_points(
        [
            (20.0, 20.0),
            (220.0, 25.0),
            (215.0, 160.0),
            (25.0, 165.0),
        ]
    )
    window.viewer.rectification.add_control_pair_point(30.0, 90.0)
    window.viewer.rectification.add_control_pair_point(210.0, 92.0)
    window.preprocess_panel.multipoint_check.setChecked(True)

    calls: list[bool] = []

    def fake_preview(*, multipoint: bool = False):
        calls.append(multipoint)
        raise ValueError("stop-before-dialog")

    monkeypatch.setattr(window.viewer, "compute_rectification_preview", fake_preview)
    monkeypatch.setattr(window, "_show_error", lambda *_args: None)

    window.preview_rectification()

    assert calls == [True]
