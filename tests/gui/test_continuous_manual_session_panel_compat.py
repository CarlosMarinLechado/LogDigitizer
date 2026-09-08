"""Regression tests for continuous manual-session panel compatibility."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from logdigitizer.gui.automatic_digitization_panel import AutomaticDigitizationPanel


def test_continuous_manual_state_keeps_automatic_panel_compatible(qtbot) -> None:
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

    assert panel.automatic_start_button.isEnabled()

    panel.set_manual_draft_state(
        True,
        0,
        can_finish_empty_session=True,
    )

    assert panel.tracing is True
    assert panel.finish_button.isEnabled()
    assert not panel.automatic_start_button.isEnabled()

    panel.set_manual_draft_state(False, 0)

    assert panel.tracing is False
    assert panel.finish_button.isEnabled() is False
    assert panel.automatic_start_button.isEnabled()


def test_manual_help_keeps_legacy_finish_wording(qtbot, monkeypatch) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    captured: list[str] = []

    def fake_information(_parent, _title, text):
        captured.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)
    qtbot.mouseClick(panel.start_help_button, Qt.MouseButton.LeftButton)

    assert captured
    assert "Click Finish to save the manual trace." in captured[0]
    assert "Right-click the image to save the current section" in captured[0]
