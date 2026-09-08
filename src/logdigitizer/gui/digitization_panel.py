"""Compact controls for manual curve digitization."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.domain.calibration import (
    CurveDefinition,
    DEFAULT_HORIZONTAL_SCALE_ID,
    HorizontalScaleProfile,
    ScaleType,
)

from .icons import app_icon, danger_icon, highlighted_icon


CUT_SECTION_TOOLTIP = (
    "Activate one-shot cut mode for the selected section. Then click the selected "
    "PCHIP line between two control nodes. The cut point is shared by the two "
    "resulting sections."
)
JOIN_SECTION_TOOLTIP = (
    "Activate one-shot join mode for the selected section. Then click a neighboring "
    "section of the same curve. Shared endpoints are deduplicated; a gap is bridged "
    "by the resulting PCHIP."
)
DELETE_SECTION_TOOLTIP = (
    "Delete the complete selected trace section after confirmation. This removes "
    "all of its control points and, for automatic sections, its preserved raw "
    "tracker samples."
)

START_MANUAL_HELP = (
    "Continuous manual digitization\n\n"
    "• Select the printed Scale used by the correction sections\n"
    "• Set Scale offset before starting a backup/wrap-around session\n"
    "• Click Start trace once to enter manual mode\n"
    "• Click to add control points\n"
    "• Over a saved trace, the manual line replaces the same vertical range\n"
    "• Right-click the image to save the current section and keep manual mode active\n"
    "• The next left-click immediately starts a new independent manual section\n"
    "• Click Finish to save the manual trace. Finish then leaves manual mode; "
    "if the current draft is empty, it simply leaves manual mode\n"
    "• Cancel discards the current unsaved draft and leaves manual mode\n"
    "• Middle-button drag pans the image while tracing."
)
EDIT_MANUAL_HELP = (
    "To edit a saved trace:\n\n"
    "• Select an existing trace\n"
    "• Drag a control node to move it\n"
    "• Click between two nodes to insert a new one\n"
    "• Right-click a control node to delete it\n"
    "• Use Cut Selected Trace to split it\n"
    "• Join Selected Traces to merge it\n"
    "• Undo (icon or Ctrl+Z)\n"
    "• Redo (icon)"
)


class ScaleOffsetSpinBox(QSpinBox):
    """Compact integer editor that makes positive offsets explicit."""

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt API
        numeric = int(value)
        return "0" if numeric == 0 else f"{numeric:+d}"


def _icon_button(
    icon_name: str,
    action_text: str,
    *,
    tooltip: str = "",
    danger: bool = False,
    show_text: bool = False,
) -> QToolButton:
    """Create a compact tool button, optionally with a visible action label."""
    button = QToolButton()
    button.setText(action_text)
    button.setToolButtonStyle(
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        if show_text else Qt.ToolButtonStyle.ToolButtonIconOnly
    )
    button.setIcon(
        danger_icon(icon_name)
        if danger
        else app_icon(icon_name)
    )
    button.setIconSize(QSize(18, 18))
    if show_text:
        button.setFixedHeight(32)
        button.setMinimumWidth(112)
    else:
        button.setFixedSize(32, 32)
    button.setAccessibleName(action_text)
    button.setToolTip(tooltip)
    return button


class DigitizationPanel(QWidget):
    """Compact manual digitization panel with contextual help."""

    curveSelectionChanged = Signal(str)
    manualStartRequested = Signal()
    manualFinishRequested = Signal()
    manualCancelRequested = Signal()
    editStartRequested = Signal()
    editDoneRequested = Signal()
    undoRequested = Signal()
    redoRequested = Signal()
    nodeDeleteRequested = Signal()
    sectionDeleteRequested = Signal()
    sectionCutRequested = Signal()
    sectionJoinRequested = Signal()
    scaleSelectionChanged = Signal(str)
    scaleOffsetChanged = Signal(int)

    # Kept for backward compatibility with controller wiring. The corresponding
    # controls are no longer part of the visible interface and their effective
    # behavior is permanently enabled.
    showSavedCurvesChanged = Signal(bool)
    dimInactiveCurvesChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._curves: tuple[CurveDefinition, ...] = ()
        self._tracing = False
        self._editing = False
        self._cutting = False
        self._joining = False
        self._can_start = False
        self._can_edit = False
        self._saved_section_count = 0
        self._scale_offset_available = False
        self._scale_profiles: tuple[HorizontalScaleProfile, ...] = ()
        self._selected_section_id_for_offset: str | None = None

        # ------------------------------------------------------------------
        # Curve
        # ------------------------------------------------------------------
        self.curve_combo = QComboBox()
        self.curve_combo.setToolTip("Select a curve to digitise")

        self.curve_mnemonic_label = QLabel("—")
        self.curve_mnemonic_label.setMinimumWidth(30)
        self.curve_mnemonic_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self.curve_mnemonic_label.setToolTip(
            "Mnemonic of the active curve, shown in its project display color."
        )

        self.curve_color_preview = QLabel()
        self.curve_color_preview.setFixedSize(30, 18)
        self.curve_color_preview.setToolTip(
            "Display color assigned to the selected project curve."
        )

        self.highlight_active_curve_button = _icon_button(
            "eye",
            "Highlight active curve",
            tooltip="Highlight active curve",
        )
        self.highlight_active_curve_button.setCheckable(True)
        self.highlight_active_curve_button.setChecked(True)

        curve_row = QHBoxLayout()
        curve_row.setContentsMargins(0, 0, 0, 0)
        curve_row.addWidget(QLabel("Curve:"))
        curve_row.addWidget(self.curve_mnemonic_label)
        curve_row.addWidget(self.curve_combo, 1)
        curve_row.addWidget(self.curve_color_preview)
        curve_row.addWidget(self.highlight_active_curve_button)

        self.scale_profile_combo = QComboBox()
        self.scale_profile_combo.setMinimumWidth(150)
        self.scale_profile_combo.setAccessibleName("Scale")
        self.scale_profile_combo.setToolTip(
            "Select the calibrated printed scale used by the next section."
        )

        self.scale_offset_spin = ScaleOffsetSpinBox()
        self.scale_offset_spin.setRange(-99, 99)
        self.scale_offset_spin.setValue(0)
        self.scale_offset_spin.setSingleStep(1)
        self.scale_offset_spin.setMinimumWidth(72)
        self.scale_offset_spin.setAccessibleName("Scale offset")
        self.scale_offset_spin.setToolTip(
            "Select the number of complete linear scale widths represented by "
            "the next manual or automatic section. Set +1 for the first "
            "backup/wrap-around scale and return to 0 for the normal scale."
        )

        self.scale_offset_badge = QLabel()
        self.scale_offset_badge.setWordWrap(False)
        self.scale_offset_badge.setStyleSheet(
            "QLabel { background: #FFF3CD; color: #664D03; "
            "border: 1px solid #D6B656; border-radius: 3px; "
            "padding: 3px 6px; font-weight: 600; }"
        )
        self.scale_offset_badge.setToolTip(
            "The active or selected section uses a backup-scale offset."
        )
        self.scale_offset_badge.hide()

        scale_row = QHBoxLayout()
        scale_row.setContentsMargins(0, 0, 0, 0)
        scale_row.addWidget(QLabel("Scale:"))
        scale_row.addWidget(self.scale_profile_combo, 1)
        scale_row.addWidget(QLabel("Scale offset:"))
        scale_row.addWidget(self.scale_offset_spin)
        scale_row.addWidget(self.scale_offset_badge)

        self.readiness_label = QLabel("Select a project segment and curve.")
        self.readiness_label.setWordWrap(True)

        self.curve_group = QGroupBox("Curve")
        curve_group_layout = QVBoxLayout(self.curve_group)
        curve_group_layout.addLayout(curve_row)
        curve_group_layout.addLayout(scale_row)
        curve_group_layout.addWidget(self.readiness_label)

        # Compatibility-only controls. They deliberately remain hidden and the
        # public properties below always return True, as required by the new UI.
        self.show_saved_check = QCheckBox("Show saved curves", self)
        self.show_saved_check.setChecked(True)
        self.show_saved_check.hide()

        self.dim_inactive_check = QCheckBox("Dim non-active curves", self)
        self.dim_inactive_check.setChecked(True)
        self.dim_inactive_check.hide()

        # Compatibility-only status widgets. Controllers/tests may still inspect
        # them, but the permanent information has been removed from the UI.
        self.section_count_label = QLabel("Saved sections: 0", self)
        self.section_count_label.hide()
        self.node_count_label = QLabel("Current nodes: 0", self)
        self.node_count_label.hide()
        self.edit_status_label = QLabel("Editing: inactive", self)
        self.edit_status_label.setWordWrap(True)
        self.edit_status_label.hide()
        self.selected_section_label = QLabel("Selected section: —", self)
        self.selected_section_label.hide()
        self.selected_node_label = QLabel("Selected node: —", self)
        self.selected_node_label.hide()

        # ------------------------------------------------------------------
        # Manual digitization / Start Manual Trace
        # ------------------------------------------------------------------
        self.start_button = _icon_button(
            "plus",
            "Start trace",
            tooltip="Start Manual Trace",
            show_text=True,
        )
        self.finish_button = _icon_button(
            "square-check",
            "Finish",
            tooltip="Finish Trace",
        )
        self.cancel_button = _icon_button(
            "square-xmark",
            "Cancel",
            tooltip="Cancel Trace",
        )
        self.start_help_button = _icon_button(
            "circle-info",
            "Help",
            tooltip="Help",
        )

        start_button_row = QHBoxLayout()
        start_button_row.addWidget(self.start_button)
        start_button_row.addWidget(self.finish_button)
        start_button_row.addWidget(self.cancel_button)
        start_button_row.addStretch(1)
        start_button_row.addWidget(self.start_help_button)

        self.start_manual_group = QGroupBox()
        self.start_manual_group.setStyleSheet(
            "QGroupBox { border: none; margin-top: 0px; }"
        )
        start_manual_layout = QVBoxLayout(self.start_manual_group)
        start_manual_layout.setContentsMargins(4, 6, 4, 4)
        start_manual_layout.addLayout(start_button_row)

        # ------------------------------------------------------------------
        # Manual digitization / Edit Manual Trace
        # ------------------------------------------------------------------
        self.edit_button = _icon_button(
            "pen",
            "Edit trace",
            tooltip="Edit Trace",
            show_text=True,
        )
        self.done_edit_button = _icon_button(
            "square-check",
            "Done Editing",
            tooltip="Done Editing",
        )
        self.edit_help_button = _icon_button(
            "circle-info",
            "Help",
            tooltip="Help",
        )

        edit_action_row = QHBoxLayout()
        edit_action_row.addWidget(self.edit_button)
        edit_action_row.addWidget(self.done_edit_button)
        edit_action_row.addStretch(1)
        edit_action_row.addWidget(self.edit_help_button)

        # Compatibility-only control: node deletion remains available through
        # right-click on the canvas. Keep the hidden button so existing
        # controller wiring/regression tests still exercise the same command.
        self.delete_node_button = QPushButton("Delete Selected Node", self)
        self.delete_node_button.setToolTip(
            "Delete the selected control node. Right-click a node on the canvas "
            "to perform this action in the visible interface."
        )
        self.delete_node_button.hide()

        self.undo_button = _icon_button(
            "rotate-left",
            "Undo",
        )
        self.redo_button = _icon_button(
            "rotate-right",
            "Redo",
        )
        self.cut_section_button = _icon_button(
            "link-slash",
            "Cut Selected Section",
            tooltip=CUT_SECTION_TOOLTIP,
        )
        self.join_section_button = _icon_button(
            "link",
            "Join Selected Section",
            tooltip=JOIN_SECTION_TOOLTIP,
        )
        self.delete_section_button = _icon_button(
            "trash",
            "Delete Selected Section",
            tooltip=DELETE_SECTION_TOOLTIP,
            danger=True,
        )

        edit_tools_row = QHBoxLayout()
        edit_tools_row.addWidget(self.undo_button)
        edit_tools_row.addWidget(self.redo_button)
        edit_tools_row.addSpacing(8)
        edit_tools_row.addWidget(self.cut_section_button)
        edit_tools_row.addWidget(self.join_section_button)
        edit_tools_row.addSpacing(8)
        edit_tools_row.addWidget(self.delete_section_button)
        edit_tools_row.addStretch(1)

        self.edit_manual_group = QGroupBox()
        self.edit_manual_group.setStyleSheet(
            "QGroupBox { border: none; margin-top: 0px; }"
        )
        edit_manual_layout = QVBoxLayout(self.edit_manual_group)
        edit_manual_layout.setContentsMargins(4, 6, 4, 4)
        edit_manual_layout.addLayout(edit_action_row)
        edit_manual_layout.addLayout(edit_tools_row)

        self.manual_group = QGroupBox("Manual digitization")
        manual_layout = QVBoxLayout(self.manual_group)
        manual_layout.setSpacing(2)
        manual_layout.addWidget(self.start_manual_group)
        manual_layout.addWidget(self.edit_manual_group)

        # The automatic subclass inserts Automatic tracing at index 1,
        # yielding Curve -> Automatic tracing -> Manual.
        layout = QVBoxLayout(self)
        layout.addWidget(self.curve_group)
        layout.addWidget(self.manual_group)
        layout.addStretch(1)

        self.curve_combo.currentIndexChanged.connect(self._curve_changed)
        self.start_button.clicked.connect(self.manualStartRequested.emit)
        self.finish_button.clicked.connect(self.manualFinishRequested.emit)
        self.cancel_button.clicked.connect(self.manualCancelRequested.emit)
        self.edit_button.clicked.connect(self.editStartRequested.emit)
        self.undo_button.clicked.connect(self.undoRequested.emit)
        self.redo_button.clicked.connect(self.redoRequested.emit)
        self.delete_node_button.clicked.connect(self.nodeDeleteRequested.emit)
        self.cut_section_button.clicked.connect(self.sectionCutRequested.emit)
        self.join_section_button.clicked.connect(self.sectionJoinRequested.emit)
        self.delete_section_button.clicked.connect(self.sectionDeleteRequested.emit)
        self.done_edit_button.clicked.connect(self.editDoneRequested.emit)
        self.start_help_button.clicked.connect(self._show_start_manual_help)
        self.edit_help_button.clicked.connect(self._show_edit_manual_help)
        self.show_saved_check.toggled.connect(self.showSavedCurvesChanged.emit)
        self.dim_inactive_check.toggled.connect(self._dim_inactive_check_toggled)
        self.highlight_active_curve_button.toggled.connect(
            self._highlight_active_curve_toggled
        )
        self._update_highlight_active_curve_visuals()
        self.scale_offset_spin.valueChanged.connect(
            self._scale_offset_value_changed
        )
        self.scale_profile_combo.currentIndexChanged.connect(
            self._scale_profile_value_changed
        )

        self.set_context(
            project_available=False,
            segment_available=False,
            image_ready=False,
            readiness_message="Create or open a project first.",
            saved_section_count=0,
            can_start=False,
        )
        self.set_draft_state(False, 0)
        self.set_edit_state(False)
        self.set_undo_state(False, False)

    @property
    def selected_curve_id(self) -> str | None:
        value = self.curve_combo.currentData()
        return str(value) if value else None

    @property
    def tracing(self) -> bool:
        return self._tracing

    @property
    def scale_offset(self) -> int:
        """Offset that will be captured by the next digitized section."""
        return int(self.scale_offset_spin.value())

    @property
    def selected_scale_id(self) -> str:
        value = self.scale_profile_combo.currentData()
        return str(value or DEFAULT_HORIZONTAL_SCALE_ID)

    def set_scale_profiles(
        self,
        profiles: Sequence[HorizontalScaleProfile],
        curve: CurveDefinition | None,
        *,
        selected_scale_id: str | None = None,
    ) -> None:
        """Show complete scales while preserving one valid working choice."""
        complete = tuple(profile for profile in profiles if profile.complete)
        previous = selected_scale_id or self.selected_scale_id
        self._scale_profiles = complete
        self.scale_profile_combo.blockSignals(True)
        self.scale_profile_combo.clear()
        for profile in complete:
            self.scale_profile_combo.addItem(
                profile.display_name(curve) if curve is not None else profile.scale_id,
                profile.scale_id,
            )
        index = self.scale_profile_combo.findData(previous)
        if index < 0 and self.scale_profile_combo.count():
            index = 0
        self.scale_profile_combo.setCurrentIndex(index)
        self.scale_profile_combo.blockSignals(False)
        self._update_scale_offset_badge()
        self._sync_scale_offset_control()

    def set_scale_offset(self, value: int) -> None:
        """Set offset without treating a controller refresh as a user edit."""
        previous = self.scale_offset_spin.blockSignals(True)
        self.scale_offset_spin.setValue(int(value))
        self.scale_offset_spin.blockSignals(previous)
        self._update_scale_offset_badge()

    @property
    def editing(self) -> bool:
        return self._editing

    @property
    def show_saved_curves(self) -> bool:
        """Return hidden saved-curve visibility state (default: enabled)."""
        return self.show_saved_check.isChecked()

    @property
    def dim_inactive_curves(self) -> bool:
        """Return hidden inactive-curve dimming state (default: enabled)."""
        return self.dim_inactive_check.isChecked()

    def set_curves(
        self,
        curves: Sequence[CurveDefinition],
        selected_curve_id: str | None = None,
    ) -> None:
        """Replace curve choices while preserving a valid selection when possible."""
        previous = selected_curve_id or self.selected_curve_id
        self._curves = tuple(curves)

        self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        for curve in self._curves:
            unit = f" [{curve.unit}]" if curve.unit else ""
            self.curve_combo.addItem(
                f"{curve.name}{unit}",
                curve.curve_id,
            )

        if previous:
            index = self.curve_combo.findData(previous)
            if index >= 0:
                self.curve_combo.setCurrentIndex(index)
        self.curve_combo.blockSignals(False)

        self.curve_combo.setEnabled(
            bool(self._curves) and not self._tracing and not self._editing
        )
        self._update_curve_color_preview()
        self._update_scale_offset_badge()
        self._sync_scale_offset_control()

    def set_context(
        self,
        *,
        project_available: bool,
        segment_available: bool,
        image_ready: bool,
        readiness_message: str,
        saved_section_count: int,
        can_start: bool,
        can_edit: bool = False,
    ) -> None:
        """Render current project/segment/readiness state without emitting actions."""
        self._can_start = bool(can_start)
        self._can_edit = bool(can_edit)
        self._saved_section_count = int(saved_section_count)
        self._scale_offset_available = bool(
            project_available
            and segment_available
            and image_ready
            and self.selected_curve_id is not None
            and can_start
        )
        self.readiness_label.setText(readiness_message)
        self.section_count_label.setText(
            f"Saved sections: {int(saved_section_count)}"
        )

        has_curve = self.selected_curve_id is not None
        self.curve_combo.setEnabled(
            bool(
                project_available
                and segment_available
                and has_curve
                and not self._tracing
                and not self._editing
            )
        )
        self.start_button.setEnabled(
            self._can_start and not self._tracing and not self._editing
        )
        self.finish_button.setEnabled(self._tracing)
        self.cancel_button.setEnabled(self._tracing)
        self.edit_button.setEnabled(
            self._can_edit and not self._tracing and not self._editing
        )
        self.done_edit_button.setEnabled(self._editing)
        self.delete_node_button.setEnabled(False)
        self.cut_section_button.setEnabled(False)
        self.join_section_button.setEnabled(False)
        self.delete_section_button.setEnabled(False)

        if not project_available:
            self.readiness_label.setText("Create or open a project first.")
        elif not segment_available:
            self.readiness_label.setText("Select a project segment.")
        elif not image_ready:
            self.readiness_label.setText(
                "Apply rectification before digitizing this segment."
            )
        self._update_readiness_visual()
        self._sync_scale_offset_control()

    def set_draft_state(self, tracing: bool, node_count: int) -> None:
        """Update controls for an active or inactive transient manual draft."""
        self._tracing = bool(tracing)
        self.node_count_label.setText(f"Current nodes: {int(node_count)}")
        self.curve_combo.setEnabled(
            bool(self._curves) and not self._tracing and not self._editing
        )
        self.finish_button.setEnabled(self._tracing and int(node_count) >= 2)
        self.cancel_button.setEnabled(self._tracing)
        self.start_button.setEnabled(
            self._can_start and not self._tracing and not self._editing
        )
        self.edit_button.setEnabled(
            self._can_edit and not self._tracing and not self._editing
        )
        if not self._editing:
            self.delete_node_button.setEnabled(False)
            self.cut_section_button.setEnabled(False)
            self.join_section_button.setEnabled(False)
            self.delete_section_button.setEnabled(False)
        self._sync_scale_offset_control()

    def set_manual_draft_state(
        self,
        tracing: bool,
        node_count: int,
        *,
        can_finish_empty_session: bool = False,
    ) -> None:
        """Update continuous manual-session state without changing old APIs.

        This intentionally calls ``self.set_draft_state`` first. On the combined
        AutomaticDigitizationPanel that dispatches through its existing override,
        preserving automatic-control synchronization and its historical two-
        argument signature. Only the extra continuous-session Finish rule is
        applied afterwards.
        """
        self.set_draft_state(tracing, node_count)
        if self._tracing and bool(can_finish_empty_session):
            self.finish_button.setEnabled(True)

    def set_undo_state(
        self,
        can_undo: bool,
        can_redo: bool,
        *,
        undo_text: str = "",
        redo_text: str = "",
    ) -> None:
        """Render undo/redo availability independently of edit selection."""
        self.undo_button.setEnabled(bool(can_undo))
        self.redo_button.setEnabled(bool(can_redo))
        self.undo_button.setText(
            f"Undo {undo_text}" if can_undo and undo_text else "Undo"
        )
        self.redo_button.setText(
            f"Redo {redo_text}" if can_redo and redo_text else "Redo"
        )

    def set_edit_state(
        self,
        editing: bool,
        *,
        selected_section_id: str | None = None,
        selected_node_index: int | None = None,
        selected_section_node_count: int = 0,
        selected_section_scale_id: str | None = None,
        selected_section_offset: int | None = None,
        cutting: bool = False,
        joining: bool = False,
    ) -> None:
        """Render saved-trace edit state for one persisted trace section."""
        self._editing = bool(editing)
        self._selected_section_id_for_offset = (
            selected_section_id if self._editing else None
        )
        self._cutting = bool(cutting) and self._editing
        self._joining = bool(joining) and self._editing and not self._cutting
        if self._editing:
            if selected_section_scale_id is not None:
                self._set_selected_scale_id(selected_section_scale_id)
            self.set_scale_offset(
                0
                if selected_section_id is None
                or selected_section_offset is None
                else selected_section_offset
            )
        self._update_edit_tool_visuals()
        self.curve_combo.setEnabled(
            bool(self._curves) and not self._tracing and not self._editing
        )
        self.start_button.setEnabled(
            self._can_start and not self._tracing and not self._editing
        )
        self.edit_button.setEnabled(
            self._can_edit and not self._tracing and not self._editing
        )
        self.done_edit_button.setEnabled(self._editing)

        can_delete_node = bool(
            self._editing
            and not self._cutting
            and not self._joining
            and selected_node_index is not None
            and int(selected_section_node_count) > 2
        )
        self.delete_node_button.setEnabled(can_delete_node)

        can_cut = (
            self._editing
            and not self._joining
            and selected_section_id is not None
        )
        self.cut_section_button.setEnabled(can_cut)
        self.cut_section_button.setText(
            "Cancel Cut" if self._cutting else "Cut Selected Section"
        )

        can_join = bool(
            self._editing
            and not self._cutting
            and selected_section_id is not None
            and self._saved_section_count > 1
        )
        self.join_section_button.setEnabled(can_join)
        self.join_section_button.setText(
            "Cancel Join" if self._joining else "Join Selected Section"
        )

        self.delete_section_button.setEnabled(
            self._editing
            and not self._cutting
            and not self._joining
            and selected_section_id is not None
        )

        if not self._editing:
            self._cutting = False
            self._joining = False
            self.cut_section_button.setText("Cut Selected Section")
            self.join_section_button.setText("Join Selected Section")
            self._update_edit_tool_visuals()
            self.edit_status_label.setText("Editing: inactive")
            self.selected_section_label.setText("Selected section: —")
            self.selected_node_label.setText("Selected node: —")
            self._sync_scale_offset_control()
            return

        if self._cutting:
            self.edit_status_label.setText(
                "Cut mode: click the selected curve between two control nodes. "
                "Click the highlighted cut icon again to cancel."
            )
        elif self._joining:
            self.edit_status_label.setText(
                "Join mode: click a neighboring section of the selected curve. "
                "Click the highlighted join icon again to cancel."
            )
        else:
            self.edit_status_label.setText(
                "Editing: select a section; edit its nodes, cut it, join it, or delete it."
            )

        if selected_section_id is None:
            self.selected_section_label.setText("Selected section: —")
            self.selected_node_label.setText("Selected node: —")
            self._sync_scale_offset_control()
            return

        self.selected_section_label.setText(
            f"Selected section: {selected_section_id[:8]} "
            f"({int(selected_section_node_count)} nodes)"
        )
        self.selected_node_label.setText(
            "Selected node: —"
            if selected_node_index is None
            else (
                f"Selected node: {int(selected_node_index) + 1} — drag to move; "
                "right-click it to remove"
            )
        )
        self._sync_scale_offset_control()

    def _update_edit_tool_visuals(self) -> None:
        """Show active cut/join mode without exposing button text visually."""
        if self._cutting:
            self.cut_section_button.setIcon(
                highlighted_icon("link-slash")
            )
            self.cut_section_button.setToolTip("Cancel cut mode.")
        else:
            self.cut_section_button.setIcon(app_icon("link-slash"))
            self.cut_section_button.setToolTip(CUT_SECTION_TOOLTIP)

        if self._joining:
            self.join_section_button.setIcon(highlighted_icon("link"))
            self.join_section_button.setToolTip("Cancel join mode.")
        else:
            self.join_section_button.setIcon(app_icon("link"))
            self.join_section_button.setToolTip(JOIN_SECTION_TOOLTIP)

    def _show_start_manual_help(self) -> None:
        QMessageBox.information(
            self,
            "Manual trace help",
            START_MANUAL_HELP,
        )

    def _show_edit_manual_help(self) -> None:
        QMessageBox.information(
            self,
            "Edit trace help",
            EDIT_MANUAL_HELP,
        )

    def _highlight_active_curve_toggled(self, checked: bool) -> None:
        """Expose the existing inactive-curve dimming as a visible UI mode."""
        enabled = bool(checked)
        previous = self.dim_inactive_check.blockSignals(True)
        self.dim_inactive_check.setChecked(enabled)
        self.dim_inactive_check.blockSignals(previous)
        self._update_highlight_active_curve_visuals()
        self.dimInactiveCurvesChanged.emit(enabled)

    def _dim_inactive_check_toggled(self, checked: bool) -> None:
        """Keep the legacy hidden checkbox synchronized with the visible button."""
        enabled = bool(checked)
        previous = self.highlight_active_curve_button.blockSignals(True)
        self.highlight_active_curve_button.setChecked(enabled)
        self.highlight_active_curve_button.blockSignals(previous)
        self._update_highlight_active_curve_visuals()
        self.dimInactiveCurvesChanged.emit(enabled)

    def _update_highlight_active_curve_visuals(self) -> None:
        """Render the focus mode clearly without changing any project data."""
        if self.highlight_active_curve_button.isChecked():
            self.highlight_active_curve_button.setIcon(highlighted_icon("eye"))
            self.highlight_active_curve_button.setToolTip(
                "Highlight active curve; other saved curves are shown "
                "with reduced opacity."
            )
        else:
            self.highlight_active_curve_button.setIcon(app_icon("eye"))
            self.highlight_active_curve_button.setToolTip(
                "Show all saved curves at full opacity."
            )

    def _curve_changed(self, *_args) -> None:
        # Offset is a per-curve working state. Never carry it silently into a
        # different curve selected by the user.
        self.set_scale_offset(0)
        self._update_curve_color_preview()
        curve_id = self.selected_curve_id
        if curve_id:
            self.curveSelectionChanged.emit(curve_id)

    def _scale_profile_value_changed(self, *_args) -> None:
        self._update_scale_offset_badge()
        self.scaleSelectionChanged.emit(self.selected_scale_id)

    def _scale_offset_value_changed(self, value: int) -> None:
        self._update_scale_offset_badge()
        self.scaleOffsetChanged.emit(int(value))

    def _update_scale_offset_badge(self) -> None:
        offset = self.scale_offset
        self.scale_offset_spin.setStyleSheet(
            "QSpinBox { background: #FFF3CD; color: #664D03; "
            "border: 1px solid #D6B656; }"
            if offset
            else ""
        )
        curve_id = self.selected_curve_id
        curve = next(
            (item for item in self._curves if item.curve_id == curve_id),
            None,
        )
        profile = next(
            (
                item
                for item in self._scale_profiles
                if item.scale_id == self.selected_scale_id
            ),
            None,
        )
        show_scale = bool(
            profile is not None
            and (
                len(self._scale_profiles) > 1
                or profile.scale_id != DEFAULT_HORIZONTAL_SCALE_ID
            )
        )
        if (offset == 0 and not show_scale) or curve is None:
            self.scale_offset_badge.clear()
            self.scale_offset_badge.hide()
            return
        parts = [curve.mnemonic]
        if show_scale and profile is not None:
            full_name = profile.display_name(curve)
            prefix = f"{curve.mnemonic}: "
            parts.append(
                full_name[len(prefix):]
                if full_name.startswith(prefix)
                else full_name
            )
        if offset:
            parts.append(f"{offset:+d} scale")
        self.scale_offset_badge.setText(" · ".join(parts))
        self.scale_offset_badge.show()

    def _set_selected_scale_id(self, scale_id: str) -> None:
        index = self.scale_profile_combo.findData(str(scale_id))
        if index < 0:
            return
        previous = self.scale_profile_combo.blockSignals(True)
        self.scale_profile_combo.setCurrentIndex(index)
        self.scale_profile_combo.blockSignals(previous)
        self._update_scale_offset_badge()

    def _sync_scale_offset_control(self) -> None:
        """Lock offset during capture and enable selected-section editing."""
        curve_id = self.selected_curve_id
        curve = next(
            (item for item in self._curves if item.curve_id == curve_id),
            None,
        )
        linear_scale = bool(
            curve is not None and curve.scale_type is ScaleType.LINEAR
        )
        if self._tracing:
            enabled = False
        elif self._editing:
            enabled = bool(
                self._selected_section_id_for_offset is not None
                and not self._cutting
                and not self._joining
            )
        else:
            enabled = self._scale_offset_available
        self.scale_offset_spin.setEnabled(enabled and linear_scale)
        self.scale_profile_combo.setEnabled(
            enabled and len(self._scale_profiles) > 1
        )

    def _update_readiness_visual(self) -> None:
        """Style readiness semantically without changing controller-owned text."""
        text = self.readiness_label.text().strip()
        if text.startswith("✓"):
            self.readiness_label.setStyleSheet("color: #438A5E;")
        elif text.startswith("⚠"):
            self.readiness_label.setStyleSheet("color: #D58B20;")
        else:
            self.readiness_label.setStyleSheet("")

    def _update_curve_color_preview(self) -> None:
        curve_id = self.selected_curve_id
        curve = next(
            (item for item in self._curves if item.curve_id == curve_id),
            None,
        )
        if curve is None:
            self.curve_mnemonic_label.setText("—")
            self.curve_mnemonic_label.setStyleSheet("")
            self.curve_color_preview.setStyleSheet(
                "background: transparent; border: 1px solid #777; "
                "border-radius: 2px;"
            )
            return
        self.curve_mnemonic_label.setText(curve.mnemonic)
        self.curve_mnemonic_label.setStyleSheet(
            f"color: {curve.display_color}; font-weight: 600;"
        )
        self.curve_color_preview.setStyleSheet(
            f"background-color: {curve.display_color}; border: 1px solid #555; "
            "border-radius: 2px;"
        )
