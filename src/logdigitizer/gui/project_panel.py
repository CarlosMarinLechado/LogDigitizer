"""Project/segment workflow tree and compact navigation controls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.domain.project import LogProject
from logdigitizer.domain.calibration import DepthUnit
from logdigitizer.domain.workflow import (
    CurveWorkflowSummary,
    SegmentWorkflowSummary,
    WorkflowState,
)

from .icons import (
    ICON_DANGER,
    ICON_MUTED,
    ICON_SUCCESS,
    ICON_WARNING,
    app_icon,
    colored_icon,
    danger_icon,
)


class SegmentTreeWidget(QTreeWidget):
    """QTreeWidget with the small QListWidget API used by older callers/tests."""

    def count(self) -> int:
        return self.topLevelItemCount()

    def item(self, row: int) -> QTreeWidgetItem | None:
        return self.topLevelItem(row)

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 - compatibility API
        item = self.topLevelItem(row)
        if item is not None:
            self.setCurrentItem(item)

    def currentRow(self) -> int:  # noqa: N802 - compatibility API
        current = self.currentItem()
        if current is None:
            return -1
        root = current
        while root.parent() is not None:
            root = root.parent()
        return self.indexOfTopLevelItem(root)


class CurveTreeItemDelegate(QStyledItemDelegate):
    """Paint only the curve mnemonic in its assigned display color."""

    def paint(self, painter, option, index) -> None:  # noqa: D401 - Qt API
        mnemonic = str(index.data(ProjectPanel.CURVE_MNEMONIC_ROLE) or "")
        color_text = str(index.data(ProjectPanel.CURVE_COLOR_ROLE) or "")
        if not mnemonic or not color_text:
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        full_text = styled.text
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else None
        if style is None:
            super().paint(painter, option, index)
            return

        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            styled,
            painter,
            styled.widget,
        )
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            styled,
            styled.widget,
        )

        font_metrics = QFontMetrics(styled.font)
        baseline = (
            text_rect.top()
            + (text_rect.height() - font_metrics.height()) // 2
            + font_metrics.ascent()
        )
        x = text_rect.left()

        painter.save()
        painter.setFont(styled.font)
        painter.setPen(QColor(color_text))
        painter.drawText(x, baseline, mnemonic)
        x += font_metrics.horizontalAdvance(mnemonic)

        remainder = (
            full_text[len(mnemonic):]
            if full_text.startswith(mnemonic)
            else full_text
        )
        if styled.state & QStyle.StateFlag.State_Selected:
            neutral = styled.palette.color(QPalette.ColorRole.HighlightedText)
        else:
            neutral = styled.palette.color(QPalette.ColorRole.Text)
        painter.setPen(neutral)
        available = max(0, text_rect.right() - x + 1)
        remainder = font_metrics.elidedText(
            remainder,
            Qt.TextElideMode.ElideRight,
            available,
        )
        painter.drawText(x, baseline, remainder)
        painter.restore()


class ProjectPanel(QWidget):
    """Display segment workflow state and emit navigation intentions."""

    segmentSelected = Signal(str)
    workflowNodeActivated = Signal(str, str, str)
    addImagesRequested = Signal()
    removeRequested = Signal(str)
    moveUpRequested = Signal(str)
    moveDownRequested = Signal(str)

    SEGMENT_ID_ROLE = int(Qt.ItemDataRole.UserRole)
    NODE_TYPE_ROLE = SEGMENT_ID_ROLE + 1
    CURVE_ID_ROLE = SEGMENT_ID_ROLE + 2
    CURVE_MNEMONIC_ROLE = SEGMENT_ID_ROLE + 3
    CURVE_COLOR_ROLE = SEGMENT_ID_ROLE + 4

    NODE_SEGMENT = "segment"
    NODE_PREPROCESSING = "preprocessing"
    NODE_DEPTH = "depth_calibration"
    NODE_CURVE = "curve"
    NODE_X = "x_calibration"
    NODE_DIGITIZATION = "digitization"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_segment_id: str | None = None

        self.project_label = QLabel("No project")
        self.project_label.setWordWrap(True)

        # Keep the historical attribute name ``segment_list`` so the rest of
        # the application and older tests do not need a mechanical rename.
        self.segment_list = SegmentTreeWidget()
        self.segment_tree = self.segment_list
        self.segment_list.setHeaderHidden(True)
        self.segment_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.segment_list.setRootIsDecorated(True)
        self.segment_list.setUniformRowHeights(True)
        self.segment_list.setIndentation(18)
        self.segment_list.setItemDelegate(CurveTreeItemDelegate(self.segment_list))

        # Keep Add image as the prominent, novice-friendly action. Secondary
        # actions operate on the selected segment and are grouped compactly
        # below it as icon-only controls, matching the rest of the application.
        self.add_button = QPushButton("Add image")
        self.add_button.setIcon(app_icon("image"))
        self.add_button.setIconSize(QSize(17, 17))
        self.add_button.setMinimumHeight(30)
        self.add_button.setToolTip("Add image")
        self.add_button.setAccessibleName("Add image")

        self.remove_button = QToolButton()
        self.remove_button.setText("Remove")
        self.remove_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.remove_button.setIcon(danger_icon("trash"))
        self.remove_button.setIconSize(QSize(18, 18))
        self.remove_button.setFixedSize(32, 32)
        self.remove_button.setAccessibleName("Remove selected image")
        self.remove_button.setToolTip("Remove selected image")

        self.up_button = QToolButton()
        self.up_button.setText("Move up")
        self.up_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.up_button.setIcon(app_icon("up-long"))
        self.up_button.setIconSize(QSize(18, 18))
        self.up_button.setFixedSize(32, 32)
        self.up_button.setAccessibleName("Move image up")

        self.down_button = QToolButton()
        self.down_button.setText("Move down")
        self.down_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.down_button.setIcon(app_icon("down-long"))
        self.down_button.setIconSize(QSize(18, 18))
        self.down_button.setFixedSize(32, 32)
        self.down_button.setAccessibleName("Move image down")

        segment_action_row = QHBoxLayout()
        segment_action_row.setContentsMargins(0, 0, 0, 0)
        segment_action_row.setSpacing(6)
        segment_action_row.addWidget(self.remove_button)
        segment_action_row.addWidget(self.up_button)
        segment_action_row.addWidget(self.down_button)
        segment_action_row.addStretch(1)
        self.segment_action_row = segment_action_row

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addWidget(self.project_label)
        layout.addWidget(self.segment_list, 1)
        layout.addWidget(self.add_button)
        layout.addLayout(segment_action_row)

        self.segment_list.currentItemChanged.connect(
            self._on_selection_changed
        )
        self.segment_list.itemClicked.connect(
            self._on_item_clicked
        )
        self.add_button.clicked.connect(self.addImagesRequested)
        self.remove_button.clicked.connect(self._emit_remove)
        self.up_button.clicked.connect(self._emit_up)
        self.down_button.clicked.connect(self._emit_down)
        self.set_controls_enabled(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        self.add_button.setEnabled(enabled)
        selected = self.current_segment_id() is not None
        self.remove_button.setEnabled(enabled and selected)
        self.up_button.setEnabled(enabled and selected)
        self.down_button.setEnabled(enabled and selected)

    def set_project(
        self,
        project: LogProject | None,
        active_segment_id: str | None = None,
        workflow_summaries: Sequence[SegmentWorkflowSummary] = (),
        depth_intervals: Mapping[
            str,
            tuple[float, float, DepthUnit],
        ] | None = None,
    ) -> None:
        """Rebuild the tree while preserving expanded curves in the active segment."""
        expanded_curves = self._expanded_curve_keys()
        selected_key = self._current_item_key()

        self.segment_list.blockSignals(True)
        self.segment_list.clear()
        self._active_segment_id = active_segment_id

        if project is None:
            self.project_label.setText("No project")
            self.segment_list.blockSignals(False)
            self.set_controls_enabled(False)
            return

        self.project_label.setText(project.name)
        summary_by_segment = {
            summary.segment_id: summary
            for summary in workflow_summaries
        }

        active_root: QTreeWidgetItem | None = None
        restore_item: QTreeWidgetItem | None = None

        for segment in sorted(
            project.segments,
            key=lambda item: item.order,
        ):
            summary = summary_by_segment.get(segment.segment_id)
            root_text = f"{segment.order:02d} · {segment.name}"
            if depth_intervals is not None:
                interval = depth_intervals.get(segment.segment_id)
                root_text += (
                    " · Depth not calibrated"
                    if interval is None
                    else f" · {self._format_depth_interval(interval)}"
                )
            root = self._make_item(
                text=root_text,
                segment_id=segment.segment_id,
                node_type=self.NODE_SEGMENT,
                state=(
                    summary.state
                    if summary is not None
                    else (
                        WorkflowState.ERROR
                        if segment.source_missing
                        else WorkflowState.NOT_STARTED
                    )
                ),
            )
            root.setToolTip(
                0,
                (
                    f"{segment.source_path}\n"
                    + (
                        "Depth interval: not calibrated\n"
                        if depth_intervals is not None
                        and segment.segment_id not in depth_intervals
                        else (
                            "Depth interval: "
                            f"{self._format_depth_interval(depth_intervals[segment.segment_id])}\n"
                            if depth_intervals is not None
                            else ""
                        )
                    )
                    + self._state_tooltip(
                        summary.state
                        if summary is not None
                        else WorkflowState.NOT_STARTED
                    )
                ),
            )
            self.segment_list.addTopLevelItem(root)

            if summary is not None:
                self._populate_segment(root, summary, expanded_curves)

            root.setExpanded(segment.segment_id == active_segment_id)
            if segment.segment_id == active_segment_id:
                active_root = root

            if selected_key is not None:
                restore_item = (
                    self._find_item_by_key(root, selected_key)
                    or restore_item
                )

        target = (
            restore_item
            if (
                restore_item is not None
                and self._item_segment_id(restore_item) == active_segment_id
            )
            else active_root
        )
        if target is None and self.segment_list.topLevelItemCount():
            target = self.segment_list.topLevelItem(0)

        if target is not None:
            self.segment_list.setCurrentItem(target)

        self.segment_list.blockSignals(False)
        self.set_controls_enabled(True)

    @staticmethod
    def _format_depth_interval(
        interval: tuple[float, float, DepthUnit],
    ) -> str:
        minimum, maximum, unit = interval
        unit_text = "m" if unit is DepthUnit.METERS else "ft"
        return f"{minimum:g}–{maximum:g} {unit_text}"

    def current_segment_id(self) -> str | None:
        return self._item_segment_id(
            self.segment_list.currentItem()
        )

    def select_segment(self, segment_id: str | None) -> None:
        if segment_id is None:
            self.segment_list.clearSelection()
            return

        self.segment_list.blockSignals(True)
        target = None
        for row in range(self.segment_list.topLevelItemCount()):
            item = self.segment_list.topLevelItem(row)
            is_target = (
                self._item_segment_id(item) == segment_id
            )
            item.setExpanded(is_target)
            if is_target:
                target = item
        if target is not None:
            self.segment_list.setCurrentItem(target)
        self._active_segment_id = segment_id
        self.segment_list.blockSignals(False)
        self.set_controls_enabled(True)


    def focus_digitization_curve(
        self,
        segment_id: str,
        curve_id: str,
    ) -> None:
        """Show the active Digitization curve without rebuilding the tree.

        The owning segment remains expanded, the previously focused curve branch
        is collapsed, and the new curve's Digitization node becomes the current
        item. This mirrors a curve change made in the Digitization panel while
        preserving the rest of the Project Explorer state.
        """
        target_root: QTreeWidgetItem | None = None
        target_curve: QTreeWidgetItem | None = None
        target_digitization: QTreeWidgetItem | None = None

        for row in range(self.segment_list.topLevelItemCount()):
            root = self.segment_list.topLevelItem(row)
            if self._item_segment_id(root) != segment_id:
                continue
            target_root = root
            for index in range(root.childCount()):
                child = root.child(index)
                if (
                    str(child.data(0, self.NODE_TYPE_ROLE) or "")
                    != self.NODE_CURVE
                    or str(child.data(0, self.CURVE_ID_ROLE) or "")
                    != curve_id
                ):
                    continue
                target_curve = child
                for child_index in range(child.childCount()):
                    candidate = child.child(child_index)
                    if (
                        str(candidate.data(0, self.NODE_TYPE_ROLE) or "")
                        == self.NODE_DIGITIZATION
                    ):
                        target_digitization = candidate
                        break
                break
            break

        if target_root is None or target_curve is None:
            return

        previous = self.segment_list.blockSignals(True)
        try:
            target_root.setExpanded(True)
            for index in range(target_root.childCount()):
                child = target_root.child(index)
                if (
                    str(child.data(0, self.NODE_TYPE_ROLE) or "")
                    == self.NODE_CURVE
                ):
                    child.setExpanded(child is target_curve)

            current = target_digitization or target_curve
            self.segment_list.setCurrentItem(current)
            self.segment_list.scrollToItem(
                current,
                QAbstractItemView.ScrollHint.EnsureVisible,
            )
            self._active_segment_id = segment_id
        finally:
            self.segment_list.blockSignals(previous)
        self.set_controls_enabled(True)

    def focus_calibration_curve(
        self,
        segment_id: str,
        curve_id: str,
    ) -> None:
        """Show the active Calibration curve without rebuilding the tree.

        The owning segment remains expanded, the previously focused curve branch
        is collapsed, and the new curve's X-calibration node becomes the current
        item. This mirrors a curve change made in the Calibration panel while
        preserving the rest of the Project Explorer state.
        """
        target_root: QTreeWidgetItem | None = None
        target_curve: QTreeWidgetItem | None = None
        target_x_calibration: QTreeWidgetItem | None = None

        for row in range(self.segment_list.topLevelItemCount()):
            root = self.segment_list.topLevelItem(row)
            if self._item_segment_id(root) != segment_id:
                continue
            target_root = root
            for index in range(root.childCount()):
                child = root.child(index)
                if (
                    str(child.data(0, self.NODE_TYPE_ROLE) or "")
                    != self.NODE_CURVE
                    or str(child.data(0, self.CURVE_ID_ROLE) or "")
                    != curve_id
                ):
                    continue
                target_curve = child
                for child_index in range(child.childCount()):
                    candidate = child.child(child_index)
                    if (
                        str(candidate.data(0, self.NODE_TYPE_ROLE) or "")
                        == self.NODE_X
                    ):
                        target_x_calibration = candidate
                        break
                break
            break

        if target_root is None or target_curve is None:
            return

        previous = self.segment_list.blockSignals(True)
        try:
            target_root.setExpanded(True)
            for index in range(target_root.childCount()):
                child = target_root.child(index)
                if (
                    str(child.data(0, self.NODE_TYPE_ROLE) or "")
                    == self.NODE_CURVE
                ):
                    child.setExpanded(child is target_curve)

            current = target_x_calibration or target_curve
            self.segment_list.setCurrentItem(current)
            self.segment_list.scrollToItem(
                current,
                QAbstractItemView.ScrollHint.EnsureVisible,
            )
            self._active_segment_id = segment_id
        finally:
            self.segment_list.blockSignals(previous)
        self.set_controls_enabled(True)

    def _populate_segment(
        self,
        root: QTreeWidgetItem,
        summary: SegmentWorkflowSummary,
        expanded_curves: set[tuple[str, str]],
    ) -> None:
        preprocessing = self._make_item(
            text="Preprocessing",
            segment_id=summary.segment_id,
            node_type=self.NODE_PREPROCESSING,
            state=summary.preprocessing_state,
        )
        preprocessing.setToolTip(
            0,
            self._preprocessing_tooltip(summary.preprocessing_state),
        )
        root.addChild(preprocessing)

        depth_text = "Depth calibration"
        if summary.depth_point_count:
            unit = (
                "m"
                if summary.depth_unit.value == "M"
                else "ft"
            )
            point_word = (
                "pt"
                if summary.depth_point_count == 1
                else "pts"
            )
            depth_text += (
                f" · {summary.depth_point_count} {point_word} · {unit}"
            )
        depth = self._make_item(
            text=depth_text,
            segment_id=summary.segment_id,
            node_type=self.NODE_DEPTH,
            state=summary.depth_state,
        )
        depth.setToolTip(
            0,
            self._depth_tooltip(summary),
        )
        root.addChild(depth)

        if not summary.curves:
            no_curves = self._make_item(
                text="No curves present",
                segment_id=summary.segment_id,
                node_type=self.NODE_CURVE,
                state=WorkflowState.NOT_STARTED,
            )
            no_curves.setFlags(
                no_curves.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )
            root.addChild(no_curves)
            return

        for curve in summary.curves:
            curve_text = f"{curve.mnemonic} · {curve.name}"
            if curve.unit:
                curve_text += f" · {curve.unit}"
            curve_item = self._make_item(
                text=curve_text,
                segment_id=summary.segment_id,
                node_type=self.NODE_CURVE,
                state=curve.state,
                curve_id=curve.curve_id,
            )
            curve_item.setData(
                0,
                self.CURVE_MNEMONIC_ROLE,
                curve.mnemonic,
            )
            curve_item.setData(
                0,
                self.CURVE_COLOR_ROLE,
                curve.display_color,
            )
            curve_item.setToolTip(
                0,
                f"{curve.mnemonic} — {curve.name}"
                + (f" [{curve.unit}]" if curve.unit else ""),
            )
            root.addChild(curve_item)

            x_item = self._make_item(
                text="X calibration",
                segment_id=summary.segment_id,
                node_type=self.NODE_X,
                state=curve.x_state,
                curve_id=curve.curve_id,
            )
            x_item.setToolTip(
                0,
                self._x_tooltip(curve),
            )
            curve_item.addChild(x_item)

            digitization_item = self._make_item(
                text="Digitization",
                segment_id=summary.segment_id,
                node_type=self.NODE_DIGITIZATION,
                state=curve.digitization_state,
                curve_id=curve.curve_id,
            )
            digitization_item.setToolTip(
                0,
                (
                    "Saved digitization available for this curve."
                    if curve.section_count
                    else "No saved digitization for this curve."
                ),
            )
            curve_item.addChild(digitization_item)

            curve_item.setExpanded(
                (summary.segment_id, curve.curve_id)
                in expanded_curves
            )

    def _make_item(
        self,
        *,
        text: str,
        segment_id: str,
        node_type: str,
        state: WorkflowState,
        curve_id: str = "",
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(
            0,
            self.SEGMENT_ID_ROLE,
            segment_id,
        )
        item.setData(
            0,
            self.NODE_TYPE_ROLE,
            node_type,
        )
        item.setData(
            0,
            self.CURVE_ID_ROLE,
            curve_id,
        )
        item.setIcon(
            0,
            self._status_icon(state),
        )
        return item

    @staticmethod
    def _status_icon(state: WorkflowState):
        if state is WorkflowState.COMPLETE:
            return colored_icon("check", ICON_SUCCESS)
        if state is WorkflowState.INCOMPLETE:
            return colored_icon(
                "triangle-exclamation",
                ICON_WARNING,
            )
        if state is WorkflowState.ERROR:
            return colored_icon("circle-xmark", ICON_DANGER)
        return colored_icon("minus", ICON_MUTED)

    @staticmethod
    def _state_tooltip(state: WorkflowState) -> str:
        if state is WorkflowState.COMPLETE:
            return "Segment completed."
        if state is WorkflowState.INCOMPLETE:
            return "Segment started but still incomplete."
        if state is WorkflowState.ERROR:
            return "Segment contains a missing or invalid project artifact."
        return "Segment not started."

    @staticmethod
    def _preprocessing_tooltip(state: WorkflowState) -> str:
        if state is WorkflowState.COMPLETE:
            return (
                "Rectification applied. Image Enhancement is optional "
                "for segment completion."
            )
        if state is WorkflowState.ERROR:
            return "The source or recorded rectified image cannot be resolved."
        return "Apply four-point rectification to complete Preprocessing."

    @staticmethod
    def _depth_tooltip(summary: SegmentWorkflowSummary) -> str:
        if summary.depth_state is WorkflowState.COMPLETE:
            return (
                f"Valid depth calibration with "
                f"{summary.depth_point_count} reference point(s)."
            )
        if summary.depth_state is WorkflowState.INCOMPLETE:
            return (
                f"Depth calibration has {summary.depth_point_count} point(s); "
                "at least two valid monotonic references are required."
            )
        return "No depth calibration points."

    @staticmethod
    def _x_tooltip(curve: CurveWorkflowSummary) -> str:
        if curve.x_state is WorkflowState.COMPLETE:
            return "Valid two-point horizontal calibration."
        if curve.x_state is WorkflowState.INCOMPLETE:
            return (
                f"Horizontal calibration has {curve.x_point_count} point(s); "
                "two valid points are required."
            )
        return "No horizontal calibration points."

    def _expanded_curve_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for row in range(self.segment_list.topLevelItemCount()):
            root = self.segment_list.topLevelItem(row)
            for index in range(root.childCount()):
                child = root.child(index)
                if (
                    child.data(0, self.NODE_TYPE_ROLE)
                    == self.NODE_CURVE
                    and child.isExpanded()
                ):
                    curve_id = str(
                        child.data(0, self.CURVE_ID_ROLE) or ""
                    )
                    if curve_id:
                        keys.add(
                            (
                                self._item_segment_id(child) or "",
                                curve_id,
                            )
                        )
        return keys

    def _current_item_key(
        self,
    ) -> tuple[str, str, str] | None:
        item = self.segment_list.currentItem()
        if item is None:
            return None
        segment_id = self._item_segment_id(item)
        node_type = str(
            item.data(0, self.NODE_TYPE_ROLE) or ""
        )
        curve_id = str(
            item.data(0, self.CURVE_ID_ROLE) or ""
        )
        if not segment_id:
            return None
        return segment_id, node_type, curve_id

    def _find_item_by_key(
        self,
        root: QTreeWidgetItem,
        key: tuple[str, str, str],
    ) -> QTreeWidgetItem | None:
        segment_id, node_type, curve_id = key
        stack = [root]
        while stack:
            item = stack.pop()
            if (
                self._item_segment_id(item) == segment_id
                and str(
                    item.data(0, self.NODE_TYPE_ROLE) or ""
                )
                == node_type
                and str(
                    item.data(0, self.CURVE_ID_ROLE) or ""
                )
                == curve_id
            ):
                return item
            stack.extend(
                item.child(index)
                for index in range(item.childCount())
            )
        return None

    def _item_segment_id(
        self,
        item: QTreeWidgetItem | None,
    ) -> str | None:
        if item is None:
            return None
        value = item.data(
            0,
            self.SEGMENT_ID_ROLE,
        )
        return str(value) if value else None

    def _on_selection_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        self.set_controls_enabled(True)
        if current is None:
            return
        if (
            str(
                current.data(
                    0,
                    self.NODE_TYPE_ROLE,
                )
                or ""
            )
            != self.NODE_SEGMENT
        ):
            return
        segment_id = self._item_segment_id(current)
        if (
            segment_id
            and segment_id != self._active_segment_id
        ):
            self.segmentSelected.emit(segment_id)

    def _on_item_clicked(
        self,
        item: QTreeWidgetItem,
        _column: int,
    ) -> None:
        node_type = str(
            item.data(0, self.NODE_TYPE_ROLE) or ""
        )
        if node_type == self.NODE_SEGMENT:
            return
        segment_id = self._item_segment_id(item)
        if not segment_id:
            return
        curve_id = str(
            item.data(0, self.CURVE_ID_ROLE) or ""
        )
        self.workflowNodeActivated.emit(
            segment_id,
            node_type,
            curve_id,
        )

    def _emit_remove(self) -> None:
        if segment_id := self.current_segment_id():
            self.removeRequested.emit(segment_id)

    def _emit_up(self) -> None:
        if segment_id := self.current_segment_id():
            self.moveUpRequested.emit(segment_id)

    def _emit_down(self) -> None:
        if segment_id := self.current_segment_id():
            self.moveDownRequested.emit(segment_id)
