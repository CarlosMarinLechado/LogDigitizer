from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt

from logdigitizer.domain.calibration import DepthUnit
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.domain.workflow import (
    CurveWorkflowSummary,
    SegmentWorkflowSummary,
    WorkflowState,
)
from logdigitizer.gui.project_panel import ProjectPanel


def _summary(
    segment_id: str,
    *,
    state: WorkflowState,
    curve_id: str = "curve-gr",
) -> SegmentWorkflowSummary:
    return SegmentWorkflowSummary(
        segment_id=segment_id,
        state=state,
        preprocessing_state=WorkflowState.COMPLETE,
        depth_state=WorkflowState.COMPLETE,
        depth_point_count=3,
        depth_unit=DepthUnit.METERS,
        curves=(
            CurveWorkflowSummary(
                curve_id=curve_id,
                mnemonic="GR",
                name="Gamma Ray",
                unit="API",
                display_color="#00B8D9",
                x_state=WorkflowState.COMPLETE,
                x_point_count=2,
                digitization_state=(
                    WorkflowState.COMPLETE
                    if state is WorkflowState.COMPLETE
                    else WorkflowState.NOT_STARTED
                ),
                section_count=2 if state is WorkflowState.COMPLETE else 0,
                state=state,
            ),
        ),
    )


def _project():
    project = LogProject.create(name="BH-01")
    first = Segment.create(
        order=1,
        name="scan_001.tif",
        source_path="source/scan_001.tif",
    )
    second = Segment.create(
        order=2,
        name="scan_002.tif",
        source_path="source/scan_002.tif",
    )
    project.add_segment(first)
    project.add_segment(second)
    return project, first, second


def test_tree_shows_active_segment_workflow_and_collapses_other_segments(
    qtbot,
) -> None:
    project, first, second = _project()
    panel = ProjectPanel()
    qtbot.addWidget(panel)

    panel.set_project(
        project,
        first.segment_id,
        (
            _summary(first.segment_id, state=WorkflowState.COMPLETE),
            _summary(second.segment_id, state=WorkflowState.INCOMPLETE),
        ),
    )

    assert panel.segment_list.count() == 2
    first_root = panel.segment_list.item(0)
    second_root = panel.segment_list.item(1)
    assert first_root.isExpanded() is True
    assert second_root.isExpanded() is False

    assert first_root.text(0) == "01 · scan_001.tif"
    assert first_root.child(0).text(0) == "Preprocessing"
    assert "Depth calibration · 3 pts · m" == first_root.child(1).text(0)

    curve = first_root.child(2)
    assert curve.text(0) == "GR · Gamma Ray · API"
    assert curve.data(0, panel.CURVE_MNEMONIC_ROLE) == "GR"
    assert curve.data(0, panel.CURVE_COLOR_ROLE) == "#00B8D9"
    assert curve.isExpanded() is False
    assert curve.child(0).text(0) == "X calibration"
    assert curve.child(1).text(0) == "Digitization"


def test_expanded_curve_is_preserved_when_tree_refreshes(qtbot) -> None:
    project, first, second = _project()
    summaries = (
        _summary(first.segment_id, state=WorkflowState.COMPLETE),
        _summary(second.segment_id, state=WorkflowState.INCOMPLETE),
    )
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.set_project(project, first.segment_id, summaries)

    curve = panel.segment_list.item(0).child(2)
    curve.setExpanded(True)

    panel.set_project(project, first.segment_id, summaries)
    assert panel.segment_list.item(0).child(2).isExpanded() is True


def test_clicking_digitization_node_emits_navigation_payload(qtbot) -> None:
    project, first, _second = _project()
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.set_project(
        project,
        first.segment_id,
        (_summary(first.segment_id, state=WorkflowState.COMPLETE),),
    )

    digitization = panel.segment_list.item(0).child(2).child(1)
    with qtbot.waitSignal(
        panel.workflowNodeActivated,
        timeout=1000,
    ) as blocker:
        panel.segment_list.itemClicked.emit(digitization, 0)

    assert blocker.args == [
        first.segment_id,
        panel.NODE_DIGITIZATION,
        "curve-gr",
    ]


def test_child_selection_still_resolves_owning_segment(qtbot) -> None:
    project, first, _second = _project()
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.set_project(
        project,
        first.segment_id,
        (_summary(first.segment_id, state=WorkflowState.COMPLETE),),
    )

    x_item = panel.segment_list.item(0).child(2).child(0)
    panel.segment_list.setCurrentItem(x_item)
    assert panel.current_segment_id() == first.segment_id


def test_tree_labels_each_image_with_its_calibrated_depth_interval(qtbot) -> None:
    project, first, second = _project()
    panel = ProjectPanel()
    qtbot.addWidget(panel)

    panel.set_project(
        project,
        first.segment_id,
        (
            _summary(first.segment_id, state=WorkflowState.COMPLETE),
            _summary(second.segment_id, state=WorkflowState.INCOMPLETE),
        ),
        {
            first.segment_id: (0.0, 1100.0, DepthUnit.METERS),
            second.segment_id: (990.0, 2000.0, DepthUnit.METERS),
        },
    )

    assert panel.segment_list.item(0).text(0) == (
        "01 · scan_001.tif · 0–1100 m"
    )
    assert panel.segment_list.item(1).text(0) == (
        "02 · scan_002.tif · 990–2000 m"
    )


