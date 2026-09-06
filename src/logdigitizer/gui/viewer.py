"""Reusable high-resolution image viewer based on QGraphicsView."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QImage,
    QImageReader,
    QMouseEvent,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
)

from logdigitizer.domain.calibration import (
    CurveDefinition,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibrationPoint,
)
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint
from logdigitizer.processing.geometry import (
    ImagePoint,
    InvalidControlPairsError,
    InvalidQuadrilateralError,
    MultipointRectificationResult,
    RectificationControlPair,
    RectificationResult,
    rectify_image,
    rectify_image_multipoint,
)

from .depth_calibration import DepthCalibrationSelection
from .curve_join_overlay import (
    ImageOverlapGuideDefinition,
    ImageOverlapGuideOverlay,
)
from .digitized_curves import (
    SavedDigitizedCurvesOverlay,
    SavedTraceNodeItem,
    SavedTracePathItem,
)
from .horizontal_calibration import HorizontalCalibrationSelection
from .image_bridge import numpy_to_qimage, qimage_to_numpy
from .manual_digitization import ManualDigitizationOverlay
from .overlays import OverlayManager
from .rectification import RectificationSelection


class ImageViewer(QGraphicsView):
    """Reusable image workspace with zoom, pan and canonical image coordinates."""

    cursorPositionChanged = Signal(float, float, bool)
    visibleCenterImageChanged = Signal(float, float, bool)
    zoomChanged = Signal(float)
    imageChanged = Signal(object)
    rectificationPointsChanged = Signal(object)
    rectificationValidationChanged = Signal(bool, object, object)
    rectificationControlPairsChanged = Signal(object)
    rectificationMultipointValidationChanged = Signal(bool, object)
    depthPointRequested = Signal(float, float)
    depthCalibrationPointMoved = Signal(int, float, float)
    depthCalibrationEditRequested = Signal(int)
    horizontalPointRequested = Signal(float, float)
    horizontalCalibrationPointMoved = Signal(int, float, float)
    horizontalCalibrationEditRequested = Signal(int)
    manualDigitizationPointsChanged = Signal(object)
    manualDigitizationPointRejected = Signal(str)
    manualDigitizationFinishRequested = Signal()
    automaticDigitizationFinishRequested = Signal()

    ZOOM_STEP = 1.20
    MIN_ZOOM = 0.02
    MAX_ZOOM = 100.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setZValue(0.0)
        self._scene.addItem(self._pixmap_item)
        self.overlays = OverlayManager(self._scene)
        self._image_path: Path | None = None
        self._source_qimage = QImage()
        self._display_qimage = QImage()
        self._pan_enabled = True
        self._middle_pan_active = False
        self._middle_pan_last_pos = QPoint()
        self._rectification_enabled = False
        self._rectification_control_pair_capture_enabled = False
        self._depth_calibration_enabled = False
        self._horizontal_calibration_enabled = False
        self._manual_digitization_enabled = False
        self._automatic_digitization_enabled = False
        self._saved_trace_selection_enabled = False
        self._saved_node_drag: tuple[str, str, int, TracePoint] | None = None
        self._saved_node_drag_point: TracePoint | None = None

        self.rectification = RectificationSelection(
            self.overlays,
            self.image_to_scene,
            self.scene_to_image,
            parent=self,
        )
        self.rectification.pointsChanged.connect(
            self.rectificationPointsChanged.emit
        )
        self.rectification.geometryValidationChanged.connect(
            self.rectificationValidationChanged.emit
        )
        self.rectification.controlPairsChanged.connect(
            self.rectificationControlPairsChanged.emit
        )
        self.rectification.multipointValidationChanged.connect(
            self.rectificationMultipointValidationChanged.emit
        )

        self.depth_calibration = DepthCalibrationSelection(
            self.overlays,
            self.image_to_scene,
            self.scene_to_image,
            parent=self,
        )
        self.depth_calibration.pointMoved.connect(
            self.depthCalibrationPointMoved.emit
        )
        self.depth_calibration.editRequested.connect(
            self.depthCalibrationEditRequested.emit
        )

        self.horizontal_calibration = HorizontalCalibrationSelection(
            self.overlays,
            self.image_to_scene,
            self.scene_to_image,
            parent=self,
        )
        self.horizontal_calibration.pointMoved.connect(
            self.horizontalCalibrationPointMoved.emit
        )
        self.horizontal_calibration.editRequested.connect(
            self.horizontalCalibrationEditRequested.emit
        )

        self.saved_digitized_curves = SavedDigitizedCurvesOverlay(
            self.overlays,
            self.image_to_scene,
            parent=self,
        )
        self.image_overlap_guides = ImageOverlapGuideOverlay(
            self.overlays,
            self.image_to_scene,
        )

        self.manual_digitization = ManualDigitizationOverlay(
            self.overlays,
            self.image_to_scene,
            parent=self,
        )
        self.manual_digitization.pointsChanged.connect(
            self.manualDigitizationPointsChanged.emit
        )
        self.manual_digitization.pointRejected.connect(
            self.manualDigitizationPointRejected.emit
        )

        self.setMouseTracking(True)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.NoAnchor
        )
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
        )
        self.setBackgroundBrush(
            Qt.GlobalColor.darkGray
        )

        # Fixed viewport HUD: this is presentation-only and never participates
        # in scene/image coordinates or scientific persistence.
        self.navigation_hud = QLabel(self.viewport())
        self.navigation_hud.setObjectName("navigationHud")
        self.navigation_hud.setTextFormat(Qt.TextFormat.RichText)
        self.navigation_hud.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.navigation_hud.setStyleSheet(
            "QLabel#navigationHud {"
            " background-color: rgba(255, 255, 255, 225);"
            " color: #2F343A;"
            " border: 1px solid rgba(74, 85, 96, 135);"
            " border-radius: 5px;"
            " padding: 7px 10px;"
            " font-size: 13px;"
            "}"
        )
        self.navigation_hud.hide()

    @property
    def image_path(self) -> Path | None:
        return self._image_path

    @property
    def has_image(self) -> bool:
        return not self._pixmap_item.pixmap().isNull()

    @property
    def zoom_factor(self) -> float:
        return float(self.transform().m11())

    @property
    def image_size(self) -> tuple[int, int]:
        pixmap = self._pixmap_item.pixmap()
        return pixmap.width(), pixmap.height()

    @property
    def rectification_enabled(self) -> bool:
        """Return whether left clicks are currently captured as corners."""
        return self._rectification_enabled

    @property
    def rectification_points(self):
        """Return rectification points in canonical source-image coordinates."""
        return self.rectification.points

    @property
    def rectification_control_pair_capture_enabled(self) -> bool:
        """Return whether clicks are capturing one intermediate left/right pair."""
        return self._rectification_control_pair_capture_enabled

    @property
    def rectification_extra_control_pairs(self):
        """Return only user-added intermediate left/right rectification pairs."""
        return self.rectification.extra_control_pairs

    @property
    def rectification_control_pairs(self):
        """Return the complete top/intermediate/bottom control-pair mesh."""
        return self.rectification.control_pairs

    @property
    def depth_calibration_enabled(self) -> bool:
        """Return whether left clicks request new depth control points."""
        return self._depth_calibration_enabled

    @property
    def horizontal_calibration_enabled(self) -> bool:
        """Return whether left clicks request X control points."""
        return self._horizontal_calibration_enabled

    @property
    def manual_digitization_enabled(self) -> bool:
        """Return whether left clicks create manual digitization nodes."""
        return self._manual_digitization_enabled

    @property
    def saved_trace_selection_enabled(self) -> bool:
        """Return whether clicks select persisted trace sections/nodes."""
        return self._saved_trace_selection_enabled

    @property
    def manual_digitization_points(self):
        """Return the current transient manual nodes in creation order."""
        return self.manual_digitization.points

    def load_image(self, path: Path) -> None:
        """Load one image without rebuilding the scene on every interaction."""
        path = Path(path)
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise ValueError(
                reader.errorString()
                or f"Cannot read image: {path}"
            )

        # Keep the exact auto-transformed QImage used for display. This avoids
        # viewport/EXIF-orientation disagreement and preserves common 16-bit
        # grayscale/RGBA formats for the scientific rectification input.
        self._source_qimage = image.copy()
        self._display_qimage = image.copy()

        pixmap = QPixmap.fromImage(self._display_qimage)
        self._pixmap_item.setPixmap(pixmap)
        self._pixmap_item.setPos(0.0, 0.0)
        self._scene.setSceneRect(
            self._pixmap_item.boundingRect()
        )
        self._rectification_control_pair_capture_enabled = False
        self.rectification.clear()
        self.depth_calibration.clear()
        self.horizontal_calibration.clear()
        self.manual_digitization.clear(emit_signal=False)
        self._saved_trace_selection_enabled = False
        self._saved_node_drag = None
        self._saved_node_drag_point = None
        self._middle_pan_active = False
        self._middle_pan_last_pos = QPoint()
        self.saved_digitized_curves.clear()
        self.image_overlap_guides.clear()
        self.overlays.clear_all()
        self._image_path = path.resolve()
        self.fit_to_window()
        self.imageChanged.emit(self._image_path)

    def clear_image(self) -> None:
        """Clear base image and overlays without replacing the scene object."""
        self._pixmap_item.setPixmap(QPixmap())
        self._source_qimage = QImage()
        self._display_qimage = QImage()
        self._scene.setSceneRect(
            0.0,
            0.0,
            1.0,
            1.0,
        )
        self._rectification_control_pair_capture_enabled = False
        self.rectification.clear()
        self.depth_calibration.clear()
        self.horizontal_calibration.clear()
        self.manual_digitization.clear(emit_signal=False)
        self._saved_trace_selection_enabled = False
        self._saved_node_drag = None
        self._saved_node_drag_point = None
        self._middle_pan_active = False
        self._middle_pan_last_pos = QPoint()
        self.saved_digitized_curves.clear()
        self.image_overlap_guides.clear()
        self.overlays.clear_all()
        self._image_path = None
        self.resetTransform()
        self.zoomChanged.emit(self.zoom_factor)
        self.cursorPositionChanged.emit(
            0.0,
            0.0,
            False,
        )
        self.clear_navigation_hud()
        self.visibleCenterImageChanged.emit(
            0.0,
            0.0,
            False,
        )
        self.imageChanged.emit(None)

    def set_pan_enabled(self, enabled: bool) -> None:
        """Enable/disable both standard and middle-button image panning."""
        self._pan_enabled = bool(enabled)
        if not self._pan_enabled and self._middle_pan_active:
            self._middle_pan_active = False
            self._middle_pan_last_pos = QPoint()
        self._update_drag_mode()
        self._update_cursor()

    def set_rectification_enabled(
        self,
        enabled: bool,
    ) -> None:
        """Enable or disable rectification capture without clearing geometry."""
        self._rectification_enabled = bool(enabled)
        if self._rectification_enabled:
            self._depth_calibration_enabled = False
            self._horizontal_calibration_enabled = False
            self._manual_digitization_enabled = False
            self._automatic_digitization_enabled = False
            self._saved_trace_selection_enabled = False
        else:
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
        self._update_drag_mode()
        self._update_cursor()

    def set_rectification_control_pair_capture_enabled(
        self,
        enabled: bool,
    ) -> bool:
        """Capture one intermediate pair as left click followed by right click.

        The established four corners must already be complete. A successfully
        completed pair automatically leaves pair-capture mode so another pair
        requires an explicit new request from the UI.
        """
        requested = bool(enabled)
        if requested and not self.rectification.is_complete:
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._update_drag_mode()
            self._update_cursor()
            return False

        if requested:
            self.set_rectification_enabled(True)
            self.rectification.cancel_pending_control_pair()
            self._rectification_control_pair_capture_enabled = True
        else:
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()

        self._update_drag_mode()
        self._update_cursor()
        return True

    def set_depth_calibration_enabled(
        self,
        enabled: bool,
    ) -> None:
        """Enable or disable click capture for vertical depth references."""
        self._depth_calibration_enabled = bool(enabled)
        if self._depth_calibration_enabled:
            self._rectification_enabled = False
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._horizontal_calibration_enabled = False
            self._manual_digitization_enabled = False
            self._automatic_digitization_enabled = False
            self._saved_trace_selection_enabled = False
        self._update_drag_mode()
        self._update_cursor()

    def set_horizontal_calibration_enabled(
        self,
        enabled: bool,
    ) -> None:
        """Enable or disable click capture for curve X references."""
        self._horizontal_calibration_enabled = bool(enabled)
        if self._horizontal_calibration_enabled:
            self._rectification_enabled = False
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._depth_calibration_enabled = False
            self._manual_digitization_enabled = False
            self._automatic_digitization_enabled = False
            self._saved_trace_selection_enabled = False
        self._update_drag_mode()
        self._update_cursor()

    def set_manual_digitization_enabled(
        self,
        enabled: bool,
    ) -> None:
        """Enable or disable creation of a transient manual trace draft."""
        self._manual_digitization_enabled = bool(enabled)
        if self._manual_digitization_enabled:
            self._rectification_enabled = False
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._depth_calibration_enabled = False
            self._horizontal_calibration_enabled = False
            self._automatic_digitization_enabled = False
            self._saved_trace_selection_enabled = False
        self._update_drag_mode()
        self._update_cursor()

    def set_automatic_digitization_enabled(self, enabled: bool) -> None:
        """Reserve canvas clicks for an active automatic tracking draft."""
        self._automatic_digitization_enabled = bool(enabled)
        if self._automatic_digitization_enabled:
            self._rectification_enabled = False
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._depth_calibration_enabled = False
            self._horizontal_calibration_enabled = False
            self._manual_digitization_enabled = False
            self._saved_trace_selection_enabled = False
            self._saved_node_drag = None
            self._saved_node_drag_point = None
        self._update_drag_mode()
        self._update_cursor()

    def set_saved_trace_selection_enabled(self, enabled: bool) -> None:
        """Enable robust click-selection of persisted sections and nodes.

        Selection mode is mutually exclusive with all point-capture tools and
        disables hand-drag panning so a left click is never swallowed by
        ``ScrollHandDrag`` before it reaches the saved-trace editor.
        """
        self._saved_trace_selection_enabled = bool(enabled)
        if self._saved_trace_selection_enabled:
            self._rectification_enabled = False
            self._rectification_control_pair_capture_enabled = False
            self.rectification.cancel_pending_control_pair()
            self._depth_calibration_enabled = False
            self._horizontal_calibration_enabled = False
            self._manual_digitization_enabled = False
            self._automatic_digitization_enabled = False
        else:
            self._saved_node_drag = None
            self._saved_node_drag_point = None
        self._update_drag_mode()
        self._update_cursor()

    def clear_manual_digitization_draft(self) -> None:
        """Discard the current transient manual trace and its overlay."""
        self.manual_digitization.clear()

    def set_manual_digitization_color(self, color: str) -> None:
        """Set the presentation color for the transient manual trace."""
        self.manual_digitization.set_display_color(color)

    def set_saved_digitized_curves(
        self,
        curves: Sequence[CurveDefinition],
        digitizations: Sequence[DigitizedCurve],
        *,
        active_curve_id: str | None = None,
        show_all_saved: bool = True,
        dim_inactive: bool = True,
        selection_enabled: bool = False,
        selected_section_id: str | None = None,
        selected_node_index: int | None = None,
    ) -> None:
        """Render persisted digitizations for the current segment.

        ``show_all_saved=False`` keeps the active curve visible and hides only
        saved digitizations belonging to non-active curves. ``selection_enabled``
        reveals editable control nodes for a selected persisted section. Clicking
        the selected path may request insertion of a new node; persistence remains
        the controller's responsibility.
        """
        self.saved_digitized_curves.set_curves(
            curves,
            digitizations,
            active_curve_id=active_curve_id,
            show_all_saved=show_all_saved,
            dim_inactive=dim_inactive,
            selection_enabled=selection_enabled,
            selected_section_id=selected_section_id,
            selected_node_index=selected_node_index,
        )

    def clear_saved_digitized_curves(self) -> None:
        """Remove every persisted-curve preview from the current image."""
        self.saved_digitized_curves.clear()

    def set_image_overlap_guides(
        self,
        definitions: Sequence[ImageOverlapGuideDefinition],
    ) -> None:
        """Render non-editable calibrated overlap instructions."""
        width, _height = self.image_size
        self.image_overlap_guides.set_guides(
            definitions,
            image_width=float(width),
        )

    def clear_image_overlap_guides(self) -> None:
        """Remove calibrated overlap instructions from the current image."""
        self.image_overlap_guides.clear()

    def set_horizontal_calibration_points(
        self,
        points: Sequence[HorizontalCalibrationPoint],
        unit: str,
        *,
        preferred_y_by_value: dict[float, float] | None = None,
    ) -> None:
        """Restore selected-curve X controls at transient vertical positions."""
        _, height = self.image_size
        if height <= 0:
            self.horizontal_calibration.clear()
            return
        default_y = min(
            max(80.0, height * 0.18),
            max(0.0, height - 80.0),
        )
        self.horizontal_calibration.set_points(
            points,
            unit,
            default_y=default_y,
            preferred_y_by_value=preferred_y_by_value,
        )

    def clear_horizontal_calibration_points(self) -> None:
        """Remove selected-curve X calibration graphics from the image."""
        self.horizontal_calibration.clear()

    def set_depth_calibration_points(
        self,
        points: Sequence[DepthCalibrationPoint],
        unit: DepthUnit,
        *,
        preferred_x_by_depth: dict[float, float] | None = None,
    ) -> None:
        """Restore depth controls using a standard transient X position."""
        width, _ = self.image_size
        if width <= 0:
            self.depth_calibration.clear()
            return
        default_x = min(
            max(80.0, width * 0.18),
            max(0.0, width - 80.0),
        )
        self.depth_calibration.set_points(
            points,
            unit,
            default_x=default_x,
            preferred_x_by_depth=preferred_x_by_depth,
        )

    def clear_depth_calibration_points(self) -> None:
        """Remove depth calibration graphics from the current image."""
        self.depth_calibration.clear()

    def set_rectification_points(
        self,
        points: Sequence[ImagePoint | Sequence[float]],
    ) -> None:
        """Restore persisted four-point geometry on the active source image."""
        self._rectification_control_pair_capture_enabled = False
        self.rectification.set_points(points)

    def set_rectification_control_pairs(
        self,
        control_pairs: Sequence[
            RectificationControlPair | Sequence[Sequence[float]]
        ],
    ) -> None:
        """Restore a persisted complete multipoint mesh including outer pairs."""
        self._rectification_control_pair_capture_enabled = False
        self.rectification.set_multipoint_control_pairs(control_pairs)

    def clear_rectification_control_pairs(self) -> None:
        """Clear only intermediate control pairs; preserve the four corners."""
        self._rectification_control_pair_capture_enabled = False
        self.rectification.clear_control_pairs()

    def remove_last_rectification_control_pair(self) -> bool:
        """Remove the most recently added intermediate control pair."""
        self._rectification_control_pair_capture_enabled = False
        return self.rectification.remove_last_control_pair()

    def clear_rectification_points(self) -> None:
        """Clear corners and every optional multipoint control pair."""
        self._rectification_control_pair_capture_enabled = False
        self.rectification.clear()

    def visible_center_image_point(self) -> QPointF | None:
        """Return the image coordinate at the visual center of the viewport."""
        if not self.has_image:
            return None
        return self.viewport_to_image(
            self.viewport().rect().center()
        )

    def set_navigation_hud(
        self,
        *,
        depth_text: str,
        curve_mnemonic: str = "",
        curve_value_text: str = "",
        mnemonic_color: str = "#4A5560",
        scale_text: str = "",
        warning_text: str = "",
    ) -> None:
        """Show a compact fixed HUD without affecting the graphics scene."""
        depth = escape(str(depth_text))
        lines = [
            (
                "<div style='white-space:nowrap;'>"
                f"<b>Depth</b>&nbsp;&nbsp;{depth}"
                "</div>"
            )
        ]
        mnemonic = str(curve_mnemonic).strip()
        if mnemonic:
            color = escape(str(mnemonic_color or "#4A5560"), quote=True)
            value = escape(str(curve_value_text or "—"))
            lines.append(
                "<div style='white-space:nowrap; margin-top:2px;'>"
                f"<span style='color:{color}; font-weight:700;'>"
                f"{escape(mnemonic)}</span>&nbsp;&nbsp;{value}"
                "</div>"
            )

        scale = str(scale_text).strip()
        if scale:
            lines.append(
                "<div style='white-space:nowrap; margin-top:2px;'>"
                f"<b>Scale</b>&nbsp;&nbsp;{escape(scale)}"
                "</div>"
            )

        warning = str(warning_text).strip()
        if warning:
            lines.append(
                "<div style='white-space:nowrap; margin-top:3px; "
                "color:#D58B20; font-weight:600;'>"
                f"⚠ {escape(warning)}"
                "</div>"
            )

        self.navigation_hud.setText("".join(lines))
        self.navigation_hud.adjustSize()
        self._position_navigation_hud()
        self.navigation_hud.show()
        self.navigation_hud.raise_()

    def clear_navigation_hud(self) -> None:
        """Hide the viewport HUD."""
        self.navigation_hud.clear()
        self.navigation_hud.hide()

    def _position_navigation_hud(self) -> None:
        """Keep the HUD anchored to the viewport's upper-right corner."""
        margin = 12
        self.navigation_hud.adjustSize()
        x = max(
            margin,
            self.viewport().width() - self.navigation_hud.width() - margin,
        )
        self.navigation_hud.move(x, margin)

    def _emit_visible_center(self) -> None:
        """Publish the image coordinate currently at the viewport center."""
        point = self.visible_center_image_point()
        if point is None:
            self.visibleCenterImageChanged.emit(0.0, 0.0, False)
            return
        self.visibleCenterImageChanged.emit(
            float(point.x()),
            float(point.y()),
            True,
        )

    def source_image_array(self) -> np.ndarray:
        """Return a detached processing raster matching the displayed QImage."""
        if self._source_qimage.isNull():
            raise ValueError("No source image is loaded.")
        return qimage_to_numpy(self._source_qimage)

    def set_display_image_array(
        self,
        image: np.ndarray,
        *,
        preserve_view: bool = True,
    ) -> None:
        """Change only the displayed pixmap; keep the loaded base raster intact."""
        if self._source_qimage.isNull():
            raise ValueError("No base image is loaded.")

        qimage = numpy_to_qimage(image)
        if (
            qimage.width() != self._source_qimage.width()
            or qimage.height() != self._source_qimage.height()
        ):
            raise ValueError(
                "Display enhancement must preserve raster dimensions."
            )

        center = self.mapToScene(self.viewport().rect().center())
        transform = self.transform()

        self._display_qimage = qimage.copy()
        self._pixmap_item.setPixmap(
            QPixmap.fromImage(self._display_qimage)
        )
        self._scene.setSceneRect(self._pixmap_item.boundingRect())

        if preserve_view:
            self.setTransform(transform)
            self.centerOn(center)
        else:
            self.fit_to_window()

    def reset_display_to_base(
        self,
        *,
        preserve_view: bool = True,
    ) -> None:
        """Restore the loaded base raster without re-reading it from disk."""
        if self._source_qimage.isNull():
            return

        center = self.mapToScene(self.viewport().rect().center())
        transform = self.transform()
        self._display_qimage = self._source_qimage.copy()
        self._pixmap_item.setPixmap(
            QPixmap.fromImage(self._display_qimage)
        )
        self._scene.setSceneRect(self._pixmap_item.boundingRect())

        if preserve_view:
            self.setTransform(transform)
            self.centerOn(center)
        else:
            self.fit_to_window()

    def set_working_image(self, path: Path) -> None:
        """Load the rectified derivative as the new visible processing base."""
        self.set_rectification_enabled(False)
        self.load_image(path)

    def displayed_image_array(self) -> np.ndarray:
        """Return a detached array of the raster currently visible in the canvas.

        The exact display QImage is used instead of converting back from QPixmap,
        because QPixmap may change the internal pixel format (for example,
        grayscale to RGBA) depending on the platform.
        """
        if self._display_qimage.isNull():
            raise ValueError("No image is displayed.")
        return qimage_to_numpy(self._display_qimage)

    def compute_rectification_preview(
        self,
        *,
        multipoint: bool = False,
    ) -> RectificationResult | MultipointRectificationResult:
        """Run standard or multipoint rectification without changing the viewer."""
        if len(self.rectification.points) != 4:
            raise InvalidQuadrilateralError(
                "Exactly four rectification points are required."
            )

        if multipoint:
            if not self.rectification.extra_control_pairs:
                raise InvalidControlPairsError(
                    "At least one intermediate control pair is required for "
                    "multipoint rectification."
                )
            return rectify_image_multipoint(
                self.source_image_array(),
                self.rectification.control_pairs,
            )

        return rectify_image(
            self.source_image_array(),
            self.rectification.points,
        )

    def zoom_in(self) -> None:
        self._zoom_at(
            self.viewport().rect().center(),
            self.ZOOM_STEP,
        )

    def zoom_out(self) -> None:
        self._zoom_at(
            self.viewport().rect().center(),
            1.0 / self.ZOOM_STEP,
        )

    def fit_to_window(self) -> None:
        """Fit the full image while preserving aspect ratio."""
        if not self.has_image:
            return
        self.resetTransform()
        self.fitInView(
            self._pixmap_item.boundingRect(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.zoomChanged.emit(self.zoom_factor)
        self._emit_visible_center()

    def actual_size(self) -> None:
        """Set a one scene-unit to one image-pixel transform (1:1)."""
        if not self.has_image:
            return
        center_scene = self.mapToScene(
            self.viewport().rect().center()
        )
        self.resetTransform()
        self.centerOn(center_scene)
        self.zoomChanged.emit(self.zoom_factor)
        self._emit_visible_center()

    def viewport_to_scene(
        self,
        point: QPoint | QPointF,
    ) -> QPointF:
        """Convert viewport coordinates to scene coordinates."""
        if isinstance(point, QPointF):
            point = point.toPoint()
        return self.mapToScene(point)

    def scene_to_image(
        self,
        scene_point: QPointF,
    ) -> QPointF | None:
        """Convert scene coordinates to image pixels if they lie on the image."""
        if not self.has_image:
            return None
        image_point = self._pixmap_item.mapFromScene(
            scene_point
        )
        rect = self._pixmap_item.boundingRect()
        if not rect.contains(image_point):
            return None
        return QPointF(
            float(image_point.x()),
            float(image_point.y()),
        )

    def image_to_scene(
        self,
        image_point: QPointF,
    ) -> QPointF:
        """Convert image pixel coordinates to scene coordinates."""
        return self._pixmap_item.mapToScene(
            image_point
        )

    def viewport_to_image(
        self,
        point: QPoint | QPointF,
    ) -> QPointF | None:
        """Convert viewport coordinates directly to canonical image pixels."""
        return self.scene_to_image(
            self.viewport_to_scene(point)
        )

    def scrollContentsBy(  # noqa: N802 - Qt API
        self,
        dx: int,
        dy: int,
    ) -> None:
        """Track panning/scrollbar navigation using the viewport center."""
        super().scrollContentsBy(dx, dy)
        if hasattr(self, "_pixmap_item") and self.has_image:
            self._emit_visible_center()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Keep the fixed HUD anchored and refresh the visible center."""
        super().resizeEvent(event)
        if hasattr(self, "navigation_hud"):
            self._position_navigation_hud()
        if hasattr(self, "_pixmap_item") and self.has_image:
            self._emit_visible_center()

    def wheelEvent(  # noqa: N802 - Qt API
        self,
        event: QWheelEvent,
    ) -> None:
        if (
            not self.has_image
            or event.angleDelta().y() == 0
        ):
            super().wheelEvent(event)
            return

        factor = (
            self.ZOOM_STEP
            if event.angleDelta().y() > 0
            else 1.0 / self.ZOOM_STEP
        )
        self._zoom_at(
            event.position().toPoint(),
            factor,
        )
        event.accept()

    def mousePressEvent(  # noqa: N802 - Qt API
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            self._pan_enabled
            and self.has_image
            and event.button() == Qt.MouseButton.MiddleButton
        ):
            # Middle-button panning stays available while capture tools own
            # the left button. This avoids interrupting manual/automatic traces.
            self._middle_pan_active = True
            self._middle_pan_last_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if (
            self._manual_digitization_enabled
            and event.button() == Qt.MouseButton.RightButton
        ):
            self.manualDigitizationFinishRequested.emit()
            event.accept()
            return

        if (
            self._automatic_digitization_enabled
            and event.button() == Qt.MouseButton.RightButton
        ):
            self.automaticDigitizationFinishRequested.emit()
            event.accept()
            return
        if (
            self._saved_trace_selection_enabled
            and event.button() == Qt.MouseButton.RightButton
        ):
            # Step 8.3D.4: right-click directly deletes a visible control node.
            # Use the same small viewport-space tolerance as left-click editing
            # so the user does not have to hit the marker with pixel precision.
            viewport_point = event.position().toPoint()
            tolerance_px = 6
            hit_rect = QRect(
                viewport_point.x() - tolerance_px,
                viewport_point.y() - tolerance_px,
                2 * tolerance_px + 1,
                2 * tolerance_px + 1,
            )
            for item in self.items(hit_rect):
                if isinstance(item, SavedTraceNodeItem):
                    self._saved_node_drag = None
                    self._saved_node_drag_point = None
                    self.saved_digitized_curves.request_node_delete(
                        item.curve_id,
                        item.section_id,
                        item.node_index,
                    )
                    event.accept()
                    return

            # A right-click on empty canvas while editing is intentionally a
            # no-op: it must neither pan nor insert nor change selection.
            event.accept()
            return

        if (
            self._saved_trace_selection_enabled
            and event.button() == Qt.MouseButton.LeftButton
        ):
            # Use a small viewport-space tolerance rather than requiring the
            # user to hit a thin cosmetic PCHIP stroke exactly. Nodes have
            # priority over paths when both intersect the same click area.
            viewport_point = event.position().toPoint()
            tolerance_px = 6
            hit_rect = QRect(
                viewport_point.x() - tolerance_px,
                viewport_point.y() - tolerance_px,
                2 * tolerance_px + 1,
                2 * tolerance_px + 1,
            )
            hit_items = self.items(hit_rect)

            for item in hit_items:
                if isinstance(item, SavedTraceNodeItem):
                    origin = item.point_px
                    drag_key = (
                        item.curve_id,
                        item.section_id,
                        item.node_index,
                        origin,
                    )
                    # Selection is synchronous and may rebuild the overlay, so
                    # retain only stable identifiers plus the original point.
                    self.saved_digitized_curves.request_node_selection(
                        item.curve_id,
                        item.section_id,
                        item.node_index,
                    )
                    self._saved_node_drag = drag_key
                    self._saved_node_drag_point = origin
                    self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                    event.accept()
                    return

            for item in hit_items:
                if isinstance(item, SavedTracePathItem) and item.selectable:
                    if item.selected:
                        image_point = self.viewport_to_image(event.position())
                        if image_point is not None:
                            self.saved_digitized_curves.request_node_insert(
                                item.curve_id,
                                item.section_id,
                                float(image_point.x()),
                                float(image_point.y()),
                            )
                    else:
                        self.saved_digitized_curves.request_section_selection(
                            item.curve_id,
                            item.section_id,
                        )
                    event.accept()
                    return

            # Blank clicks in edit-selection mode must not start hand panning.
            event.accept()
            return

        if (
            self._manual_digitization_enabled
            and event.button() == Qt.MouseButton.LeftButton
        ):
            image_point = self.viewport_to_image(event.position())
            if image_point is not None:
                self.manual_digitization.add_point(
                    float(image_point.x()),
                    float(image_point.y()),
                )
            event.accept()
            return

        if (
            self._horizontal_calibration_enabled
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            item = self.itemAt(
                event.position().toPoint()
            )
            if self.horizontal_calibration.is_handle_item(item):
                super().mousePressEvent(event)
                return

            image_point = self.viewport_to_image(
                event.position()
            )
            if image_point is not None:
                self.horizontalPointRequested.emit(
                    float(image_point.x()),
                    float(image_point.y()),
                )
            event.accept()
            return

        if (
            self._depth_calibration_enabled
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            item = self.itemAt(
                event.position().toPoint()
            )
            if self.depth_calibration.is_handle_item(item):
                super().mousePressEvent(event)
                return

            image_point = self.viewport_to_image(
                event.position()
            )
            if image_point is not None:
                self.depthPointRequested.emit(
                    float(image_point.x()),
                    float(image_point.y()),
                )
            event.accept()
            return

        if (
            self._rectification_enabled
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            item = self.itemAt(
                event.position().toPoint()
            )

            if self.rectification.is_handle_item(item):
                super().mousePressEvent(event)
                return

            image_point = self.viewport_to_image(
                event.position()
            )
            if image_point is not None:
                if self._rectification_control_pair_capture_enabled:
                    was_pending = self.rectification.has_pending_control_point
                    accepted = self.rectification.add_control_pair_point(
                        image_point.x(),
                        image_point.y(),
                    )
                    if (
                        accepted
                        and was_pending
                        and not self.rectification.has_pending_control_point
                    ):
                        # One explicit request creates exactly one pair. This
                        # prevents an accidental third click from starting the
                        # next pair.
                        self._rectification_control_pair_capture_enabled = False
                        self._update_cursor()
                else:
                    self.rectification.add_point(
                        image_point.x(),
                        image_point.y(),
                    )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(  # noqa: N802 - Qt API
        self,
        event: QMouseEvent,
    ) -> None:
        if self._middle_pan_active:
            current_pos = event.position().toPoint()
            delta = current_pos - self._middle_pan_last_pos
            self._middle_pan_last_pos = current_pos
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        if self._saved_node_drag is not None:
            image_point = self.viewport_to_image(event.position())
            if image_point is not None:
                curve_id, section_id, node_index, _origin = self._saved_node_drag
                width, height = self.image_size
                preview = self.saved_digitized_curves.preview_node_move(
                    curve_id,
                    section_id,
                    node_index,
                    float(image_point.x()),
                    float(image_point.y()),
                    image_width=width,
                    image_height=height,
                )
                if preview is not None:
                    self._saved_node_drag_point = preview
                    self.cursorPositionChanged.emit(
                        preview.x_px,
                        preview.y_px,
                        True,
                    )
            event.accept()
            return

        image_point = self.viewport_to_image(
            event.position()
        )
        if image_point is None:
            self.cursorPositionChanged.emit(
                0.0,
                0.0,
                False,
            )
        else:
            self.cursorPositionChanged.emit(
                image_point.x(),
                image_point.y(),
                True,
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(  # noqa: N802 - Qt API
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            self._middle_pan_active
            and event.button() == Qt.MouseButton.MiddleButton
        ):
            self._middle_pan_active = False
            self._middle_pan_last_pos = QPoint()
            self._update_cursor()
            event.accept()
            return
        if (
            self._saved_node_drag is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            curve_id, section_id, node_index, origin = self._saved_node_drag
            final = self._saved_node_drag_point or origin
            self._saved_node_drag = None
            self._saved_node_drag_point = None
            self._update_cursor()

            moved = (
                abs(final.x_px - origin.x_px) > 1e-9
                or abs(final.y_px - origin.y_px) > 1e-9
            )
            if moved:
                self.saved_digitized_curves.request_node_move_commit(
                    curve_id,
                    section_id,
                    node_index,
                    final,
                )
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.cursorPositionChanged.emit(
            0.0,
            0.0,
            False,
        )
        super().leaveEvent(event)

    def _update_drag_mode(self) -> None:
        """Resolve left-button conflicts between capture tools and hand pan."""
        if (
            self._rectification_enabled
            or self._depth_calibration_enabled
            or self._horizontal_calibration_enabled
            or self._manual_digitization_enabled
            or self._automatic_digitization_enabled
            or self._saved_trace_selection_enabled
        ):
            self.setDragMode(
                QGraphicsView.DragMode.NoDrag
            )
            return

        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if self._pan_enabled
            else QGraphicsView.DragMode.NoDrag
        )

    def _update_cursor(self) -> None:
        if self._middle_pan_active:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._saved_node_drag is not None:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if (
            self._rectification_enabled
            or self._depth_calibration_enabled
            or self._horizontal_calibration_enabled
            or self._manual_digitization_enabled
            or self._automatic_digitization_enabled
        ):
            self.viewport().setCursor(
                Qt.CursorShape.CrossCursor
            )
        else:
            self.viewport().unsetCursor()

    def _zoom_at(
        self,
        viewport_point: QPoint,
        factor: float,
    ) -> None:
        if not self.has_image:
            return

        current = self.zoom_factor
        proposed = current * factor

        if proposed < self.MIN_ZOOM:
            factor = self.MIN_ZOOM / current
        elif proposed > self.MAX_ZOOM:
            factor = self.MAX_ZOOM / current

        if abs(factor - 1.0) < 1e-12:
            return

        scene_before = self.mapToScene(
            viewport_point
        )
        self.scale(factor, factor)
        scene_after = self.mapToScene(
            viewport_point
        )
        delta = scene_after - scene_before
        self.translate(
            delta.x(),
            delta.y(),
        )
        self.zoomChanged.emit(
            self.zoom_factor
        )
        self._emit_visible_center()
