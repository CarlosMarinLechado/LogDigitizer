"""GUI coordinator for guided automatic tracking through step 8.4D.7B."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QMouseEvent

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.digitization import (
    DigitizationError,
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    TrackerStopReason,
)
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
    AutomaticTrackingSessionError,
    AutomaticTrackingSessionState,
)

from .automatic_digitization_overlay import AutomaticDigitizationOverlay
from .automatic_digitization_panel import AutomaticDigitizationPanel
from .viewer import ImageViewer


class AutomaticDigitizationController(QObject):
    """Own one transient raw automatic trace and intercept canvas clicks while active."""

    def __init__(
        self,
        service: ProjectService,
        viewer: ImageViewer,
        panel: AutomaticDigitizationPanel,
        *,
        prepare_capture_callback: Callable[[], None],
        title_refresh_callback: Callable[[], None],
        digitization_refresh_callback: Callable[[str], None],
        record_edit_callback: Callable[[str, str, DigitizedCurve | None, DigitizedCurve | None], None],
        message_callback: Callable[[str, int], None],
        error_callback: Callable[[str, Exception], None],
        settings: AutomaticTraceSettings | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.viewer = viewer
        self.panel = panel
        self._prepare_capture_callback = prepare_capture_callback
        self._title_refresh_callback = title_refresh_callback
        self._digitization_refresh_callback = digitization_refresh_callback
        self._record_edit_callback = record_edit_callback
        self._message_callback = message_callback
        self._error_callback = error_callback
        self._settings_override = settings
        self._current_settings = settings or panel.automatic_trace_settings

        self._curve_id: str | None = None
        self._segment_id: str | None = None
        self._scale_id: str | None = None
        self._scale_offset = 0
        self._session: AutomaticTrackingSession | None = None
        self._capture_clicks = False

        self.overlay = AutomaticDigitizationOverlay(
            viewer.overlays,
            viewer.image_to_scene,
        )

        # Keep the viewport wrapper captured once. During QMainWindow teardown
        # Qt can destroy ImageViewer before pending viewport events are drained;
        # calling self.viewer.viewport() from eventFilter at that point raises
        # "Internal C++ object already deleted".
        self._viewport = viewer.viewport()

        panel.automaticStartRequested.connect(self.start)
        panel.automaticFinishRequested.connect(self.finish)
        panel.automaticCancelRequested.connect(self.cancel)
        panel.curveSelectionChanged.connect(self._curve_selected)
        viewer.imageChanged.connect(self._image_changed)
        viewer.automaticDigitizationFinishRequested.connect(self.finish)
        self._viewport.installEventFilter(self)

    @property
    def active(self) -> bool:
        return self._session is not None

    @property
    def active_curve_id(self) -> str | None:
        """Curve currently owning the transient automatic draft, if any."""
        return self._curve_id if self.active else None

    @property
    def active_scale_offset(self) -> int | None:
        """Scale offset captured when the automatic draft started."""
        return self._scale_offset if self.active else None

    @property
    def active_scale_id(self) -> str | None:
        """Horizontal scale captured when the automatic draft started."""
        return self._scale_id if self.active else None

    @property
    def current_settings(self) -> AutomaticTraceSettings:
        """Settings captured when the current/most recent automatic run started."""
        return self._current_settings

    @property
    def complete(self) -> bool:
        return bool(
            self._session is not None
            and self._session.state is AutomaticTrackingSessionState.COMPLETE
        )

    @property
    def waiting_for_resume(self) -> bool:
        return bool(
            self._session is not None
            and self._session.state
            is AutomaticTrackingSessionState.WAITING_FOR_RESUME
        )

    @property
    def raw_samples_px(self) -> tuple[TracePoint, ...]:
        if self._session is None:
            return ()
        return self._session.raw_samples_px

    @property
    def last_splice_point_px(self) -> TracePoint | None:
        if self._session is None:
            return None
        return self._session.last_splice_point_px

    @property
    def last_correction_point_px(self) -> TracePoint | None:
        if self._session is None:
            return None
        return self._session.last_correction_point_px

    def refresh_persisted_raw_overlay(self, *_args) -> None:
        """Clear the draft-only overlay when no automatic session is active.

        Saved raw automatic sections now belong to the common saved-curves
        overlay, where every project curve can be shown and inactive curves are
        dimmed consistently. Keeping this compatibility callback avoids double
        rendering and still lets undo/redo clear a stale automatic draft layer.
        """
        if self.active:
            return

        try:
            self.overlay.clear()
        except RuntimeError:
            # Safe no-op while Qt is destroying its scene items.
            return

    def _redraw_current_overlay(
        self,
        *,
        lost_y_px: float | None = None,
    ) -> None:
        curve_id = self._curve_id
        session = self._session
        if curve_id is None:
            self.overlay.clear()
            return

        try:
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError, ValueError):
            curve = None

        current = () if session is None else session.raw_samples_px
        groups = (current,) if current else ()

        if curve is not None:
            self.overlay.set_display_color(curve.display_color)
        self.overlay.set_sample_groups(
            groups,
            current_samples=current,
            lost_y_px=lost_y_px,
        )

    def _curve_selected(self, curve_id: str) -> None:
        if not self.active:
            self.refresh_persisted_raw_overlay()

    def start(self) -> None:
        """Arm a new automatic trace; the next canvas click is its start point."""
        if self.active:
            return
        if bool(getattr(self.panel, "automatic_erase_active", False)):
            self._message_callback(
                "Turn off the automatic eraser before starting a new trace.",
                4500,
            )
            return
        if self.panel.tracing or self.panel.editing:
            self._message_callback(
                "Finish the current manual digitization operation first.",
                4500,
            )
            return

        curve_id = self.panel.selected_curve_id
        segment = self.service.active_segment
        if curve_id is None or segment is None or not self.viewer.has_image:
            self._message_callback(
                "Select a calibrated curve on a rectified segment first.",
                5000,
            )
            return

        try:
            reason = self.service.curve_digitization_block_reason(
                curve_id,
                segment,
            )
            curve = self.service.get_curve(curve_id)
            if reason is not None:
                self._message_callback(f"Cannot track curve: {reason}", 5000)
                return
            scale_id = self.panel.selected_scale_id
            if self.service.get_horizontal_calibration(
                curve_id,
                segment,
                scale_id=scale_id,
            ) is None:
                self._message_callback(
                    "Select a complete horizontal scale before tracking.",
                    5000,
                )
                return
            image = self.viewer.displayed_image_array()
        except (KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot start automatic digitization", exc)
            return

        self._prepare_capture_callback()
        # Public viewer setters keep the existing manual/saved modes out of the
        # way without editing ImageViewer's already-stable mousePressEvent.
        self.viewer.set_manual_digitization_enabled(False)
        self.viewer.set_saved_trace_selection_enabled(False)

        self._curve_id = curve_id
        self._segment_id = segment.segment_id
        self._scale_id = scale_id
        self._scale_offset = self.panel.scale_offset

        # Capture one immutable settings snapshot for Start/Resume/Correction
        # lifecycle. Sliders are locked while a draft is active, so the same
        # scientific parameters govern every resume click in the section.
        self._current_settings = (
            self._settings_override
            if self._settings_override is not None
            else self.panel.automatic_trace_settings
        )
        self._session = AutomaticTrackingSession(
            image,
            settings=self._current_settings,
        )
        self.panel.set_detected_line_width(None)
        self.overlay.set_display_color(curve.display_color)
        self._redraw_current_overlay()
        self._capture_clicks = True
        self.viewer.set_automatic_digitization_enabled(True)
        self.panel.set_automatic_state(
            True,
            can_finish=False,
            status_text=(
                f"Automatic tracking — {curve.mnemonic}: click the curve "
                "to choose the starting point."
                + (
                    f" Scale offset: {self._scale_offset:+d}."
                    if self._scale_offset
                    else ""
                )
            ),
        )
        self._message_callback(
            "Automatic tracking armed. Click the curve at the desired start point.",
            5000,
        )

    def finish(self) -> None:
        """Accept and persist the current raw automatic trace as one section."""
        session = self._session
        curve_id = self._curve_id
        if session is None or curve_id is None:
            return

        samples = session.raw_samples_px
        if len(samples) < 2:
            self._message_callback(
                "Automatic trace needs at least two trajectory samples "
                "before it can be saved.",
                5000,
            )
            return

        try:
            existing = self.service.get_digitized_curve(curve_id)
            digitized = (
                DigitizedCurve.from_dict(existing.to_dict())
                if existing is not None
                else DigitizedCurve(curve_id=curve_id)
            )
            section = TraceSection.create_automatic(
                raw_samples=samples,
                control_points=(),
                scale_id=self._scale_id or self.panel.selected_scale_id,
                scale_offset=self._scale_offset,
            )
            automatic_sections_affected = (
                digitized.apply_automatic_override(section)
            )
            self.service.set_digitized_curve(digitized)
            self._record_edit_callback(
                (
                    "Replace automatic geometry"
                    if automatic_sections_affected
                    else "Add automatic raw section"
                ),
                curve_id,
                existing,
                digitized,
            )
            curve = self.service.get_curve(curve_id)
        except (
            DigitizationError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            self._error_callback("Cannot finish automatic digitization", exc)
            return

        self._capture_clicks = False
        self._session = None
        saved_scale_offset = self._scale_offset
        self._scale_id = None
        self._scale_offset = 0
        self.viewer.set_automatic_digitization_enabled(False)
        self.panel.set_automatic_state(
            False,
            status_text=(
                f"Automatic raw trace saved for {curve.mnemonic}: "
                f"{len(section.raw_samples_px)} samples."
                + (
                    f" Offset {saved_scale_offset:+d} scale."
                    if saved_scale_offset
                    else ""
                )
            ),
        )
        self._title_refresh_callback()
        self._digitization_refresh_callback(curve_id)
        self.refresh_persisted_raw_overlay()
        self._message_callback(
            f"Automatic raw section saved for {curve.mnemonic} "
            f"({len(section.raw_samples_px)} samples)."
            + (
                " Previous automatic geometry in the same Y range was "
                "replaced; manual corrections were preserved."
                if automatic_sections_affected
                else ""
            ),
            5000,
        )

    def cancel(self) -> None:
        """Discard the transient raw trace without changing persistent project data."""
        if not self.active:
            return
        curve_id = self._curve_id or self.panel.selected_curve_id
        self._session = None
        self._curve_id = None
        self._segment_id = None
        self._scale_id = None
        self._scale_offset = 0
        self._capture_clicks = False
        self.viewer.set_automatic_digitization_enabled(False)
        self.panel.set_automatic_state(False)
        if curve_id is not None:
            self._digitization_refresh_callback(curve_id)
        self.refresh_persisted_raw_overlay()
        self._message_callback("Automatic raw draft cleared.", 3500)

    def abort_for_context_change(self) -> None:
        """Drop automatic draft/fallback geometry when its image context changes."""
        self._session = None
        self._curve_id = None
        self._segment_id = None
        self._scale_id = None
        self._scale_offset = 0
        self._capture_clicks = False
        self.overlay.clear()
        try:
            self.viewer.set_automatic_digitization_enabled(False)
        except RuntimeError:
            pass
        self.panel.set_automatic_state(False)
        self.panel.set_detected_line_width(None)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        # Fast exit is important during widget teardown. Once capture has
        # finished/cancelled, the controller must not dereference Qt children.
        if not self._capture_clicks:
            return False

        if (
            watched is self._viewport
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._handle_canvas_click(mouse_event)
                return True

        # Do not call QObject.eventFilter() here: during application teardown
        # the C++ side of this QObject may already be in destruction while Qt
        # is still draining pending events. False simply means "not handled".
        return False

    def _handle_canvas_click(self, event: QMouseEvent) -> None:
        session = self._session
        if session is None:
            return

        if (
            self.service.active_segment_id != self._segment_id
            or self.panel.selected_curve_id != self._curve_id
        ):
            self.abort_for_context_change()
            self._message_callback(
                "Automatic trace was cleared because the segment or curve changed.",
                4500,
            )
            return

        image_point = self.viewer.viewport_to_image(event.position())
        if image_point is None:
            return
        point = TracePoint(float(image_point.x()), float(image_point.y()))

        reconnection_applied = False
        try:
            if session.state is AutomaticTrackingSessionState.WAITING_FOR_START:
                result = session.start(point)
            elif session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME:
                last_y = session.raw_samples_px[-1].y_px
                if point.y_px <= last_y:
                    result = session.correct(point)
                else:
                    result = session.resume(point)
                reconnection_applied = True
            elif session.state is AutomaticTrackingSessionState.COMPLETE:
                result = session.correct(point)
                reconnection_applied = True
            else:
                return
        except (
            AutomaticTraceError,
            AutomaticTrackingSessionError,
            ValueError,
        ) as exc:
            self._message_callback(str(exc), 4500)
            return
        except Exception as exc:  # GUI safety boundary
            self._error_callback("Automatic tracking failed", exc)
            return

        self.panel.set_detected_line_width(
            session.estimated_line_width_px
        )

        if result.stop_reason is TrackerStopReason.LOST_CURVE:
            self._redraw_current_overlay(lost_y_px=result.stop_y_px)
            reconnection_prefix = (
                "Trace reconnected. " if reconnection_applied else ""
            )
            self.panel.set_automatic_state(
                True,
                can_finish=len(session.raw_samples_px) >= 2,
                status_text=(
                    reconnection_prefix
                    + "Tracking stopped: curve confidence was lost. "
                    "Click the correct curve at or below the loss location to resume."
                ),
            )
            self._message_callback(
                "Curve lost. Click the correct trace lower down to resume.",
                6000,
            )
            return

        self._redraw_current_overlay()
        self.panel.set_automatic_state(
            True,
            complete=True,
            can_finish=len(session.raw_samples_px) >= 2,
            status_text=(
                f"Automatic raw trace complete: {len(session.raw_samples_px)} "
                "trajectory samples. Click an earlier point on the correct "
                "curve to replace a deviated tail, or press Finish Automatic "
                "Trace."
            ),
        )
        if reconnection_applied:
            self._message_callback(
                "Automatic trace reconnected through the selected point. "
                "Click again to make another correction, or press Finish "
                "Automatic Trace.",
                5500,
            )
        else:
            self._message_callback(
                "Automatic tracker reached the image bottom. Click an earlier "
                "point to correct a deviation, or press Finish Automatic Trace.",
                5500,
            )

    def _image_changed(self, _path) -> None:
        # If the image changes after a run has been armed, its raster snapshot
        # no longer matches the canvas and the draft must be discarded.
        if self.active:
            self.abort_for_context_change()
            return
        self.refresh_persisted_raw_overlay()
