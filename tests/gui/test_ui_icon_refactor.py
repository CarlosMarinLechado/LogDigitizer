from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QToolButton

from logdigitizer.gui.automatic_digitization_panel import AutomaticDigitizationPanel
from logdigitizer.gui.digitization_panel import DigitizationPanel
from logdigitizer.gui.icons import (
    ICON_ACTIVE,
    ICON_DANGER,
    ICON_DISABLED,
    ICON_NORMAL,
)
from logdigitizer.gui.main_window import MainWindow
from logdigitizer.gui.preprocess_panel import PreprocessPanel
from logdigitizer.gui.project_panel import ProjectPanel
from logdigitizer.domain.project import LogProject
from logdigitizer.utils.paths import ProjectPaths


def test_main_toolbar_matches_icon_specification(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    toolbar = window.main_toolbar
    actions = [action for action in toolbar.actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == [
        "New Project",
        "Open Project",
        "Save Project",
        "Quality Control",
        "Export LAS…",
        "Zoom In",
        "Zoom Out",
        "Fit to Window",
        "Pan",
    ]
    assert toolbar.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
    assert all(not action.icon().isNull() for action in actions)

    assert window.add_action not in toolbar.actions()
    assert window.actual_action not in toolbar.actions()

    assert window.new_action.toolTip() == "New Project"
    assert window.open_action.toolTip() == "Open Project"
    assert window.save_action.toolTip() == "Save Project"
    assert window.quality_control_action.toolTip() == (
        "Review digitization continuity and LAS sampling gaps"
    )
    assert window.export_las_action.toolTip() == (
        "Export project curves to LAS 2.0"
    )
    assert window.fit_action.toolTip() == "Fit to Window"
    assert not window.quality_control_action.isEnabled()
    assert not window.export_las_action.isEnabled()

    quality_button = toolbar.widgetForAction(window.quality_control_action)
    assert isinstance(quality_button, QToolButton)
    assert quality_button.toolButtonStyle() is (
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )

    export_button = toolbar.widgetForAction(window.export_las_action)
    assert isinstance(export_button, QToolButton)
    assert export_button.toolButtonStyle() is (
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )

    for action in (
        window.zoom_in_action,
        window.zoom_out_action,
        window.pan_action,
    ):
        button = toolbar.widgetForAction(action)
        assert button is not None
        assert button.toolTip() == ""


def test_file_menu_contains_quality_control_before_export(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    file_menu = window.menuBar().actions()[0].menu()
    assert file_menu is not None
    actions = file_menu.actions()
    assert window.quality_control_action in actions
    assert actions.index(window.quality_control_action) < actions.index(
        window.export_las_action
    )


def test_export_action_tracks_project_export_availability(
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.service.project = LogProject.create(name="P")
    window.service.paths = ProjectPaths(
        root=tmp_path,
        project_file=tmp_path / "P.logproj",
    )
    monkeypatch.setattr(window.service, "can_export_las", lambda: True)

    window._refresh_export_action()

    assert window.quality_control_action.isEnabled()
    assert window.export_las_action.isEnabled()


def test_project_move_controls_are_icon_only(qtbot) -> None:
    panel = ProjectPanel()
    qtbot.addWidget(panel)

    for button in (panel.up_button, panel.down_button):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()
        assert button.toolTip() == ""


def test_reference_grid_readout_is_removed(qtbot) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)

    assert hasattr(panel, "size_label")
    assert panel.size_label.isHidden()
    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(100, 200),
    )
    assert "100 × 200" in panel.size_label.text()


def test_digitization_sections_follow_new_order(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    root = panel.layout()
    assert root.itemAt(0).widget() is panel.curve_group
    assert root.itemAt(1).widget() is panel.automatic_group
    assert root.itemAt(2).widget() is panel.manual_group

    assert panel.curve_group.title() == "Curve"
    assert panel.automatic_group.title() == "Automatic tracing"
    assert panel.manual_group.title() == "Manual digitization"


def test_automatic_primary_actions_use_compact_icons(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    assert isinstance(panel.automatic_start_button, QPushButton)
    assert panel.automatic_start_button.text() == "Start trace"
    assert not panel.automatic_start_button.icon().isNull()
    assert panel.automatic_start_button.toolTip()

    assert isinstance(panel.automatic_finish_button, QPushButton)
    assert panel.automatic_finish_button.text() == "Finish"
    assert not panel.automatic_finish_button.icon().isNull()
    assert panel.automatic_finish_button.toolTip()

    expected_icon_only = {
        panel.automatic_cancel_button: "Clear automatic draft",
        panel.automatic_erase_button: "Erase automatic range",
        panel.automatic_help_button: "Automatic trace help",
    }
    for button, accessible_name in expected_icon_only.items():
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.size().width() == 32
        assert button.size().height() == 32
        assert button.accessibleName() == accessible_name
        assert not button.icon().isNull()
        assert button.toolTip()


def test_advanced_tracker_settings_expand_and_collapse(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    button = panel.automatic_settings_button
    settings = panel.automatic_tracker_settings

    assert isinstance(button, QToolButton)
    assert button.text() == "Advanced tracker settings"
    assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert not button.icon().isNull()
    assert not button.isChecked()
    assert settings.isHidden()
    assert button.toolTip() == "Show advanced tracker settings."

    button.click()
    assert button.isChecked()
    assert not settings.isHidden()
    assert button.toolTip() == "Hide advanced tracker settings."

    button.click()
    assert not button.isChecked()
    assert settings.isHidden()
    assert button.toolTip() == "Show advanced tracker settings."


def test_automatic_help_matches_current_tracker_workflow(
    qtbot,
    monkeypatch,
) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)
    calls: list[tuple[str, str]] = []

    def fake_information(_parent, title, text):
        calls.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)
    panel.automatic_help_button.click()

    assert calls[0][0] == "Automatic trace help"
    assert "click the correct curve at a lower position" in calls[0][1]
    assert "incorrect lower part of the draft is removed" in calls[0][1]
    assert "manual digitization is preserved" in calls[0][1]
    assert "scale offset and tracker settings are locked" in calls[0][1]


def test_automatic_tracker_controls_have_contextual_tooltips(qtbot) -> None:
    panel = AutomaticDigitizationPanel()
    qtbot.addWidget(panel)

    for slider in (
        panel.automatic_sensitivity_slider,
        panel.automatic_search_width_slider,
        panel.automatic_flexibility_slider,
        panel.automatic_gap_tolerance_slider,
    ):
        assert slider.toolTip()

    panel.set_detected_line_width(7.0)
    assert panel.automatic_line_width_label.text() == (
        "Detected width: 7.0 px \u00b7 Automatic"
    )
    assert panel.automatic_line_width_label.toolTip()


def test_automatic_status_and_settings_lock_during_capture(qtbot) -> None:
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

    assert panel.automatic_status_label.text() == "\u25cf Ready to start"
    assert panel.automatic_sensitivity_slider.isEnabled()

    panel.set_automatic_state(True, can_finish=True)

    assert panel.automatic_status_label.text() == "\u25cf Automatic trace active"
    assert not panel.automatic_start_button.isEnabled()
    assert panel.automatic_finish_button.isEnabled()
    assert panel.automatic_cancel_button.isEnabled()
    assert not panel.automatic_sensitivity_slider.isEnabled()
    assert not panel.automatic_search_width_slider.isEnabled()
    assert not panel.automatic_flexibility_slider.isEnabled()
    assert not panel.automatic_gap_tolerance_slider.isEnabled()


def test_curve_selector_and_saved_curve_defaults_match_new_spec(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    assert panel.curve_combo.toolTip() == "Select a curve to digitise"

    assert panel.show_saved_check.isHidden()
    assert panel.dim_inactive_check.isHidden()
    assert panel.show_saved_curves is True
    assert panel.dim_inactive_curves is True
    assert panel.show_saved_check.isChecked()
    assert panel.dim_inactive_check.isChecked()

    # Removed permanent information remains compatibility-only and hidden.
    assert panel.section_count_label.isHidden()
    assert panel.node_count_label.isHidden()
    assert panel.edit_status_label.isHidden()
    assert panel.selected_section_label.isHidden()
    assert panel.selected_node_label.isHidden()


def test_highlight_active_curve_control_is_visible_icon_toggle(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    button = panel.highlight_active_curve_button
    assert isinstance(button, QToolButton)
    assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
    assert button.size().width() == 32
    assert button.size().height() == 32
    assert button.isCheckable()
    assert button.isChecked()
    assert not button.icon().isNull()
    assert "Highlight active curve" in button.toolTip()

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not button.isChecked()
    assert panel.dim_inactive_curves is False
    assert button.toolTip() == "Show all saved curves at full opacity."


def test_manual_primary_actions_have_text_and_auxiliary_actions_are_icon_only(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    expected = {
        panel.start_button: "Start Manual Trace",
        panel.finish_button: "Finish Trace",
        panel.cancel_button: "Cancel Trace",
        panel.start_help_button: "Help",
        panel.edit_button: "Edit Trace",
        panel.done_edit_button: "Done Editing",
        panel.edit_help_button: "Help",
    }

    for button, tooltip in expected.items():
        assert isinstance(button, QToolButton)
        if button in (panel.start_button, panel.edit_button):
            assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            assert button.minimumWidth() >= 112
            assert button.text() == (
                "Start trace" if button is panel.start_button else "Edit trace"
            )
        else:
            assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
            assert button.size().width() == 32
        assert button.size().height() == 32
        assert not button.icon().isNull()
        assert button.toolTip() == tooltip


def test_digitization_edit_tools_are_icon_only(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    assert hasattr(panel, "delete_node_button")
    assert panel.delete_node_button.isHidden()

    for button in (
        panel.undo_button,
        panel.redo_button,
        panel.cut_section_button,
        panel.join_section_button,
        panel.delete_section_button,
    ):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()

    assert panel.undo_button.toolTip() == ""
    assert panel.redo_button.toolTip() == ""
    assert panel.cut_section_button.toolTip()
    assert panel.join_section_button.toolTip()
    assert panel.delete_section_button.toolTip()


def test_cut_and_join_active_modes_keep_icon_only_presentation(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    panel.set_edit_state(
        True,
        selected_section_id="section-001",
        selected_section_node_count=4,
        cutting=True,
    )
    assert panel.cut_section_button.text() == "Cancel Cut"
    assert panel.cut_section_button.toolTip() == "Cancel cut mode."
    assert (
        panel.cut_section_button.toolButtonStyle()
        is Qt.ToolButtonStyle.ToolButtonIconOnly
    )

    panel.set_edit_state(
        True,
        selected_section_id="section-001",
        selected_section_node_count=4,
        joining=True,
    )
    assert panel.join_section_button.text() == "Cancel Join"
    assert panel.join_section_button.toolTip() == "Cancel join mode."
    assert (
        panel.join_section_button.toolButtonStyle()
        is Qt.ToolButtonStyle.ToolButtonIconOnly
    )


def test_manual_help_buttons_open_contextual_help(qtbot, monkeypatch) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)

    calls: list[tuple[str, str]] = []

    def fake_information(_parent, title, text):
        calls.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_information)

    qtbot.mouseClick(panel.start_help_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.edit_help_button, Qt.MouseButton.LeftButton)

    assert calls[0][0] == "Manual trace help"
    assert "Click to add control points" in calls[0][1]
    assert "Click Finish to save the manual trace." in calls[0][1]

    assert calls[1][0] == "Edit trace help"
    assert "Select an existing trace" in calls[1][1]
    assert "Right-click a control node to delete it" in calls[1][1]
    assert "Undo (icon or Ctrl+Z)" in calls[1][1]


def test_icon_palette_is_centralized() -> None:
    assert ICON_NORMAL == "#4A5560"
    assert ICON_ACTIVE == "#1F5F8B"
    assert ICON_DISABLED == "#B8BEC5"
    assert ICON_DANGER == "#C64545"
