from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QToolButton

from logdigitizer.gui.enhancement_controls import EnhancementControls
from logdigitizer.gui.preprocess_panel import PreprocessPanel


def test_preprocessing_removes_permanent_explanatory_text_and_paths(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    assert panel.geometry_group.title() == "Rectification"
    assert panel.instructions.isHidden()
    assert panel.points_label.isHidden()
    assert panel.geometry_label.isHidden()
    assert panel.result_label.isHidden()

    assert panel.help_label.isHidden()
    assert panel.live_label.isHidden()
    assert panel.enhancement_result_label.isHidden()


def test_rectification_status_is_single_compact_line(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)

    panel.set_selection_state(point_count=0, is_valid=None)
    assert panel.rectification_status_label.text() == "⚠ Add 4 corner points"

    panel.set_selection_state(point_count=3, is_valid=None)
    assert panel.rectification_status_label.text() == "⚠ 3 / 4 corners defined"

    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(200, 500),
    )
    assert panel.rectification_status_label.text() == (
        "✓ Rectification geometry valid"
    )


def test_multipoint_controls_use_progressive_disclosure(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_rectification_stage()
    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(200, 500),
    )

    assert panel.advanced_widget.isHidden()

    panel.multipoint_check.setChecked(True)
    assert not panel.advanced_widget.isHidden()

    panel.multipoint_check.setChecked(False)
    assert panel.advanced_widget.isHidden()


def test_help_actions_are_compact_icon_buttons(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    for button in (
        panel.rectification_help_button,
        panel.multipoint_help_button,
        panel.enhancement_help_button,
    ):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()
        assert button.toolTip()

    assert panel.multipoint_help_button.toolTip() == (
        "How to use multipoint correction"
    )


def test_multipoint_help_explains_left_then_right_capture(qtbot, monkeypatch) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    calls: list[tuple[str, str]] = []

    def fake_information(_parent, title, text):
        calls.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)

    assert panel.multipoint_help_button.isEnabled()

    qtbot.mouseClick(
        panel.multipoint_help_button,
        Qt.MouseButton.LeftButton,
    )

    assert calls
    title, text = calls[0]
    assert title == "Multipoint correction"
    assert "LEFT boundary first" in text
    assert "RIGHT boundary" in text
    assert "Remove last pair" in text
    assert "Preview the result" in text


def test_rectified_and_working_paths_are_not_shown_in_visible_status(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)

    panel.enter_enhancement_stage(
        "preprocessed/rectified.png",
        "preprocessed/working.png",
    )

    assert panel.rectification_status_label.text() == "✓ Rectification applied"
    assert panel.enhancement_status_label.text() == "✓ Enhancement applied"

    # Compatibility labels retain state internally but stay hidden.
    assert "rectified.png" in panel.result_label.text()
    assert panel.result_label.isHidden()
    assert "working.png" in panel.enhancement_result_label.text()
    assert panel.enhancement_result_label.isHidden()


def test_enhancement_pending_uses_compact_visible_status(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)
    panel.enter_enhancement_stage(
        "preprocessed/rectified.png",
        "preprocessed/working.png",
    )

    panel.enhancement_controls.black_slider.setValue(10)

    assert panel.enhancement_status_label.text() == (
        "⚠ Enhancement changes not applied"
    )


def test_reset_is_icon_only_with_tooltip(qtbot) -> None:
    controls = EnhancementControls()
    qtbot.addWidget(controls)

    assert isinstance(controls.reset_button, QToolButton)
    assert controls.reset_button.toolButtonStyle() is (
        Qt.ToolButtonStyle.ToolButtonIconOnly
    )
    assert not controls.reset_button.icon().isNull()
    assert controls.reset_button.toolTip() == "Reset image enhancement"
