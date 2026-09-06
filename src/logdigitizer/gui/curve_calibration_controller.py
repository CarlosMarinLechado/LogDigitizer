"""GUI controller for project curves and per-segment horizontal calibration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QWidget

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import (
    CalibrationError,
    CurveDefinition,
    DEFAULT_HORIZONTAL_SCALE_ID,
)
from logdigitizer.domain.states import CurveCalibrationReadiness
from logdigitizer.domain.curve_display import next_available_curve_display_color
from logdigitizer.processing.calibration import pixel_x_to_value

from .calibration_panel import (
    CalibrationPanel,
    CurveDefinitionDialog,
    HorizontalValueDialog,
)
from .viewer import ImageViewer


@dataclass(frozen=True, slots=True)
class CurveCursorReadout:
    """Scientific X readout used consistently by the status bar and viewer HUD."""

    curve: CurveDefinition | None
    value: float | None
    status: str
    scale_id: str
    scale_offset: int


class CurveCalibrationController(QObject):
    """Coordinate global curve definitions with active-segment X calibration."""

    def __init__(
        self,
        service: ProjectService,
        viewer: ImageViewer,
        panel: CalibrationPanel,
        value_status: QLabel,
        *,
        error_callback: Callable[[str, Exception], None],
        title_refresh_callback: Callable[[], None],
        message_callback: Callable[[str, int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.viewer = viewer
        self.panel = panel
        self.value_status = value_status
        self._error_callback = error_callback
        self._title_refresh_callback = title_refresh_callback
        self._message_callback = message_callback
        self._active_curve_id: str | None = None
        self._active_scale_id = DEFAULT_HORIZONTAL_SCALE_ID
        self._active_calibration = None

        panel.curveAddRequested.connect(self._add_curve)
        panel.curveEditRequested.connect(self._edit_curve)
        panel.curveRemoveRequested.connect(self._remove_curve)
        panel.curveSelectionChanged.connect(self._curve_selected)
        panel.curvePresenceChanged.connect(self._curve_presence_changed)
        panel.horizontalClearRequested.connect(self._clear_points)
        panel.horizontalDeleteRequested.connect(self._delete_point)
        panel.horizontalEditRequested.connect(self._edit_point)
        panel.horizontalScaleAddRequested.connect(self._add_scale)
        panel.horizontalScaleRemoveRequested.connect(self._remove_scale)
        panel.horizontalScaleSelectionChanged.connect(self._scale_selected)

        viewer.horizontalPointRequested.connect(self._add_point_requested)
        viewer.horizontalCalibrationPointMoved.connect(self._point_moved)
        viewer.horizontalCalibrationEditRequested.connect(
            self._edit_selected_handle
        )

    @property
    def active_curve_id(self) -> str | None:
        return self._active_curve_id

    def can_capture(self) -> bool:
        curve_id = self.panel.selected_curve_id
        if curve_id is None or not self._image_ready():
            return False
        try:
            if not self.service.is_curve_present(curve_id):
                return False
        except (KeyError, RuntimeError):
            return False
        return len(
            self.service.get_horizontal_calibration_points(
                curve_id,
                scale_id=self.panel.selected_scale_id,
            )
        ) < 2

    def capture_unavailable_message(self) -> str:
        if not self._image_ready():
            return "Apply rectification before adding X calibration points."
        curve_id = self.panel.selected_curve_id
        if curve_id is None:
            return "Define and select a curve before adding X calibration points."
        try:
            if not self.service.is_curve_present(curve_id):
                curve = self.service.get_curve(curve_id)
                return (
                    f"{curve.mnemonic} is marked as not present in this image."
                )
        except (KeyError, RuntimeError):
            pass
        return "This curve already has two X calibration points."

    def refresh(
        self,
        *,
        preferred_y_by_value: dict[float, float] | None = None,
        selected_curve_id: str | None = None,
        selected_scale_id: str | None = None,
    ) -> None:
        project = self.service.project
        curves = self.service.get_curves()
        self.panel.set_project_available(project is not None)

        wanted = (
            selected_curve_id
            or self._active_curve_id
            or self.panel.selected_curve_id
        )
        valid_ids = {curve.curve_id for curve in curves}
        if wanted not in valid_ids:
            wanted = curves[0].curve_id if curves else None

        self.panel.set_curves(curves, wanted)
        self._active_curve_id = self.panel.selected_curve_id

        curve = self._selected_curve()
        segment = self.service.active_segment
        image_ready = self._image_ready()

        depth_points = (
            self.service.get_depth_calibration_points(segment)
            if segment is not None
            else ()
        )
        depth_valid = (
            self.service.get_depth_calibration(segment) is not None
            if segment is not None
            else False
        )

        if curve is None or segment is None:
            self.panel.set_curve_presence(
                True,
                segment_available=segment is not None,
            )
            self.panel.set_horizontal_scales((), curve)
            self.viewer.clear_horizontal_calibration_points()
            self._active_scale_id = DEFAULT_HORIZONTAL_SCALE_ID
            self._active_calibration = None
            self.panel.set_calibration_check(
                depth_valid=depth_valid,
                curve=curve,
                readiness=None,
                depth_point_count=len(depth_points),
                x_point_count=0,
            )
            self._set_idle_status(curve, present=True)
            return

        present = self.service.is_curve_present(curve.curve_id, segment)
        self.panel.set_curve_presence(
            present,
            segment_available=True,
        )
        profiles = self.service.get_horizontal_scale_profiles(
            curve.curve_id,
            segment,
        )
        wanted_scale = (
            selected_scale_id
            or (
                self._active_scale_id
                if curve.curve_id == self._active_curve_id
                else None
            )
            or self.panel.selected_scale_id
            or DEFAULT_HORIZONTAL_SCALE_ID
        )
        self.panel.set_horizontal_scales(
            profiles,
            curve,
            selected_scale_id=wanted_scale,
            curve_present=present,
        )
        self._active_scale_id = self.panel.selected_scale_id
        points = self.service.get_horizontal_calibration_points(
            curve.curve_id,
            segment,
            scale_id=self._active_scale_id,
        )

        if present and image_ready and self.viewer.has_image:
            # Use the selected curve's display color only for the short vertical
            # X-calibration marks. Labels and selected/drag states remain unchanged.
            self.viewer.horizontal_calibration.set_mark_color(
                curve.display_color
            )
            self.viewer.set_horizontal_calibration_points(
                points,
                curve.unit,
                preferred_y_by_value=preferred_y_by_value,
            )
        else:
            self.viewer.clear_horizontal_calibration_points()

        self._active_calibration = (
            self.service.get_horizontal_calibration(
                curve.curve_id,
                segment,
                scale_id=self._active_scale_id,
            )
            if present
            else None
        )
        readiness = self.service.get_curve_calibration_readiness(
            curve.curve_id,
            segment,
        )
        self.panel.set_calibration_check(
            depth_valid=depth_valid,
            curve=curve,
            readiness=readiness,
            depth_point_count=len(depth_points),
            x_point_count=len(points),
        )
        self._set_idle_status(curve, present=present)

    def cursor_readout(
        self,
        x: float,
        *,
        scale_id: str | None = None,
        scale_offset: int = 0,
        curve_id: str | None = None,
    ) -> CurveCursorReadout:
        """Return one calibrated X value without changing presentation state.

        ``curve_id`` is optional so legacy/status-bar callers keep using the
        calibration-panel selection, while the viewer HUD can explicitly read
        the curve selected in the Digitization workspace.
        """
        if curve_id is None:
            curve = self._selected_curve()
        else:
            try:
                curve = self.service.get_curve(curve_id)
            except (KeyError, RuntimeError):
                curve = None
        requested_scale_id = scale_id or self._active_scale_id
        if curve is None:
            return CurveCursorReadout(
                curve=None,
                value=None,
                status="no_curve",
                scale_id=requested_scale_id,
                scale_offset=int(scale_offset),
            )

        try:
            present = self.service.is_curve_present(curve.curve_id)
        except (KeyError, RuntimeError):
            present = False
        if not present:
            return CurveCursorReadout(
                curve=curve,
                value=None,
                status="not_present",
                scale_id=requested_scale_id,
                scale_offset=int(scale_offset),
            )

        calibration = self.service.get_horizontal_calibration(
            curve.curve_id,
            scale_id=requested_scale_id,
        )
        if calibration is None:
            return CurveCursorReadout(
                curve=curve,
                value=None,
                status="not_calibrated",
                scale_id=requested_scale_id,
                scale_offset=int(scale_offset),
            )

        try:
            value = pixel_x_to_value(
                x,
                calibration,
                scale_offset=scale_offset,
            )
        except CalibrationError:
            return CurveCursorReadout(
                curve=curve,
                value=None,
                status="offset_unavailable",
                scale_id=requested_scale_id,
                scale_offset=int(scale_offset),
            )

        return CurveCursorReadout(
            curve=curve,
            value=float(value),
            status="ok",
            scale_id=requested_scale_id,
            scale_offset=int(scale_offset),
        )

    def update_cursor(
        self,
        x: float,
        valid: bool,
        *,
        scale_id: str | None = None,
        scale_offset: int = 0,
    ) -> None:
        """Show the calibrated cursor value using its contextual scale."""
        if not valid:
            self.value_status.setText("Curve: —")
            return

        readout = self.cursor_readout(
            x,
            scale_id=scale_id,
            scale_offset=scale_offset,
        )
        curve = readout.curve
        if curve is None:
            self.value_status.setText("Curve: No curve selected")
            return
        if readout.status == "not_present":
            self.value_status.setText(
                f"{curve.mnemonic}: Not present in image"
            )
            return
        if readout.status == "not_calibrated":
            self.value_status.setText(
                f"{curve.mnemonic}: Not calibrated"
            )
            return
        if readout.status != "ok" or readout.value is None:
            self.value_status.setText(
                f"{curve.mnemonic}: Scale offset unavailable"
            )
            return

        unit = f" {curve.unit}" if curve.unit else ""
        offset_label = (
            f" · {readout.scale_offset:+d} scale"
            if readout.scale_offset
            else ""
        )
        profiles = self.service.get_horizontal_scale_profiles(curve.curve_id)
        profile = self.service.get_horizontal_scale_profile(
            curve.curve_id,
            readout.scale_id,
        )
        scale_label = ""
        if profile is not None and (
            len(profiles) > 1
            or profile.scale_id != DEFAULT_HORIZONTAL_SCALE_ID
        ):
            full_name = profile.display_name(curve)
            prefix = f"{curve.mnemonic}: "
            compact = (
                full_name[len(prefix):]
                if full_name.startswith(prefix)
                else full_name
            )
            scale_label = f" · {compact}"
        self.value_status.setText(
            f"{curve.mnemonic}{scale_label}{offset_label}: "
            f"{readout.value:.6g}{unit}"
        )

    def _selected_curve(self) -> CurveDefinition | None:
        curve_id = self.panel.selected_curve_id or self._active_curve_id
        if curve_id is None:
            return None
        try:
            return self.service.get_curve(curve_id)
        except (KeyError, RuntimeError):
            return None

    def _image_ready(self) -> bool:
        segment = self.service.active_segment
        if segment is None:
            return False
        path = self.service.resolve_segment_preprocessed(segment)
        return path is not None and path.is_file()

    def _curve_selected(self, curve_id: str) -> None:
        self._active_curve_id = curve_id
        self._active_scale_id = DEFAULT_HORIZONTAL_SCALE_ID
        self.refresh(selected_curve_id=curve_id)

    def _scale_selected(self, curve_id: str, scale_id: str) -> None:
        self._active_curve_id = curve_id
        self._active_scale_id = scale_id
        self.refresh(
            selected_curve_id=curve_id,
            selected_scale_id=scale_id,
        )

    def _add_scale(self, curve_id: str) -> None:
        try:
            profile = self.service.add_horizontal_scale(curve_id)
        except (CalibrationError, KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot add scale", exc)
            return
        self._active_scale_id = profile.scale_id
        self.refresh(
            selected_curve_id=curve_id,
            selected_scale_id=profile.scale_id,
        )
        self.panel.set_horizontal_capture_mode(True)
        self._title_refresh_callback()
        self._message_callback(
            "New scale added — capture its two X calibration points.",
            4500,
        )

    def _remove_scale(self, curve_id: str, scale_id: str) -> None:
        try:
            curve = self.service.get_curve(curve_id)
            profile = self.service.get_horizontal_scale_profile(
                curve_id,
                scale_id,
            )
        except (KeyError, RuntimeError):
            return
        if profile is None:
            return
        response = QMessageBox.question(
            self.parent(),
            "Remove scale",
            f"Remove '{profile.display_name(curve)}' from this segment?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.remove_horizontal_scale(curve_id, scale_id)
        except (CalibrationError, KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot remove scale", exc)
            return
        self._active_scale_id = DEFAULT_HORIZONTAL_SCALE_ID
        self.refresh(
            selected_curve_id=curve_id,
            selected_scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
        )
        self._title_refresh_callback()
        self._message_callback("Scale removed", 3500)

    def _curve_presence_changed(self, curve_id: str, present: bool) -> None:
        try:
            changed = self.service.set_curve_present(curve_id, present)
        except (CalibrationError, KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot change curve presence", exc)
            self.refresh(selected_curve_id=curve_id)
            return

        if not present:
            self.panel.set_horizontal_capture_mode(False)
            self.viewer.set_horizontal_calibration_enabled(False)

        self.refresh(selected_curve_id=curve_id)
        if changed:
            self._title_refresh_callback()
            curve = self.service.get_curve(curve_id)
            self._message_callback(
                (
                    f"{curve.mnemonic} marked as present in this image"
                    if present
                    else f"{curve.mnemonic} marked as not present in this image"
                ),
                4000,
            )

    def _add_curve(self) -> None:
        suggested_color = next_available_curve_display_color(
            curve.display_color
            for curve in self.service.get_curves()
        )
        dialog = CurveDefinitionDialog(
            suggested_display_color=suggested_color,
            parent=self.parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            curve = self.service.add_curve(**dialog.values)
        except (CalibrationError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot add curve", exc)
            return
        self._active_curve_id = curve.curve_id
        self.refresh(selected_curve_id=curve.curve_id)
        self._title_refresh_callback()
        self._message_callback(
            f"Curve added: {curve.mnemonic} — {curve.name}",
            4000,
        )

    def _edit_curve(self, curve_id: str) -> None:
        try:
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError) as exc:
            self._error_callback("Cannot edit curve", exc)
            return
        dialog = CurveDefinitionDialog(
            initial_curve=curve,
            parent=self.parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.service.update_curve(
                curve_id,
                **dialog.values,
            )
        except (CalibrationError, KeyError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot edit curve", exc)
            return
        self.refresh(selected_curve_id=updated.curve_id)
        self._title_refresh_callback()
        self._message_callback(
            f"Curve updated: {updated.mnemonic}",
            3500,
        )

    def _remove_curve(self, curve_id: str) -> None:
        try:
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError):
            return

        response = QMessageBox.question(
            self.parent(),
            "Remove curve",
            (
                f"Remove '{curve.mnemonic} — {curve.name}' from the project?\n\n"
                "Its X calibration points will also be removed from every segment."
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
            self.service.remove_curve(curve_id)
        except (KeyError, RuntimeError) as exc:
            self._error_callback("Cannot remove curve", exc)
            return
        self._active_curve_id = None
        self.refresh()
        self._title_refresh_callback()
        self._message_callback("Curve removed", 3500)

    def _add_point_requested(self, x: float, y: float) -> None:
        curve = self._selected_curve()
        if curve is None or not self._image_ready():
            return
        scale_id = self.panel.selected_scale_id
        if len(
            self.service.get_horizontal_calibration_points(
                curve.curve_id,
                scale_id=scale_id,
            )
        ) >= 2:
            self._message_callback(
                "This curve already has two X calibration points.",
                4500,
            )
            self.panel.set_horizontal_capture_mode(False)
            return

        dialog = HorizontalValueDialog(
            curve,
            title="Add X calibration point",
            parent=self.parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        value = dialog.value
        try:
            self.service.add_horizontal_calibration_point(
                curve.curve_id,
                pixel_x=x,
                value=value,
                scale_id=scale_id,
            )
        except (CalibrationError, ValueError) as exc:
            self._error_callback("Cannot add X point", exc)
            return

        self.refresh(
            preferred_y_by_value={value: y},
            selected_curve_id=curve.curve_id,
            selected_scale_id=scale_id,
        )
        self._title_refresh_callback()
        if len(
            self.service.get_horizontal_calibration_points(
                curve.curve_id,
                scale_id=scale_id,
            )
        ) >= 2:
            self.panel.set_horizontal_capture_mode(False)
        self._message_callback(
            f"X reference added for {curve.mnemonic}: {value:g}"
            + (f" {curve.unit}" if curve.unit else ""),
            3500,
        )

    def _point_moved(
        self,
        index: int,
        x: float,
        y: float,
    ) -> None:
        del y  # Vertical placement is presentation-only and never persisted.
        curve = self._selected_curve()
        if curve is None:
            return
        scale_id = self.panel.selected_scale_id
        points = self.service.get_horizontal_calibration_points(
            curve.curve_id,
            scale_id=scale_id,
        )
        if not 0 <= index < len(points):
            self.refresh(selected_curve_id=curve.curve_id)
            return
        if abs(points[index].pixel_x - x) <= 1e-6:
            # Pure vertical drag must not dirty the project.
            return

        try:
            self.service.update_horizontal_calibration_point(
                curve.curve_id,
                index,
                pixel_x=x,
                scale_id=scale_id,
            )
        except (CalibrationError, IndexError, ValueError) as exc:
            self.refresh(
                selected_curve_id=curve.curve_id,
                selected_scale_id=scale_id,
            )
            self._message_callback(
                f"X point move rejected: {exc}",
                6000,
            )
            return
        self.refresh(
            selected_curve_id=curve.curve_id,
            selected_scale_id=scale_id,
        )
        self._title_refresh_callback()

    def _edit_selected_handle(self, index: int) -> None:
        curve = self._selected_curve()
        if curve is not None:
            self._edit_point(curve.curve_id, index)

    def _edit_point(self, curve_id: str, index: int) -> None:
        try:
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError):
            return
        scale_id = self.panel.selected_scale_id
        points = self.service.get_horizontal_calibration_points(
            curve_id,
            scale_id=scale_id,
        )
        if not 0 <= index < len(points):
            return
        point = points[index]
        y_hint = self.viewer.horizontal_calibration.point_image_y(index)

        dialog = HorizontalValueDialog(
            curve,
            initial_value=point.value,
            title="Edit X calibration point",
            parent=self.parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_value = dialog.value
        try:
            self.service.update_horizontal_calibration_point(
                curve_id,
                index,
                value=new_value,
                scale_id=scale_id,
            )
        except (CalibrationError, IndexError, ValueError) as exc:
            self._error_callback("Cannot edit X point", exc)
            return

        preferred = (
            {new_value: y_hint}
            if y_hint is not None
            else None
        )
        self.refresh(
            preferred_y_by_value=preferred,
            selected_curve_id=curve_id,
            selected_scale_id=scale_id,
        )
        self._title_refresh_callback()

    def _delete_point(self, curve_id: str, index: int) -> None:
        scale_id = self.panel.selected_scale_id
        try:
            self.service.remove_horizontal_calibration_point(
                curve_id,
                index,
                scale_id=scale_id,
            )
        except (CalibrationError, IndexError, KeyError, ValueError) as exc:
            self._error_callback("Cannot delete X point", exc)
            return
        self.refresh(
            selected_curve_id=curve_id,
            selected_scale_id=scale_id,
        )
        self._title_refresh_callback()

    def _clear_points(self, curve_id: str) -> None:
        try:
            curve = self.service.get_curve(curve_id)
        except (KeyError, RuntimeError):
            return
        scale_id = self.panel.selected_scale_id
        if not self.service.get_horizontal_calibration_points(
            curve_id,
            scale_id=scale_id,
        ):
            return
        response = QMessageBox.question(
            self.parent(),
            "Clear X calibration",
            (
                "Remove both X calibration points for the selected "
                f"{curve.mnemonic} scale?"
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
            self.service.clear_horizontal_calibration_points(
                curve_id,
                scale_id=scale_id,
            )
        except (CalibrationError, RuntimeError, ValueError) as exc:
            self._error_callback("Cannot clear X points", exc)
            return
        self.refresh(
            selected_curve_id=curve_id,
            selected_scale_id=scale_id,
        )
        self._title_refresh_callback()

    def _set_idle_status(
        self,
        curve: CurveDefinition | None,
        *,
        present: bool,
    ) -> None:
        if curve is None:
            self.value_status.setText("Curve: No curve selected")
        elif not present:
            self.value_status.setText(
                f"{curve.mnemonic}: Not present in image"
            )
        elif self._active_calibration is None:
            self.value_status.setText(f"{curve.mnemonic}: Not calibrated")
        else:
            self.value_status.setText(
                f"{curve.mnemonic}: move cursor over image"
            )