def test_tree_marks_an_image_without_valid_depth_calibration(qtbot) -> None:
    project, first, _second = _project()
    panel = ProjectPanel()
    qtbot.addWidget(panel)

    panel.set_project(project, first.segment_id, (), {})

    assert panel.segment_list.item(0).text(0).endswith(
        "· Depth not calibrated"
    )


def test_focus_digitization_curve_switches_expanded_branch_and_selection(qtbot) -> None:
    project, first, _second = _project()
    summary = _summary(first.segment_id, state=WorkflowState.COMPLETE)
    sp = CurveWorkflowSummary(
        curve_id="curve-sp",
        mnemonic="SP",
        name="Spontaneous Potential",
        unit="MV",
        display_color="#FF8C00",
        x_state=WorkflowState.COMPLETE,
        x_point_count=2,
        digitization_state=WorkflowState.COMPLETE,
        section_count=8,
        state=WorkflowState.COMPLETE,
    )
    summary = SegmentWorkflowSummary(
        segment_id=summary.segment_id,
        state=summary.state,
        preprocessing_state=summary.preprocessing_state,
        depth_state=summary.depth_state,
        depth_point_count=summary.depth_point_count,
        depth_unit=summary.depth_unit,
        curves=summary.curves + (sp,),
    )

    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.set_project(project, first.segment_id, (summary,))

    root = panel.segment_list.item(0)
    gr_item = root.child(2)
    sp_item = root.child(3)
    gr_item.setExpanded(True)
    assert sp_item.isExpanded() is False

    panel.focus_digitization_curve(first.segment_id, "curve-sp")

    assert root.isExpanded() is True
    assert gr_item.isExpanded() is False
    assert sp_item.isExpanded() is True
    current = panel.segment_list.currentItem()
    assert current is sp_item.child(1)
    assert current.text(0) == "Digitization"


def test_complete_status_uses_plain_check_icon(monkeypatch) -> None:
    import logdigitizer.gui.project_panel as project_panel_module

    calls = []
    monkeypatch.setattr(
        project_panel_module,
        "colored_icon",
        lambda name, color: calls.append((name, color)) or object(),
    )

    ProjectPanel._status_icon(WorkflowState.COMPLETE)

    assert calls[0][0] == "check"


def test_focus_calibration_curve_switches_expanded_branch_and_selection(qtbot) -> None:
    project, first, _second = _project()
    summary = _summary(first.segment_id, state=WorkflowState.COMPLETE)
    sp = CurveWorkflowSummary(
        curve_id="curve-sp",
        mnemonic="SP",
        name="Spontaneous Potential",
        unit="MV",
        display_color="#FF8C00",
        x_state=WorkflowState.COMPLETE,
        x_point_count=2,
        digitization_state=WorkflowState.COMPLETE,
        section_count=8,
        state=WorkflowState.COMPLETE,
    )
    summary = SegmentWorkflowSummary(
        segment_id=summary.segment_id,
        state=summary.state,
        preprocessing_state=summary.preprocessing_state,
        depth_state=summary.depth_state,
        depth_point_count=summary.depth_point_count,
        depth_unit=summary.depth_unit,
        curves=summary.curves + (sp,),
    )

    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.set_project(project, first.segment_id, (summary,))

    gr_item = panel.segment_list.item(0).child(2)
    sp_item = panel.segment_list.item(0).child(3)
    gr_item.setExpanded(True)

    panel.focus_calibration_curve(first.segment_id, "curve-sp")

    assert gr_item.isExpanded() is False
    assert sp_item.isExpanded() is True
    assert panel.segment_list.currentItem() is sp_item.child(0)
    assert sp_item.child(0).text(0) == "X calibration"


def test_project_explorer_uses_compact_segment_action_toolbar(qtbot) -> None:
    from PySide6.QtWidgets import QToolButton

    panel = ProjectPanel()
    qtbot.addWidget(panel)

    assert panel.add_button.text() == "Add image"
    assert not panel.add_button.icon().isNull()
    assert panel.add_button.toolTip() == "Add image"

    assert isinstance(panel.remove_button, QToolButton)
    assert panel.remove_button.text() == "Remove"
    assert panel.remove_button.toolButtonStyle() == (
        Qt.ToolButtonStyle.ToolButtonIconOnly
    )
    assert panel.remove_button.toolTip() == "Remove selected image"

    assert panel.segment_action_row.indexOf(panel.remove_button) == 0
    assert panel.segment_action_row.indexOf(panel.up_button) == 1
    assert panel.segment_action_row.indexOf(panel.down_button) == 2
    assert panel.up_button.toolTip() == ""
    assert panel.down_button.toolTip() == ""
