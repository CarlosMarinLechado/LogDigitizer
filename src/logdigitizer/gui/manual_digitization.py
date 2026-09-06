"""Transient graphical overlay for manual curve digitization.

The overlay owns a :class:`ManualTraceDraft` while the user is placing control
points.  Scientific geometry remains in canonical rectified-image pixel
coordinates; Qt items are only a presentation of that geometry.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
)

from logdigitizer.domain.curve_display import DEFAULT_CURVE_DISPLAY_COLOR
from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.manual_digitization import (
    ManualTraceDirection,
    ManualTraceDraft,
    ManualTraceError,
)

from .overlays import OverlayManager


class ManualTraceNodeItem(QGraphicsEllipseItem):
    """Non-editable visual marker for one manual control point.

    Node editing is intentionally deferred to step 8.3.  The marker ignores
    view transformations so it remains easy to see at every zoom level while
    its centre stays anchored to the exact image-pixel coordinate.
    """

    RADIUS_PX = 4.0
    def __init__(self, index: int, color: str) -> None:
        radius = self.RADIUS_PX
        super().__init__(-radius, -radius, 2.0 * radius, 2.0 * radius)
        self.index = int(index)

        base = QColor(color)
        pen = QPen(base, 1.6)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(base.lighter(175))
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(31.0)


class ManualTracePathItem(QGraphicsPathItem):
    """Visual PCHIP preview of the current manual trace draft."""

    LINE_WIDTH_PX = 2.0

    def __init__(
        self,
        path: QPainterPath,
        *,
        sample_count: int,
        color: str,
    ) -> None:
        super().__init__(path)
        self.sample_count = int(sample_count)
        pen = QPen(QColor(color), self.LINE_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(30.0)


class ManualDigitizationOverlay(QObject):
    """Own the transient draft and render its nodes plus PCHIP preview."""

    pointsChanged = Signal(object)
    pointRejected = Signal(str)

    LAYER_NAME = "manual_digitization"
    PREVIEW_MAX_STEP_Y_PX = 1.0

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._draft = ManualTraceDraft()
        self._display_color = DEFAULT_CURVE_DISPLAY_COLOR
        self._nodes: list[ManualTraceNodeItem] = []
        self._path_item: ManualTracePathItem | None = None


    @property
    def display_color(self) -> str:
        """Color used for the active draft line and node borders."""
        return self._display_color

    def set_display_color(self, color: str) -> None:
        """Change draft presentation color without altering its geometry."""
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            raise ValueError(f"Invalid manual trace display color: {color!r}")
        self._display_color = qcolor.name().upper()
        if self._draft.points:
            self._render()

    @property
    def draft(self) -> ManualTraceDraft:
        """Return the transient scientific draft owned by this overlay."""
        return self._draft

    @property
    def points(self) -> tuple[TracePoint, ...]:
        """Return control points in creation order."""
        return self._draft.points

    @property
    def direction(self) -> ManualTraceDirection | None:
        """Return the current creation direction, if already established."""
        return self._draft.direction

    @property
    def nodes(self) -> tuple[ManualTraceNodeItem, ...]:
        """Return the currently rendered node items."""
        return tuple(self._nodes)

    @property
    def path_item(self) -> ManualTracePathItem | None:
        """Return the rendered PCHIP path, or ``None`` with fewer than 2 nodes."""
        return self._path_item

    def add_point(self, x_px: float, y_px: float) -> bool:
        """Add one image-coordinate point and redraw the complete draft.

        Rejected vertical-direction changes leave both model and overlay
        unchanged and emit :attr:`pointRejected` for the future controller/UI.
        """
        try:
            self._draft.add_point((float(x_px), float(y_px)))
        except ManualTraceError as exc:
            self.pointRejected.emit(str(exc))
            return False

        self._render()
        self.pointsChanged.emit(self.points)
        return True

    def clear(self, *, emit_signal: bool = True) -> None:
        """Clear draft geometry and all associated graphics."""
        had_points = bool(self._draft.points)
        self._draft.clear()
        self._overlays.clear_layer(self.LAYER_NAME)
        self._nodes.clear()
        self._path_item = None
        if emit_signal and had_points:
            self.pointsChanged.emit(())

    def _render(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._nodes.clear()
        self._path_item = None

        preview = self._draft.preview_samples(
            max_step_y_px=self.PREVIEW_MAX_STEP_Y_PX,
        )
        if len(preview) >= 2:
            first_scene = self._image_to_scene(
                QPointF(preview[0].x_px, preview[0].y_px)
            )
            path = QPainterPath(first_scene)
            for point in preview[1:]:
                scene_point = self._image_to_scene(
                    QPointF(point.x_px, point.y_px)
                )
                path.lineTo(scene_point)

            path_item = ManualTracePathItem(
                path,
                sample_count=len(preview),
                color=self._display_color,
            )
            self._overlays.add_item(self.LAYER_NAME, path_item)
            self._path_item = path_item

        for index, point in enumerate(self._draft.points):
            node = ManualTraceNodeItem(index, self._display_color)
            node.setPos(
                self._image_to_scene(
                    QPointF(point.x_px, point.y_px)
                )
            )
            self._overlays.add_scene_item(self.LAYER_NAME, node)
            self._nodes.append(node)
