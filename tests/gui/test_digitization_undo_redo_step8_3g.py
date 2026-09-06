from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.gui.main_window import MainWindow


def make_window_with_sections(
    qtbot,
    tmp_path: Path,
    sections: list[TraceSection],
):
    source = tmp_path / "scan.png"
    PIL.new("L", (280, 240), 225).save(source)

    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
    )
    segment = service.add_images([source])[0]
    segment.extensions["preprocess"] = {"derived_path": segment.source_path}

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )
    service.add_depth_calibration_point(pixel_y=10.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=230.0, depth=220.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=270.0,
        value=150.0,
    )
    service.set_digitized_curve(DigitizedCurve(curve.curve_id, sections))
    service.save()

    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(1000, 720)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve.curve_id
    )
    return window, service, curve.curve_id, segment.segment_id


def enter_editing_and_select_section(
    qtbot,
    window: MainWindow,
    curve_id: str,
    section_id: str,
) -> None:
    qtbot.mouseClick(
        window.digitization_panel.edit_button,
        Qt.MouseButton.LeftButton,
    )
    window.viewer.saved_digitized_curves.request_section_selection(
        curve_id,
        section_id,
    )
    assert window.manual_digitization_controller.selected_section_id == section_id


def manual_section() -> TraceSection:
    return TraceSection.create_manual(
        (
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            TracePoint(60.0, 85.0),
            TracePoint(78.0, 115.0),
        )
    )


def test_move_node_undo_redo_preserves_automatic_raw_trace(
    qtbot,
    tmp_path: Path,
) -> None:
    raw = (
        TracePoint(50.0, 25.0),
        TracePoint(60.0, 40.0),
        TracePoint(70.0, 55.0),
        TracePoint(64.0, 85.0),
        TracePoint(78.0, 115.0),
    )
    section = TraceSection.create_automatic(
        control_points=(
            TracePoint(50.0, 25.0),
            TracePoint(70.0, 55.0),
            TracePoint(78.0, 115.0),
        ),
        raw_samples=raw,
        smoothing_strength=0.35,
        simplification_tolerance_px=1.25,
    )
    window, service, curve_id, _ = make_window_with_sections(
        qtbot, tmp_path, [section]
    )
    enter_editing_and_select_section(
        qtbot, window, curve_id, section.section_id
    )

    controller = window.manual_digitization_controller
    controller._saved_node_move_committed(
        curve_id,
        section.section_id,
        1,
        82.0,
        62.0,
    )

    moved = service.get_digitized_curve(curve_id).get_section(section.section_id)
    assert moved.control_points_px[1] == TracePoint(82.0, 62.0)
    assert moved.raw_samples_px == raw
    assert controller.undo_stack.canUndo() is True
    assert "Move node" in window.digitization_panel.undo_button.text()

    qtbot.mouseClick(
        window.digitization_panel.undo_button,
        Qt.MouseButton.LeftButton,
    )
    restored = service.get_digitized_curve(curve_id).get_section(section.section_id)
    assert restored.control_points_px[1] == TracePoint(70.0, 55.0)
    assert restored.raw_samples_px == raw
    assert restored.smoothing_strength == pytest.approx(0.35)
    assert restored.simplification_tolerance_px == pytest.approx(1.25)
    assert controller.undo_stack.canRedo() is True

    qtbot.mouseClick(
        window.digitization_panel.redo_button,
        Qt.MouseButton.LeftButton,
    )
    redone = service.get_digitized_curve(curve_id).get_section(section.section_id)
    assert redone.control_points_px[1] == TracePoint(82.0, 62.0)
    assert redone.raw_samples_px == raw


