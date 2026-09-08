"""Controller for manual digitization creation and saved-trace editing."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent, QUndoStack
from PySide6.QtWidgets import QMessageBox

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.digitization import (
    DigitizationError,
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.processing.manual_digitization import ManualTraceError

from .automatic_trace_eraser import AutomaticTraceEraserOverlay
from .digitization_panel import DigitizationPanel
from .digitization_undo import (
    AppliedDigitizationEditCommand,
    EditSelection,
)
from .viewer import ImageViewer


class ManualDigitizationController(QObject):
    """Coordinate trace creation and saved-node editing without widget domain logic."""

    MIN_INSERT_VERTICAL_GAP_PX = 0.25

    def __init__(
        self,
        service: ProjectService,
        viewer: ImageViewer,
        panel: DigitizationPanel,
        *,
        prepare_capture_callback: Callable[[], None],
        title_refresh_callback: Callable[[], None],
        message_callback: Callable[[str, int], None],
        error_callback: Callable[[str, Exception], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.viewer = viewer
        self.panel = panel
        self._prepare_capture_callback = prepare_capture_callback
        self._title_refresh_callback = title_refresh_callback
        self._message_callback = message_callback
        self._error_callback = error_callback
        self._active_curve_id: str | None = None
        self._active_scale_id: str | None = None
        self._active_scale_offset: int | None = None
        # Manual mode is a session: right-click saves the current section and
        # immediately arms the canvas for the next one. This flag lets the
        # panel keep Finish available after at least one section has been saved,
        # even while the next draft is still empty.
        self._manual_session_has_saved_section = False
        self._editing_curve_id: str | None = None
        self._selected_section_id: str | None = None
        self._selected_node_index: int | None = None
        self._cut_mode = False
        self._join_mode = False
        self._erasing_automatic = False
        self._erase_curve_id: str | None = None
        self._erase_segment_id: str | None = None
        self._erase_drag_start_y_px: float | None = None
        self._erase_drag_current_y_px: float | None = None
        self._erase_image_width_px = 0.0
        self.undo_stack = QUndoStack(self)
        self._undo_context_segment_id: str | None = service.active_segment_id
        self._scale_offset_context_segment_id: str | None = (
            service.active_segment_id
        )
        self._scale_offset_context_curve_id: str | None = (
            panel.selected_curve_id
        )
        self.automatic_trace_eraser = AutomaticTraceEraserOverlay(
            viewer.overlays,
            viewer.image_to_scene,
        )
        self._viewport = viewer.viewport()
        self._viewport.installEventFilter(self)

        action_parent = parent if parent is not None else panel
        self.undo_action = self.undo_stack.createUndoAction(action_parent, "Undo")
        self.redo_action = self.undo_stack.createRedoAction(action_parent, "Redo")
        self.undo_action.setShortcuts(
            QKeySequence.keyBindings(QKeySequence.StandardKey.Undo)
        )
        redo_shortcuts = list(
            QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)
        )
        alternate_redo = QKeySequence("Ctrl+Shift+Z")
        if alternate_redo not in redo_shortcuts:
            redo_shortcuts.append(alternate_redo)
        self.redo_action.setShortcuts(redo_shortcuts)
        self.undo_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.redo_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action_parent.addAction(self.undo_action)
        action_parent.addAction(self.redo_action)

        panel.curveSelectionChanged.connect(self._curve_selected)
        panel.manualStartRequested.connect(self.start_manual_trace)
        panel.manualFinishRequested.connect(self.finish_manual_session)
        panel.manualCancelRequested.connect(self.cancel_manual_trace)
        erase_signal = getattr(panel, "automaticEraseToggled", None)
        if erase_signal is not None:
            erase_signal.connect(self.set_automatic_erase_active)
        panel.editStartRequested.connect(self.start_saved_trace_selection)
        panel.editDoneRequested.connect(self.finish_saved_trace_selection)
        panel.undoRequested.connect(self.undo)
        panel.redoRequested.connect(self.redo)
        panel.nodeDeleteRequested.connect(self.delete_selected_node)
        panel.sectionCutRequested.connect(self.toggle_cut_mode)
        panel.sectionJoinRequested.connect(self.toggle_join_mode)
        panel.sectionDeleteRequested.connect(self.delete_selected_section)
        panel.showSavedCurvesChanged.connect(self._saved_display_changed)
        panel.dimInactiveCurvesChanged.connect(self._saved_display_changed)
        panel.scaleOffsetChanged.connect(self._scale_offset_changed)
        panel.scaleSelectionChanged.connect(self._scale_selection_changed)
        viewer.manualDigitizationPointsChanged.connect(self._points_changed)
        viewer.manualDigitizationPointRejected.connect(self._point_rejected)
        viewer.manualDigitizationFinishRequested.connect(self.finish_manual_trace)
        viewer.saved_digitized_curves.sectionSelected.connect(
            self._saved_section_selected
        )
        viewer.saved_digitized_curves.nodeSelected.connect(
            self._saved_node_selected
        )
        viewer.saved_digitized_curves.nodeInsertRequested.connect(
            self._saved_node_insert_requested
        )
        viewer.saved_digitized_curves.nodeDeleteRequested.connect(
            self._saved_node_delete_requested
        )
        viewer.saved_digitized_curves.nodeMoveCommitted.connect(
            self._saved_node_move_committed
        )
        for signal in (
            self.undo_stack.canUndoChanged,
            self.undo_stack.canRedoChanged,
            self.undo_stack.undoTextChanged,
            self.undo_stack.redoTextChanged,
        ):
            signal.connect(self._sync_undo_state)
        self._sync_undo_state()

    def undo(self) -> None:
        """Undo the most recent persisted digitization edit."""
        if not self.undo_stack.canUndo():
            return
        text = self.undo_stack.undoText()
        self._cut_mode = False
        self._join_mode = False
        self.undo_stack.undo()
        self._message_callback(f"Undo: {text}.", 3000)

    def redo(self) -> None:
        """Redo the most recently undone persisted digitization edit."""
        if not self.undo_stack.canRedo():
            return
        text = self.undo_stack.redoText()
        self._cut_mode = False
        self._join_mode = False
        self.undo_stack.redo()
        self._message_callback(f"Redo: {text}.", 3000)

    def _sync_undo_state(self, *_args) -> None:
        self.panel.set_undo_state(
            self.undo_stack.canUndo(),
            self.undo_stack.canRedo(),
            undo_text=self.undo_stack.undoText(),
            redo_text=self.undo_stack.redoText(),
        )

    def _record_applied_edit(
        self,
        text: str,
        curve_id: str,
        before_curve: DigitizedCurve | None,
        after_curve: DigitizedCurve | None,
        *,
        before_selection: EditSelection | None = None,
        after_selection: EditSelection | None = None,
    ) -> None:
        """Push an already-applied before/after snapshot onto the undo stack."""
        segment_id = self.service.active_segment_id
        if segment_id is None:
            return
        if (
            self.undo_stack.count()
            and self._undo_context_segment_id != segment_id
        ):
            self.undo_stack.clear()
        self._undo_context_segment_id = segment_id
        command = AppliedDigitizationEditCommand(
            text=text,
            segment_id=segment_id,
            curve_id=curve_id,
            before_payload=(
                None if before_curve is None else before_curve.to_dict()
            ),
            after_payload=(
                None if after_curve is None else after_curve.to_dict()
            ),
            before_selection=before_selection or EditSelection(),
            after_selection=after_selection or EditSelection(),
            apply_callback=self._apply_undo_snapshot,
        )
        self.undo_stack.push(command)

    def _apply_undo_snapshot(
        self,
        segment_id: str,
        curve_id: str,
        payload,
        selection: EditSelection,
    ) -> None:
        """Restore one serialized curve snapshot for QUndoStack."""
        if self.service.active_segment_id != segment_id:
            self._message_callback(
                "Undo/redo history belongs to another segment and was not applied.",
                5000,
            )
            return
        try:
            current = self.service.get_digitized_curve(curve_id)
            if payload is None:
                if current is not None:
                    self.service.remove_digitized_curve(curve_id)
                restored = None
            else:
                restored = DigitizedCurve.from_dict(payload)
                self.service.set_digitized_curve(restored)
        except (DigitizationError, KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot apply digitization undo/redo", exc)
            return

        self._cut_mode = False
        self._join_mode = False
        if self._editing_curve_id == curve_id:
            self._selected_section_id = None
            self._selected_node_index = None
            if restored is not None and selection.section_id is not None:
                try:
                    section = restored.get_section(selection.section_id)
                except KeyError:
                    pass
                else:
                    self._selected_section_id = selection.section_id
                    if (
                        selection.node_index is not None
                        and 0 <= selection.node_index < len(section.control_points_px)
                    ):
                        self._selected_node_index = selection.node_index

        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)

    @property
    def active_curve_id(self) -> str | None:
        """Curve currently owning the transient manual draft, if any."""
        return self._active_curve_id

    @property
    def active_scale_offset(self) -> int | None:
        """Scale offset captured when the transient manual draft started."""
        return self._active_scale_offset

    @property
    def active_scale_id(self) -> str | None:
        """Horizontal scale captured when the manual draft started."""
        return self._active_scale_id

    @property
    def editing_curve_id(self) -> str | None:
        """Curve whose persisted sections/nodes are being edited."""
        return self._editing_curve_id

    @property
    def selected_section_id(self) -> str | None:
        return self._selected_section_id

    @property
    def selected_node_index(self) -> int | None:
        return self._selected_node_index

    @property
    def cutting(self) -> bool:
        """Return whether the next selected-path click will split the section."""
        return self._cut_mode

    @property
    def joining(self) -> bool:
        """Return whether the next section click will join with the selected section."""
        return self._join_mode

    @property
    def tracing(self) -> bool:
        return (
            self._active_curve_id is not None
            and self.viewer.manual_digitization_enabled
        )

    @property
    def editing(self) -> bool:
        return self._editing_curve_id is not None

    @property
    def erasing_automatic(self) -> bool:
        return self._erasing_automatic

    @property
    def canvas_capture_active(self) -> bool:
        """Return whether a manual trace or automatic eraser owns the canvas."""
        return self.tracing or self.erasing_automatic

    def refresh(self, *, selected_curve_id: str | None = None) -> None:
        """Synchronize curve list, readiness, saved geometry and edit selection."""
        current_segment_id = self.service.active_segment_id
        if self._scale_offset_context_segment_id != current_segment_id:
            self.panel.set_scale_offset(0)
            self._scale_offset_context_segment_id = current_segment_id
        if (
            self.undo_stack.count()
            and self._undo_context_segment_id != current_segment_id
        ):
            self.undo_stack.clear()
        self._undo_context_segment_id = current_segment_id

        curves = self.service.get_curves()
        valid_curve_ids = {curve.curve_id for curve in curves}
        if self.undo_stack.count() and any(
            getattr(self.undo_stack.command(index), "curve_id", None)
            not in valid_curve_ids
            for index in range(self.undo_stack.count())
        ):
            self.undo_stack.clear()
        wanted = (
            self._active_curve_id
            or self._editing_curve_id
            or self._erase_curve_id
            or selected_curve_id
            or self.panel.selected_curve_id
        )
        valid_ids = {curve.curve_id for curve in curves}
        if wanted not in valid_ids:
            wanted = curves[0].curve_id if curves else None
            if self._editing_curve_id not in valid_ids:
                self._clear_edit_selection()

        self.panel.set_curves(curves, wanted)
        selected_panel_curve_id = self.panel.selected_curve_id
        if (
            not self.tracing
            and not self.editing
            and not self.erasing_automatic
            and self._scale_offset_context_curve_id
            != selected_panel_curve_id
        ):
            self.panel.set_scale_offset(0)
        self._scale_offset_context_curve_id = selected_panel_curve_id
        curve_id = (
            self._active_curve_id
            or self._editing_curve_id
            or self._erase_curve_id
            or self.panel.selected_curve_id
        )
        project_available = self.service.project is not None
        segment = self.service.active_segment
        segment_available = segment is not None
        image_ready = self._image_ready()

        selected_curve = None
        profiles = ()
        if curve_id is not None and segment is not None:
            try:
                selected_curve = self.service.get_curve(curve_id)
                profiles = self.service.get_complete_horizontal_scale_profiles(
                    curve_id,
                    segment,
                )
            except (KeyError, RuntimeError):
                selected_curve = None
                profiles = ()

        saved_section_count = 0
        digitized = None
        if curve_id is not None and segment is not None:
            try:
                digitized = self.service.get_digitized_curve(curve_id, segment)
            except (KeyError, RuntimeError):
                digitized = None
            if digitized is not None:
                saved_section_count = len(digitized.sections)

        readiness_message = "Select a curve to digitize."
        can_start = False
        if curve_id is not None and segment is not None and image_ready:
            try:
                block_reason = self.service.curve_digitization_block_reason(
                    curve_id,
                    segment,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                block_reason = str(exc)
            if block_reason is None:
                curve = self.service.get_curve(curve_id)
                readiness_message = (
                    f"✓ {curve.mnemonic} ready for manual digitization."
                )
                can_start = True
            else:
                # Keep the panel status intentionally compact. The detailed
                # calibration reason is still reported when Start is attempted.
                readiness_message = "⚠ Calibrate before digitizing"

        can_edit = bool(
            curve_id is not None
            and segment is not None
            and image_ready
            and saved_section_count > 0
        )

        selected_node_count = 0
        selected_section_scale_id: str | None = None
        selected_section_offset: int | None = None
        if (
            self.editing
            and digitized is not None
            and self._selected_section_id is not None
        ):
            try:
                selected_section = digitized.get_section(
                    self._selected_section_id
                )
                selected_node_count = len(
                    selected_section.control_points_px
                )
                selected_section_scale_id = selected_section.scale_id
                selected_section_offset = selected_section.scale_offset
            except KeyError:
                self._selected_section_id = None
                self._selected_node_index = None

        preferred_scale_id = (
            self._active_scale_id
            or selected_section_scale_id
            or self.panel.selected_scale_id
        )
        self.panel.set_scale_profiles(
            profiles,
            selected_curve,
            selected_scale_id=preferred_scale_id,
        )

        self.panel.set_context(
            project_available=project_available,
            segment_available=segment_available,
            image_ready=image_ready,
            readiness_message=readiness_message,
            saved_section_count=saved_section_count,
            can_start=can_start,
            can_edit=can_edit,
        )
        self.panel.set_manual_draft_state(
            self.tracing,
            len(self.viewer.manual_digitization_points),
            can_finish_empty_session=self._manual_session_has_saved_section,
        )
        self.panel.set_edit_state(
            self.editing,
            selected_section_id=self._selected_section_id,
            selected_node_index=self._selected_node_index,
            selected_section_node_count=selected_node_count,
            selected_section_scale_id=selected_section_scale_id,
            selected_section_offset=selected_section_offset,
            cutting=self._cut_mode,
            joining=self._join_mode,
        )
        self._refresh_saved_overlay(
            curves,
            active_curve_id=curve_id,
            image_ready=image_ready,
        )

    def set_automatic_erase_active(self, active: bool) -> None:
        """Enable or disable drag-to-erase for the selected automatic curve."""
        if not active:
            self._deactivate_automatic_eraser(refresh=True)
            return
        if self._erasing_automatic:
            return
        if self.tracing or self.editing:
            self._set_panel_automatic_erase_state(False)
            self._message_callback(
                "Finish the current manual digitization operation first.",
                4500,
            )
            return
        if bool(getattr(self.panel, "automatic_active", False)):
            self._set_panel_automatic_erase_state(False)
            self._message_callback(
                "Finish or clear the current automatic draft first.",
                4500,
            )
            return

        curve_id = self.panel.selected_curve_id
        segment = self.service.active_segment
        if curve_id is None or segment is None or not self._image_ready():
            self._set_panel_automatic_erase_state(False)
            self._message_callback(
                "Select a saved automatic curve on a rectified segment first.",
                5000,
            )
            return

        try:
            digitized = self.service.get_digitized_curve(curve_id, segment)
            image = self.viewer.displayed_image_array()
        except (KeyError, RuntimeError, ValueError) as exc:
            self._set_panel_automatic_erase_state(False)
            self._error_callback("Cannot start automatic trace eraser", exc)
            return

        if digitized is None or not self._has_automatic_geometry(digitized):
            self._set_panel_automatic_erase_state(False)
            self._message_callback(
                "The selected curve has no saved automatic trace to erase.",
                5000,
            )
            return

        self._prepare_capture_callback()
        self.viewer.set_manual_digitization_enabled(False)
        self.viewer.set_saved_trace_selection_enabled(False)
        self._erasing_automatic = True
        self._erase_curve_id = curve_id
        self._erase_segment_id = segment.segment_id
        self._erase_drag_start_y_px = None
        self._erase_drag_current_y_px = None
        self._erase_image_width_px = float(image.shape[1])
        self.automatic_trace_eraser.clear()
        self._set_panel_automatic_erase_state(True)
        self._viewport.setCursor(Qt.CursorShape.CrossCursor)
        self._message_callback(
            "Automatic eraser active. Drag vertically anywhere on the image; "
            "release to erase automatic trace geometry in that Y range.",
            6500,
        )

    def _set_panel_automatic_erase_state(self, active: bool) -> None:
        setter = getattr(self.panel, "set_automatic_erase_state", None)
        if setter is not None:
            setter(bool(active))

    @staticmethod
    def _has_automatic_geometry(digitized: DigitizedCurve) -> bool:
        return any(
            section.source is TraceSource.AUTOMATIC
            and (
                len(section.raw_samples_px) >= 2
                or len(section.control_points_px) >= 2
            )
            for section in digitized.sections
        )

    def _deactivate_automatic_eraser(self, *, refresh: bool) -> None:
        curve_id = self._erase_curve_id or self.panel.selected_curve_id
        self._erasing_automatic = False
        self._erase_curve_id = None
        self._erase_segment_id = None
        self._erase_drag_start_y_px = None
        self._erase_drag_current_y_px = None
        self._erase_image_width_px = 0.0
        self.automatic_trace_eraser.clear()
        self._set_panel_automatic_erase_state(False)
        try:
            self._viewport.unsetCursor()
        except RuntimeError:
            pass
        if refresh:
            self.refresh(selected_curve_id=curve_id)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if not self._erasing_automatic or watched is not self._viewport:
            return False

        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._begin_automatic_erase_drag(mouse_event)
                return True
        elif event_type == QEvent.Type.MouseMove:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and self._erase_drag_start_y_px is not None
            ):
                self._update_automatic_erase_drag(mouse_event)
                return True
        elif event_type == QEvent.Type.MouseButtonRelease:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._finish_automatic_erase_drag(mouse_event)
                return True
        return False

    def _begin_automatic_erase_drag(self, event: QMouseEvent) -> None:
        """Start a vertical erase range anywhere inside the image raster.

        Erase mode is intentionally a Y-range selection tool, not a trace
        hit-testing tool. The drag X position is therefore irrelevant: saved
        automatic geometry for the selected curve is clipped only by the
        selected vertical interval.
        """
        if not self._erase_context_is_current():
            self._deactivate_automatic_eraser(refresh=True)
            self._message_callback(
                "Automatic eraser was cancelled because the context changed.",
                4500,
            )
            return
        image_point = self.viewer.viewport_to_image(event.position())
        if image_point is None:
            return

        start_y = float(image_point.y())
        self._erase_drag_start_y_px = start_y
        self._erase_drag_current_y_px = start_y
        self.automatic_trace_eraser.set_range(
            start_y,
            start_y,
            image_width_px=self._erase_image_width_px,
        )
        self._viewport.setCursor(Qt.CursorShape.SizeVerCursor)

    def _update_automatic_erase_drag(self, event: QMouseEvent) -> None:
        image_point = self.viewer.viewport_to_image(event.position())
        if image_point is None or self._erase_drag_start_y_px is None:
            return
        self._erase_drag_current_y_px = float(image_point.y())
        self.automatic_trace_eraser.set_range(
            self._erase_drag_start_y_px,
            self._erase_drag_current_y_px,
            image_width_px=self._erase_image_width_px,
        )

    def _finish_automatic_erase_drag(self, event: QMouseEvent) -> None:
        start_y = self._erase_drag_start_y_px
        if start_y is None:
            return
        image_point = self.viewer.viewport_to_image(event.position())
        if image_point is not None:
            self._erase_drag_current_y_px = float(image_point.y())
        end_y = self._erase_drag_current_y_px
        self._erase_drag_start_y_px = None
        self._erase_drag_current_y_px = None
        self.automatic_trace_eraser.clear()
        self._viewport.setCursor(Qt.CursorShape.CrossCursor)
        if end_y is None:
            return
        self._apply_automatic_erase_range(start_y, end_y)

    def _erase_context_is_current(self) -> bool:
        return bool(
            self._erasing_automatic
            and self.service.active_segment_id == self._erase_segment_id
            and self.panel.selected_curve_id == self._erase_curve_id
        )

    def _apply_automatic_erase_range(
        self,
        start_y_px: float,
        end_y_px: float,
    ) -> None:
        curve_id = self._erase_curve_id
        if curve_id is None or not self._erase_context_is_current():
            self._deactivate_automatic_eraser(refresh=True)
            return

        minimum_y, maximum_y = sorted(
            (float(start_y_px), float(end_y_px))
        )
        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                raise DigitizationError(
                    "The selected curve no longer has saved geometry."
                )
            digitized = DigitizedCurve.from_dict(existing.to_dict())
            affected = digitized.erase_automatic_range(
                minimum_y,
                maximum_y,
            )
            if affected == 0:
                self._message_callback(
                    "The selected Y range contains no automatic trace.",
                    4000,
                )
                return

            after_curve: DigitizedCurve | None
            if digitized.sections:
                self.service.set_digitized_curve(digitized)
                after_curve = digitized
            else:
                self.service.remove_digitized_curve(curve_id)
                after_curve = None
            self._record_applied_edit(
                "Erase automatic range",
                curve_id,
                existing,
                after_curve,
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot erase automatic trace range", exc)
            return

        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"Automatic trace erased for {curve.mnemonic} between "
            f"Y={minimum_y:.1f} and Y={maximum_y:.1f} px.",
            5000,
        )
        if after_curve is None or not self._has_automatic_geometry(after_curve):
            self._deactivate_automatic_eraser(refresh=True)

    def start_manual_trace(self) -> None:
        """Begin a new transient manual section for the selected ready curve."""
        if self.erasing_automatic:
            self._message_callback(
                "Turn off the automatic eraser before starting a manual trace.",
                4500,
            )
            return
        if self.editing:
            self._message_callback(
                "Finish saved-trace selection before starting a new trace.",
                5000,
            )
            return

        curve_id = self.panel.selected_curve_id
        if curve_id is None:
            self._message_callback("Select a curve before digitizing.", 5000)
            return
        if not self._image_ready():
            self._message_callback(
                "Apply rectification before manual digitization.",
                5000,
            )
            self.refresh(selected_curve_id=curve_id)
            return

        try:
            block_reason = self.service.curve_digitization_block_reason(curve_id)
            curve = self.service.get_curve(curve_id)
            scale_id = self.panel.selected_scale_id
            scale_calibration = self.service.get_horizontal_calibration(
                curve_id,
                scale_id=scale_id,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot start manual digitization", exc)
            return

        if block_reason is not None:
            self._message_callback(block_reason, 6000)
            self.refresh(selected_curve_id=curve_id)
            return
        if scale_calibration is None:
            self._message_callback(
                "Select a complete horizontal scale before digitizing.",
                5000,
            )
            self.refresh(selected_curve_id=curve_id)
            return

        self._prepare_capture_callback()
        self.viewer.clear_manual_digitization_draft()
        self.viewer.set_manual_digitization_color(curve.display_color)
        self._active_curve_id = curve_id
        self._active_scale_id = scale_id
        self._active_scale_offset = self.panel.scale_offset
        self._manual_session_has_saved_section = False
        self.viewer.set_manual_digitization_enabled(True)
        self.panel.set_manual_draft_state(
            True,
            0,
            can_finish_empty_session=False,
        )
        self._refresh_saved_overlay(
            self.service.get_curves(),
            active_curve_id=curve_id,
            image_ready=True,
        )
        self._message_callback(
            f"Manual trace started for {curve.mnemonic}"
            + (
                f" · {self._active_scale_offset:+d} scale"
                if self._active_scale_offset
                else ""
            )
            + ". Add at least two points. Right-click saves each section "
            "and keeps manual mode active.",
            6000,
        )

    def finish_manual_trace(self) -> None:
        """Save the current manual section and keep manual mode active.

        This method is bound to right-click on the image. Finishing one section
        clears only the transient draft; the selected curve, scale, offset and
        canvas capture mode remain active so the next left-click immediately
        starts another independent manual section.
        """
        if not self.tracing:
            return
        if not self.viewer.manual_digitization_points:
            # Right-click on an empty armed canvas is intentionally a no-op.
            return
        self._persist_manual_section(continue_session=True)

    def finish_manual_session(self) -> None:
        """Save any valid current draft and leave continuous manual mode.

        The visible Finish button ends the whole manual session. If the user has
        already right-clicked the last section, the draft is empty and Finish
        simply leaves manual mode. If a valid section is still in progress, it
        is saved first. A one-point draft is retained so it cannot be discarded
        silently.
        """
        if not self.tracing:
            return

        node_count = len(self.viewer.manual_digitization_points)
        if node_count == 0:
            curve_id = self._active_curve_id
            self._end_manual_session()
            self.refresh(selected_curve_id=curve_id)
            self._message_callback("Manual digitization finished.", 3500)
            return
        if node_count < 2:
            self._message_callback(
                "Add a second point, or use Cancel to discard the unfinished "
                "manual section.",
                5000,
            )
            return

        self._persist_manual_section(continue_session=False)

    def _persist_manual_section(self, *, continue_session: bool) -> None:
        """Persist the current draft using the established manual-override rules."""
        curve_id = self._active_curve_id
        if curve_id is None:
            return

        try:
            section = self.viewer.manual_digitization.draft.finish()
            section.set_scale_id(
                self._active_scale_id or self.panel.selected_scale_id
            )
            section.set_scale_offset(self._active_scale_offset or 0)
            existing = self.service.get_digitized_curve(curve_id)
            digitized = (
                DigitizedCurve.from_dict(existing.to_dict())
                if existing is not None
                else DigitizedCurve(curve_id=curve_id)
            )
            automatic_sections_affected = digitized.apply_manual_override(
                section
            )
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                (
                    "Apply manual correction"
                    if automatic_sections_affected
                    else "Add manual section"
                ),
                curve_id,
                existing,
                digitized,
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            ManualTraceError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            if isinstance(exc, ManualTraceError):
                self._message_callback(str(exc), 5000)
            else:
                self._error_callback("Cannot finish manual trace", exc)
            return

        self.viewer.clear_manual_digitization_draft()
        self._title_refresh_callback()

        offset_suffix = (
            f" Offset {section.scale_offset:+d} scale."
            if section.scale_offset
            else ""
        )
        if automatic_sections_affected:
            saved_message = (
                f"Manual correction saved for {curve.mnemonic}; automatic "
                f"trace replaced between Y={section.start_y_px:.1f} and "
                f"Y={section.end_y_px:.1f} px."
                + offset_suffix
            )
        else:
            saved_message = (
                f"Manual section saved for {curve.mnemonic} "
                f"({len(section.control_points_px)} control points)."
                + offset_suffix
            )

        if continue_session:
            self._manual_session_has_saved_section = True
            self.viewer.set_manual_digitization_enabled(True)
            self.refresh(selected_curve_id=curve_id)
            self._message_callback(
                saved_message
                + " Continue with the next section, or press Finish to leave "
                "manual mode.",
                6500,
            )
            return

        self._end_manual_session()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            saved_message + " Manual digitization finished.",
            5500,
        )

    def _end_manual_session(self) -> None:
        """Leave manual creation mode without changing already saved sections."""
        self.viewer.set_manual_digitization_enabled(False)
        self.viewer.clear_manual_digitization_draft()
        self._active_curve_id = None
        self._active_scale_id = None
        self._active_scale_offset = None
        self._manual_session_has_saved_section = False
        self.panel.set_manual_draft_state(
            False,
            0,
            can_finish_empty_session=False,
        )

    def cancel_manual_trace(self) -> None:
        """Discard the transient draft without changing persistent project data."""
        if (
            self._active_curve_id is None
            and not self.viewer.manual_digitization_points
        ):
            return
        curve_id = self._active_curve_id or self.panel.selected_curve_id
        self.viewer.set_manual_digitization_enabled(False)
        self.viewer.clear_manual_digitization_draft()
        self._active_curve_id = None
        self._active_scale_id = None
        self._active_scale_offset = None
        self._manual_session_has_saved_section = False
        self.panel.set_manual_draft_state(
            False,
            0,
            can_finish_empty_session=False,
        )
        self.refresh(selected_curve_id=curve_id)
        self._message_callback("Manual trace cancelled.", 3500)

    def start_saved_trace_selection(self) -> None:
        """Enter edit mode for the currently selected saved curve."""
        if self.erasing_automatic:
            self._message_callback(
                "Turn off the automatic eraser before editing saved nodes.",
                4500,
            )
            return
        if self.tracing:
            self._message_callback(
                "Finish or cancel the current manual trace first.",
                5000,
            )
            return

        curve_id = self.panel.selected_curve_id
        segment = self.service.active_segment
        if curve_id is None or segment is None or not self._image_ready():
            self._message_callback(
                "Select a saved digitized curve on a rectified segment first.",
                5000,
            )
            return

        try:
            digitized = self.service.get_digitized_curve(curve_id, segment)
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot edit saved trace", exc)
            return

        if digitized is None or not digitized.sections:
            self._message_callback(
                "This curve has no saved sections to edit.",
                4500,
            )
            self.refresh(selected_curve_id=curve_id)
            return

        self._prepare_capture_callback()
        self.viewer.set_manual_digitization_enabled(False)
        self.viewer.clear_manual_digitization_draft()
        self.viewer.set_saved_trace_selection_enabled(True)
        self._editing_curve_id = curve_id
        self._selected_section_id = None
        self._selected_node_index = None
        self._cut_mode = False
        self._join_mode = False
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"Saved-trace editing started for {curve.mnemonic}. "
            "Select a section, then edit its nodes or delete the whole section.",
            5500,
        )

    def finish_saved_trace_selection(self) -> None:
        """Leave saved-trace edit mode."""
        if not self.editing:
            return
        curve_id = self._editing_curve_id
        self._cut_mode = False
        self._join_mode = False
        self.viewer.set_saved_trace_selection_enabled(False)
        self._clear_edit_selection()
        self.panel.set_scale_offset(0)
        self.refresh(selected_curve_id=curve_id)
        self._message_callback("Saved-trace selection finished.", 3500)

    def abort_for_context_change(self) -> None:
        """Drop transient creation and edit-selection state on context changes."""
        if self.erasing_automatic:
            self._deactivate_automatic_eraser(refresh=False)
        if (
            self._active_curve_id is not None
            or self.viewer.manual_digitization_points
        ):
            self.viewer.set_manual_digitization_enabled(False)
            self.viewer.clear_manual_digitization_draft()
        self._cut_mode = False
        self._join_mode = False
        self.viewer.set_saved_trace_selection_enabled(False)
        self.viewer.clear_saved_digitized_curves()
        self._active_curve_id = None
        self._active_scale_id = None
        self._active_scale_offset = None
        self._manual_session_has_saved_section = False
        self._clear_edit_selection()
        self.undo_stack.clear()
        self._undo_context_segment_id = self.service.active_segment_id
        self.refresh()

    def _refresh_saved_overlay(
        self,
        curves,
        *,
        active_curve_id: str | None,
        image_ready: bool,
    ) -> None:
        """Restore saved digitizations and current selection for the segment."""
        segment = self.service.active_segment
        if segment is None or not image_ready or not self.viewer.has_image:
            self.viewer.clear_saved_digitized_curves()
            return

        digitizations = []
        for curve in curves:
            try:
                digitized = self.service.get_digitized_curve(
                    curve.curve_id,
                    segment,
                )
            except (KeyError, RuntimeError):
                digitized = None
            if digitized is not None and digitized.sections:
                digitizations.append(digitized)

        self.viewer.set_saved_digitized_curves(
            curves,
            digitizations,
            active_curve_id=active_curve_id,
            show_all_saved=self.panel.show_saved_curves,
            dim_inactive=self.panel.dim_inactive_curves,
            selection_enabled=self.editing,
            selected_section_id=self._selected_section_id,
            selected_node_index=self._selected_node_index,
        )

    def _saved_section_selected(self, curve_id: str, section_id: str) -> None:
        if not self.editing or curve_id != self._editing_curve_id:
            return
        digitized = self.service.get_digitized_curve(curve_id)
        if digitized is None:
            return
        try:
            digitized.get_section(section_id)
        except KeyError:
            return

        if self._join_mode:
            self._join_selected_section_with(curve_id, section_id)
            return

        self._selected_section_id = section_id
        self._selected_node_index = None
        self._cut_mode = False
        self._join_mode = False
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            "Saved section selected. Edit its nodes, cut or join it, or use "
            "Delete Selected Section to remove the complete section.",
            3500,
        )

    def _saved_node_selected(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
    ) -> None:
        if not self.editing or curve_id != self._editing_curve_id:
            return
        if self._cut_mode:
            self._message_callback(
                "Cut mode is active. Click the selected line between nodes, "
                "or use Cancel Cut to return to node editing.",
                4000,
            )
            return
        if self._join_mode:
            self._message_callback(
                "Join mode is active. Click another neighboring section, "
                "or use Cancel Join to return to node editing.",
                4000,
            )
            return
        digitized = self.service.get_digitized_curve(curve_id)
        if digitized is None:
            return
        try:
            section = digitized.get_section(section_id)
        except KeyError:
            return
        if not 0 <= int(node_index) < len(section.control_points_px):
            return
        self._selected_section_id = section_id
        self._selected_node_index = int(node_index)
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"Control node {int(node_index) + 1} selected. Drag it to move it, "
            "right-click it to delete it, or click the line to insert another.",
            3000,
        )

    def _saved_node_insert_requested(
        self,
        curve_id: str,
        section_id: str,
        x_px: float,
        y_px: float,
    ) -> None:
        """Insert one control node between existing nodes of the selected section."""
        if (
            not self.editing
            or curve_id != self._editing_curve_id
            or section_id != self._selected_section_id
        ):
            return

        if self._cut_mode:
            self._cut_selected_section_at(
                curve_id,
                section_id,
                float(x_px),
                float(y_px),
            )
            return
        if self._join_mode:
            self._message_callback(
                "Join mode is active. Click another neighboring section, not the line.",
                4000,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return

            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            points = list(section.control_points_px)
            if len(points) < 2:
                return

            candidate = TracePoint(float(x_px), float(y_px))
            y_values = [point.y_px for point in points]
            insertion_index = bisect_left(y_values, candidate.y_px)

            # Insertion is deliberately restricted to an interval between two
            # existing controls. End extrapolation would silently lengthen the
            # saved section, which belongs to later section-management tools.
            if insertion_index <= 0 or insertion_index >= len(points):
                self._message_callback(
                    "Insert a node between existing control nodes.",
                    4000,
                )
                return

            previous_y = points[insertion_index - 1].y_px
            next_y = points[insertion_index].y_px
            gap = self.MIN_INSERT_VERTICAL_GAP_PX
            if (
                candidate.y_px - previous_y < gap
                or next_y - candidate.y_px < gap
            ):
                self._message_callback(
                    "Node insertion rejected: choose a point farther from an "
                    "existing control node.",
                    4500,
                )
                return

            points.insert(insertion_index, candidate)
            section.replace_control_points(points)

            # Rebuild from serialized data so automatic raw samples, processing
            # parameters and canonical section ordering are all preserved.
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                "Insert node",
                curve_id,
                existing,
                digitized,
                before_selection=EditSelection(
                    self._selected_section_id, self._selected_node_index
                ),
                after_selection=EditSelection(section_id, insertion_index),
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot insert digitization node", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._selected_section_id = section_id
        self._selected_node_index = insertion_index
        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"{curve.mnemonic} node {insertion_index + 1} inserted at "
            f"({candidate.x_px:.1f}, {candidate.y_px:.1f}) px.",
            3500,
        )

    def _saved_node_move_committed(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
        x_px: float,
        y_px: float,
    ) -> None:
        """Persist one completed node drag while preserving raw auto data."""
        if not self.editing or curve_id != self._editing_curve_id:
            return
        if self._cut_mode:
            self.refresh(selected_curve_id=curve_id)
            self._message_callback(
                "Cut mode is active. Click the selected line between nodes, "
                "or cancel Cut mode before moving a node.",
                4500,
            )
            return
        if self._join_mode:
            self.refresh(selected_curve_id=curve_id)
            self._message_callback(
                "Join mode is active. Select another neighboring section or cancel Join.",
                4500,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return

            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            index = int(node_index)
            if not 0 <= index < len(section.control_points_px):
                return

            points = list(section.control_points_px)
            points[index] = TracePoint(float(x_px), float(y_px))
            section.replace_control_points(points)

            # Rebuild once after the edit so section ordering remains canonical
            # if an endpoint move changes a section's start Y.  Automatic raw
            # samples and processing settings survive this round-trip unchanged.
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                "Move node",
                curve_id,
                existing,
                digitized,
                before_selection=EditSelection(section_id, index),
                after_selection=EditSelection(section_id, index),
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot move digitization node", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._selected_section_id = section_id
        self._selected_node_index = int(node_index)
        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"{curve.mnemonic} node {int(node_index) + 1} moved to "
            f"({float(x_px):.1f}, {float(y_px):.1f}) px.",
            3500,
        )

    def _saved_node_delete_requested(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
    ) -> None:
        """Delete the control node targeted by a canvas right-click.

        Right-click is intentionally direct: the node does not need a prior
        left-click selection. The section is already selected because only its
        control nodes are rendered in edit mode.
        """
        if not self.editing or curve_id != self._editing_curve_id:
            return
        if self._cut_mode:
            self._message_callback(
                "Cancel Cut mode before deleting a control node.",
                4000,
            )
            return
        if self._join_mode:
            self._message_callback(
                "Cancel Join mode before deleting a control node.",
                4000,
            )
            return
        digitized = self.service.get_digitized_curve(curve_id)
        if digitized is None:
            return
        try:
            section = digitized.get_section(section_id)
        except KeyError:
            return
        index = int(node_index)
        if not 0 <= index < len(section.control_points_px):
            return

        self._selected_section_id = section_id
        self._selected_node_index = index
        self.delete_selected_node()

    def delete_selected_node(self) -> None:
        """Delete the selected editable control node from one saved section.

        A continuous section must retain at least two control nodes. Deleting an
        entire two-node section is intentionally deferred to the later section-
        management step rather than making a node operation silently remove it.
        """
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        node_index = self._selected_node_index
        if (
            not self.editing
            or curve_id is None
            or section_id is None
            or node_index is None
        ):
            return
        if self._cut_mode:
            self._message_callback(
                "Cancel Cut mode before deleting a control node.",
                4000,
            )
            return
        if self._join_mode:
            self._message_callback(
                "Cancel Join mode before deleting a control node.",
                4000,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return

            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            points = list(section.control_points_px)
            index = int(node_index)
            if not 0 <= index < len(points):
                return

            if len(points) <= 2:
                self._message_callback(
                    "A trace section must keep at least two control nodes. "
                    "Section removal will be handled separately.",
                    5000,
                )
                self.refresh(selected_curve_id=curve_id)
                return

            removed = points.pop(index)
            section.replace_control_points(points)

            # Round-trip the complete curve so automatic raw samples and their
            # processing settings remain untouched while section ordering stays
            # canonical after the edit.
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                "Delete node",
                curve_id,
                existing,
                digitized,
                before_selection=EditSelection(section_id, index),
                after_selection=EditSelection(section_id, None),
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot delete digitization node", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        # Keep the section selected but clear node selection. This prevents an
        # accidental double-delete and makes the next destructive action explicit.
        self._selected_section_id = section_id
        self._selected_node_index = None
        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"{curve.mnemonic} node {index + 1} deleted "
            f"({removed.x_px:.1f}, {removed.y_px:.1f}) px.",
            3500,
        )

    def toggle_cut_mode(self) -> None:
        """Arm or cancel one-shot section splitting for the selected section."""
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        if not self.editing or curve_id is None or section_id is None:
            return
        if self._join_mode:
            self._message_callback(
                "Cancel Join mode before cutting a section.",
                3500,
            )
            return

        self._cut_mode = not self._cut_mode
        self._selected_node_index = None
        self.refresh(selected_curve_id=curve_id)
        if self._cut_mode:
            self._message_callback(
                "Cut mode active. Click the selected curve between two control "
                "nodes to split the section. Click Cancel Cut to abort.",
                6000,
            )
        else:
            self._message_callback("Cut mode cancelled.", 3000)

    def _cut_selected_section_at(
        self,
        curve_id: str,
        section_id: str,
        x_px: float,
        y_px: float,
    ) -> None:
        """Split the selected section at one clicked point on its PCHIP path.

        The upper section keeps the original ``section_id``. The lower section
        receives a new ID. The cut control point is present in both sections so
        the split is geometrically lossless and can later be reversed by Join.
        Automatic raw samples are partitioned by Y without synthesizing or
        modifying any tracker sample.
        """
        if (
            not self._cut_mode
            or not self.editing
            or curve_id != self._editing_curve_id
            or section_id != self._selected_section_id
        ):
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return

            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            points = list(section.control_points_px)
            if len(points) < 2:
                return

            cut_point = TracePoint(float(x_px), float(y_px))
            y_values = [point.y_px for point in points]
            insertion_index = bisect_left(y_values, cut_point.y_px)
            if insertion_index <= 0 or insertion_index >= len(points):
                self._message_callback(
                    "Cut the section between existing control nodes.",
                    4500,
                )
                return

            previous_y = points[insertion_index - 1].y_px
            next_y = points[insertion_index].y_px
            gap = self.MIN_INSERT_VERTICAL_GAP_PX
            if (
                cut_point.y_px - previous_y < gap
                or next_y - cut_point.y_px < gap
            ):
                self._message_callback(
                    "Section cut rejected: choose a point farther from an "
                    "existing control node.",
                    4500,
                )
                return

            upper_points = tuple(points[:insertion_index] + [cut_point])
            lower_points = tuple([cut_point] + points[insertion_index:])

            if section.source is TraceSource.AUTOMATIC:
                # Preserve the tracker output exactly: only partition existing
                # raw samples; never create a synthetic raw cut sample.
                upper_raw = tuple(
                    point
                    for point in section.raw_samples_px
                    if point.y_px <= cut_point.y_px
                )
                lower_raw = tuple(
                    point
                    for point in section.raw_samples_px
                    if point.y_px >= cut_point.y_px
                )
                section.replace_control_points(upper_points)
                section.replace_raw_samples(upper_raw)
                lower = TraceSection.create_automatic(
                    control_points=lower_points,
                    raw_samples=lower_raw,
                    smoothing_strength=section.smoothing_strength,
                    simplification_tolerance_px=(
                        section.simplification_tolerance_px
                    ),
                    scale_id=section.scale_id,
                    scale_offset=section.scale_offset,
                )
            else:
                section.replace_control_points(upper_points)
                lower = TraceSection.create_manual(
                    lower_points,
                    scale_id=section.scale_id,
                    scale_offset=section.scale_offset,
                )

            digitized.add_section(lower)
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                "Cut section",
                curve_id,
                existing,
                digitized,
                before_selection=EditSelection(section_id, None),
                after_selection=EditSelection(section_id, None),
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot cut digitization section", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._cut_mode = False
        self._selected_section_id = section_id
        self._selected_node_index = None
        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"{curve.mnemonic} section split at "
            f"({cut_point.x_px:.1f}, {cut_point.y_px:.1f}) px. "
            "Two saved sections now share the cut point.",
            5000,
        )

    def toggle_join_mode(self) -> None:
        """Arm or cancel one-shot joining with the currently selected section."""
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        if not self.editing or curve_id is None or section_id is None:
            return
        if self._cut_mode:
            self._message_callback(
                "Cancel Cut mode before joining sections.",
                3500,
            )
            return

        digitized = self.service.get_digitized_curve(curve_id)
        if digitized is None or len(digitized.sections) < 2:
            self._message_callback(
                "At least two saved sections are required for Join.",
                4000,
            )
            self.refresh(selected_curve_id=curve_id)
            return

        self._join_mode = not self._join_mode
        self._selected_node_index = None
        self.refresh(selected_curve_id=curve_id)
        if self._join_mode:
            self._message_callback(
                "Join mode active. Click a neighboring saved section of this curve. "
                "Click Cancel Join to abort.",
                6000,
            )
        else:
            self._message_callback("Join mode cancelled.", 3000)

    @staticmethod
    def _merge_points_without_duplicate_y(
        first: tuple[TracePoint, ...],
        second: tuple[TracePoint, ...],
        *,
        label: str,
    ) -> tuple[TracePoint, ...]:
        """Merge two Y-sorted point sequences, deduplicating a shared endpoint.

        Equal Y is valid only when both points are the same geometric point. This
        keeps the canonical X=f(Y) representation unambiguous.
        """
        merged: list[TracePoint] = []
        for point in sorted((*first, *second), key=lambda item: item.y_px):
            if merged and point.y_px == merged[-1].y_px:
                if abs(point.x_px - merged[-1].x_px) <= 1e-9:
                    continue
                raise DigitizationError(
                    f"{label} contain different X values at the same y_px."
                )
            merged.append(point)
        return tuple(merged)

    def _join_selected_section_with(
        self,
        curve_id: str,
        second_section_id: str,
    ) -> None:
        """Join the selected section with one neighboring section of the curve.

        Sections must be adjacent in vertical ordering and may touch at one shared
        endpoint or have a gap. Overlapping depth ranges are rejected. Manual and
        automatic provenance is never mixed. Automatic joins additionally require
        identical processing settings so the resulting section has unambiguous
        smoothing/simplification metadata. The shallower section ID survives; this
        makes Cut -> Join restore the original ID created before the split.
        """
        first_section_id = self._selected_section_id
        if (
            not self._join_mode
            or not self.editing
            or curve_id != self._editing_curve_id
            or first_section_id is None
        ):
            return
        if second_section_id == first_section_id:
            self._message_callback(
                "Choose a different neighboring section to join.",
                3500,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return
            digitized = DigitizedCurve.from_dict(existing.to_dict())
            first = digitized.get_section(first_section_id)
            second = digitized.get_section(second_section_id)

            ordered_ids = [section.section_id for section in digitized.sections]
            first_index = ordered_ids.index(first_section_id)
            second_index = ordered_ids.index(second_section_id)
            if abs(first_index - second_index) != 1:
                self._message_callback(
                    "Join rejected: select a neighboring section of the same curve.",
                    4500,
                )
                return

            upper, lower = (
                (first, second)
                if first.start_y_px <= second.start_y_px
                else (second, first)
            )
            if upper.end_y_px is None or lower.start_y_px is None:
                return
            if upper.end_y_px > lower.start_y_px:
                self._message_callback(
                    "Join rejected: the two sections overlap in depth.",
                    4500,
                )
                return
            gap_px = max(0.0, lower.start_y_px - upper.end_y_px)
            if upper.source is not lower.source:
                self._message_callback(
                    "Join rejected: manual and automatic sections cannot be merged "
                    "because their provenance differs.",
                    5000,
                )
                return
            if upper.scale_id != lower.scale_id:
                self._message_callback(
                    "Join rejected: sections using different calibrated scales "
                    "must remain separate.",
                    5000,
                )
                return
            if upper.scale_offset != lower.scale_offset:
                self._message_callback(
                    "Join rejected: sections with different scale offsets must "
                    "remain separate so the wrap-around transition is preserved.",
                    5000,
                )
                return

            try:
                controls = self._merge_points_without_duplicate_y(
                    upper.control_points_px,
                    lower.control_points_px,
                    label="Control points",
                )
            except DigitizationError as exc:
                self._message_callback(
                    f"Join rejected: {exc}",
                    5000,
                )
                return

            if upper.source is TraceSource.AUTOMATIC:
                if (
                    upper.smoothing_strength != lower.smoothing_strength
                    or upper.simplification_tolerance_px
                    != lower.simplification_tolerance_px
                ):
                    self._message_callback(
                        "Join rejected: automatic sections use different smoothing "
                        "or simplification settings.",
                        5000,
                    )
                    return
                try:
                    raw = self._merge_points_without_duplicate_y(
                        upper.raw_samples_px,
                        lower.raw_samples_px,
                        label="Raw samples",
                    )
                except DigitizationError as exc:
                    self._message_callback(
                        f"Join rejected: {exc}",
                        5000,
                    )
                    return
                upper.replace_raw_samples(raw)

            upper.replace_control_points(controls)
            digitized.remove_section(lower.section_id)
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            self._record_applied_edit(
                "Join sections",
                curve_id,
                existing,
                digitized,
                before_selection=EditSelection(first_section_id, None),
                after_selection=EditSelection(upper.section_id, None),
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot join digitization sections", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._join_mode = False
        self._selected_section_id = upper.section_id
        self._selected_node_index = None
        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        gap_text = (
            "shared endpoint restored"
            if gap_px <= 1e-9
            else f"{gap_px:.1f} px gap bridged"
        )
        self._message_callback(
            f"{curve.mnemonic} sections joined ({gap_text}). "
            f"{len(digitized.sections)} saved section(s) remain.",
            5000,
        )

    def delete_selected_section(self) -> None:
        """Delete the complete selected trace section after confirmation.

        Section deletion is deliberately a separate, explicit operation from
        node deletion. Removing a whole section still asks for confirmation;
        the accepted deletion can now be restored through Undo.
        """
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        if not self.editing or curve_id is None or section_id is None:
            return
        if self._cut_mode:
            self._message_callback(
                "Cancel Cut mode before deleting the selected section.",
                4000,
            )
            return
        if self._join_mode:
            self._message_callback(
                "Cancel Join mode before deleting the selected section.",
                4000,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return
            section = existing.get_section(section_id)
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot delete digitization section", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        raw_count = len(section.raw_samples_px)
        extra = (
            f"\nPreserved automatic raw samples: {raw_count}."
            if raw_count
            else ""
        )
        response = QMessageBox.question(
            self.panel,
            "Delete trace section",
            (
                f"Delete the selected {curve.mnemonic} section?\n\n"
                f"Control nodes: {len(section.control_points_px)}.{extra}\n\n"
                "The complete section will be removed. You can restore it "
                "with Undo."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        try:
            digitized = DigitizedCurve.from_dict(existing.to_dict())
            digitized.remove_section(section_id)
            remaining = len(digitized.sections)
            if remaining:
                self.service.set_digitized_curve(digitized)
                after_curve = digitized
            else:
                self.service.remove_digitized_curve(curve_id)
                after_curve = None
            self._record_applied_edit(
                "Delete section",
                curve_id,
                existing,
                after_curve,
                before_selection=EditSelection(section_id, self._selected_node_index),
                after_selection=EditSelection(),
            )
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot delete digitization section", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._title_refresh_callback()
        if remaining:
            # Stay in edit mode so another saved section can be selected.
            self._selected_section_id = None
            self._selected_node_index = None
            self.refresh(selected_curve_id=curve_id)
            self._message_callback(
                f"{curve.mnemonic} section deleted. {remaining} saved "
                f"section(s) remain.",
                4000,
            )
            return

        # With no persisted geometry left for this curve there is nothing to
        # edit, so leave edit mode automatically and restore normal navigation.
        self.viewer.set_saved_trace_selection_enabled(False)
        self._clear_edit_selection()
        self.refresh(selected_curve_id=curve_id)
        self._message_callback(
            f"Last saved {curve.mnemonic} section deleted. Editing finished.",
            4500,
        )

    def _saved_display_changed(self, *_args) -> None:
        self.refresh(selected_curve_id=self.panel.selected_curve_id)

    def _scale_offset_changed(self, scale_offset: int) -> None:
        """Persist an offset edit only when one saved section is selected."""
        if not self.editing or self._selected_section_id is None:
            return
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        if curve_id is None:
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return
            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            if section.scale_offset == int(scale_offset):
                return
            section.set_scale_offset(scale_offset)
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            selection = EditSelection(section_id, self._selected_node_index)
            self._record_applied_edit(
                "Change scale offset",
                curve_id,
                existing,
                digitized,
                before_selection=selection,
                after_selection=selection,
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot change section scale offset", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        suffix = "normal scale" if not scale_offset else f"{scale_offset:+d} scale"
        self._message_callback(
            f"{curve.mnemonic} section changed to {suffix}.",
            3500,
        )

    def _scale_selection_changed(self, scale_id: str) -> None:
        """Persist a scale edit only when one saved section is selected."""
        if not self.editing or self._selected_section_id is None:
            return
        curve_id = self._editing_curve_id
        section_id = self._selected_section_id
        if curve_id is None:
            return
        try:
            if self.service.get_horizontal_calibration(
                curve_id,
                scale_id=scale_id,
            ) is None:
                raise DigitizationError(
                    "The selected horizontal scale is not completely calibrated."
                )
            existing = self.service.get_digitized_curve(curve_id)
            if existing is None:
                return
            digitized = DigitizedCurve.from_dict(existing.to_dict())
            section = digitized.get_section(section_id)
            if section.scale_id == scale_id:
                return
            section.set_scale_id(scale_id)
            digitized = DigitizedCurve.from_dict(digitized.to_dict())
            self.service.set_digitized_curve(digitized)
            selection = EditSelection(section_id, self._selected_node_index)
            self._record_applied_edit(
                "Change scale",
                curve_id,
                existing,
                digitized,
                before_selection=selection,
                after_selection=selection,
            )
            curve = self.service.get_curve(curve_id)
            profile = self.service.get_horizontal_scale_profile(
                curve_id,
                scale_id,
            )
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot change section scale", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        self._title_refresh_callback()
        self.refresh(selected_curve_id=curve_id)
        label = profile.display_name(curve) if profile is not None else scale_id
        self._message_callback(
            f"{curve.mnemonic} section changed to {label}.",
            3500,
        )

    def _curve_selected(self, curve_id: str) -> None:
        if not self.tracing and not self.editing:
            self.refresh(selected_curve_id=curve_id)

    def _points_changed(self, points) -> None:
        if self._active_curve_id is not None:
            self.panel.set_manual_draft_state(True, len(points))

    def _point_rejected(self, message: str) -> None:
        self._message_callback(f"Point rejected: {message}", 5000)

    def _clear_edit_selection(self) -> None:
        self._cut_mode = False
        self._join_mode = False
        self._editing_curve_id = None
        self._selected_section_id = None
        self._selected_node_index = None
        self.panel.set_edit_state(False)

    def _image_ready(self) -> bool:
        segment = self.service.active_segment
        if segment is None or not self.viewer.has_image:
            return False
        path = self.service.resolve_segment_preprocessed(segment)
        return path is not None and path.is_file()