def test_insert_and_delete_node_are_independent_undo_commands(
    qtbot,
    tmp_path: Path,
) -> None:
    section = manual_section()
    window, service, curve_id, _ = make_window_with_sections(
        qtbot, tmp_path, [section]
    )
    enter_editing_and_select_section(
        qtbot, window, curve_id, section.section_id
    )
    controller = window.manual_digitization_controller

    controller._saved_node_insert_requested(
        curve_id,
        section.section_id,
        65.0,
        70.0,
    )
    inserted = service.get_digitized_curve(curve_id).get_section(section.section_id)
    assert len(inserted.control_points_px) == 5
    assert controller.selected_node_index == 2

    controller.delete_selected_node()
    deleted = service.get_digitized_curve(curve_id).get_section(section.section_id)
    assert len(deleted.control_points_px) == 4
    assert controller.undo_stack.count() == 2

    controller.undo()
    after_undo_delete = service.get_digitized_curve(curve_id).get_section(
        section.section_id
    )
    assert len(after_undo_delete.control_points_px) == 5
    assert TracePoint(65.0, 70.0) in after_undo_delete.control_points_px

    controller.undo()
    after_undo_insert = service.get_digitized_curve(curve_id).get_section(
        section.section_id
    )
    assert after_undo_insert.control_points_px == section.control_points_px

    controller.redo()
    controller.redo()
    after_two_redos = service.get_digitized_curve(curve_id).get_section(
        section.section_id
    )
    assert after_two_redos.control_points_px == section.control_points_px


def test_cut_and_join_round_trip_through_undo_redo(
    qtbot,
    tmp_path: Path,
) -> None:
    section = manual_section()
    original_id = section.section_id
    window, service, curve_id, _ = make_window_with_sections(
        qtbot, tmp_path, [section]
    )
    enter_editing_and_select_section(
        qtbot, window, curve_id, section.section_id
    )
    controller = window.manual_digitization_controller

    controller.toggle_cut_mode()
    controller._cut_selected_section_at(
        curve_id,
        section.section_id,
        65.0,
        70.0,
    )
    cut_curve = service.get_digitized_curve(curve_id)
    assert len(cut_curve.sections) == 2

    controller.undo()
    restored = service.get_digitized_curve(curve_id)
    assert len(restored.sections) == 1
    assert restored.sections[0].section_id == original_id
    assert restored.sections[0].control_points_px == section.control_points_px

    controller.redo()
    recut = service.get_digitized_curve(curve_id)
    assert len(recut.sections) == 2
    lower_id = next(
        item.section_id for item in recut.sections if item.section_id != original_id
    )

    # Join the two persisted sections, then verify that Join itself is undoable.
    controller._selected_section_id = original_id
    controller.toggle_join_mode()
    controller._join_selected_section_with(curve_id, lower_id)
    joined = service.get_digitized_curve(curve_id)
    assert len(joined.sections) == 1
    assert joined.sections[0].section_id == original_id

    controller.undo()
    unjoined = service.get_digitized_curve(curve_id)
    assert len(unjoined.sections) == 2

    controller.redo()
    rejoined = service.get_digitized_curve(curve_id)
    assert len(rejoined.sections) == 1
    assert rejoined.sections[0].section_id == original_id


def test_delete_last_section_can_be_undone_after_edit_mode_closes(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    section = manual_section()
    window, service, curve_id, _ = make_window_with_sections(
        qtbot, tmp_path, [section]
    )
    enter_editing_and_select_section(
        qtbot, window, curve_id, section.section_id
    )
    controller = window.manual_digitization_controller

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    controller.delete_selected_section()

    assert service.get_digitized_curve(curve_id) is None
    assert controller.editing is False
    assert controller.undo_stack.canUndo() is True
    assert window.digitization_panel.undo_button.isEnabled() is True

    controller.undo()
    restored = service.get_digitized_curve(curve_id)
    assert restored is not None
    assert len(restored.sections) == 1
    assert restored.sections[0].section_id == section.section_id

    controller.redo()
    assert service.get_digitized_curve(curve_id) is None


def test_context_change_clears_undo_history_and_actions_use_window_shortcuts(
    qtbot,
    tmp_path: Path,
) -> None:
    section = manual_section()
    window, service, curve_id, _ = make_window_with_sections(
        qtbot, tmp_path, [section]
    )
    enter_editing_and_select_section(
        qtbot, window, curve_id, section.section_id
    )
    controller = window.manual_digitization_controller
    controller._saved_node_move_committed(
        curve_id,
        section.section_id,
        1,
        80.0,
        60.0,
    )

    assert controller.undo_stack.count() == 1
    assert controller.undo_action.shortcutContext() is Qt.ShortcutContext.WindowShortcut
    assert controller.redo_action.shortcutContext() is Qt.ShortcutContext.WindowShortcut
    assert controller.undo_action.shortcuts()
    assert controller.redo_action.shortcuts()

    controller.abort_for_context_change()
    assert controller.undo_stack.count() == 0
    assert window.digitization_panel.undo_button.isEnabled() is False
    assert window.digitization_panel.redo_button.isEnabled() is False
