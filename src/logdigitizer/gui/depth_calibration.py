"""Interactive depth-calibration overlays for the image viewer.

Only ``pixel_y`` and the physical depth belong to the scientific calibration.
The horizontal position of each graphical handle is intentionally transient UI
state and is never persisted.

The visible marker is a screen-size-stable T whose intersection at local
coordinate (0, 0) is the exact calibrated image-pixel anchor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsSceneMouseEvent,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from logdigitizer.domain.calibration import DepthCalibrationPoint, DepthUnit
from .overlays import OverlayManager


def depth_unit_label(unit: DepthUnit) -> str:
    return "m" if unit is DepthUnit.METERS else "ft"


class DepthCalibrationHandle(QGraphicsObject):
    """Movable, screen-size-stable depth label plus left-facing red T marker."""

    LABEL_GAP = 6.0
    H_PADDING = 6.0
    V_PADDING = 3.0
    MIN_MARK_LENGTH = 28.0
    MAX_MARK_LENGTH = 70.0
    MARK_RATIO = 0.65
    CROSS_HALF_LENGTH = 13.0
    MARK_COLOR = QColor(220, 0, 0)

    def __init__(
        self,
        *,
        index: int,
        depth: float,
        unit: DepthUnit,
        position_is_valid: Callable[[QPointF], bool],
        moved_callback: Callable[[int, QPointF], None],
        edit_callback: Callable[[int], None],
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.index = int(index)
        self.depth = float(depth)
        self.unit = unit
        self._position_is_valid = position_is_valid
        self._moved_callback = moved_callback
        self._edit_callback = edit_callback
        self._font = QFont()
        self._font.setPointSizeF(9.0)
        self._label_rect = QRectF()
        self._mark_length = self.MIN_MARK_LENGTH
        self._recompute_geometry()

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setZValue(40.0)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Drag freely in X/Y. The T intersection is the exact calibration "
            "anchor. Only image pixel Y is stored scientifically. "
            "Double-click to edit depth."
        )

    @property
    def label_rect(self) -> QRectF:
        return QRectF(self._label_rect)

    @property
    def mark_length(self) -> float:
        return float(self._mark_length)

    @property
    def mark_color(self) -> QColor:
        return QColor(self.MARK_COLOR)

    @property
    def anchor_local_point(self) -> QPointF:
        return QPointF(0.0, 0.0)

    def set_definition(self, *, index: int, depth: float, unit: DepthUnit) -> None:
        self.prepareGeometryChange()
        self.index = int(index)
        self.depth = float(depth)
        self.unit = unit
        self._recompute_geometry()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = 3.0
        left = min(self._label_rect.left() - half, -self._mark_length - half)
        right = half
        top = min(self._label_rect.top() - half, -self.CROSS_HALF_LENGTH - half)
        bottom = max(
            self._label_rect.bottom() + half,
            self.CROSS_HALF_LENGTH + half,
        )
        return QRectF(left, top, right - left, bottom - top)

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        del widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self._font)

        border = QColor(20, 20, 20)
        if option.state & QStyle.StateFlag.State_Selected:
            border = QColor(0, 80, 180)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(QColor(255, 255, 255, 238))
        painter.drawRoundedRect(self._label_rect, 3.0, 3.0)
        painter.setPen(QPen(QColor(20, 20, 20), 1.0))
        painter.drawText(
            self._label_rect,
            Qt.AlignmentFlag.AlignCenter,
            self._label_text(),
        )

        pen = QPen(self.MARK_COLOR, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(-self._mark_length, 0.0), QPointF(0.0, 0.0))
        painter.drawLine(
            QPointF(0.0, -self.CROSS_HALF_LENGTH),
            QPointF(0.0, self.CROSS_HALF_LENGTH),
        )

    def itemChange(self, change, value):  # noqa: N802
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self.scene() is not None
            and isinstance(value, QPointF)
            and not self._position_is_valid(value)
        ):
            return self.pos()
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._moved_callback(self.index, self.scenePos())

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        event.accept()
        self._edit_callback(self.index)

    def _label_text(self) -> str:
        return f"{self.depth:.2f} {depth_unit_label(self.unit)}"

    def _recompute_geometry(self) -> None:
        metrics = QFontMetricsF(self._font)
        text_width = metrics.horizontalAdvance(self._label_text())
        label_width = text_width + 2.0 * self.H_PADDING
        label_height = metrics.height() + 2.0 * self.V_PADDING
        self._mark_length = min(
            self.MAX_MARK_LENGTH,
            max(self.MIN_MARK_LENGTH, label_width * self.MARK_RATIO),
        )
        self._label_rect = QRectF(
            -self._mark_length - self.LABEL_GAP - label_width,
            -label_height / 2.0,
            label_width,
            label_height,
        )


class DepthCalibrationSelection(QObject):
    pointMoved = Signal(int, float, float)
    editRequested = Signal(int)
    LAYER_NAME = "depth_calibration"

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
        scene_to_image: Callable[[QPointF], QPointF | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._scene_to_image = scene_to_image
        self._handles: list[DepthCalibrationHandle] = []

    @property
    def handles(self) -> tuple[DepthCalibrationHandle, ...]:
        return tuple(self._handles)

    def is_handle_item(self, item: QGraphicsItem | None) -> bool:
        return isinstance(item, DepthCalibrationHandle) and item in self._handles

    def clear(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._handles.clear()

    def set_points(
        self,
        points: Sequence[DepthCalibrationPoint],
        unit: DepthUnit,
        *,
        default_x: float,
        preferred_x_by_depth: Mapping[float, float] | None = None,
    ) -> None:
        existing_x: dict[float, float] = {}
        for handle in self._handles:
            image_point = self._scene_to_image(handle.scenePos())
            if image_point is not None:
                existing_x[handle.depth] = float(image_point.x())

        self.clear()
        preferred = dict(preferred_x_by_depth or {})

        for index, point in enumerate(points):
            x = preferred.get(point.depth, existing_x.get(point.depth, default_x))
            handle = DepthCalibrationHandle(
                index=index,
                depth=point.depth,
                unit=unit,
                position_is_valid=self._position_is_valid,
                moved_callback=self._handle_moved,
                edit_callback=self.editRequested.emit,
            )
            handle.setPos(
                self._image_to_scene(QPointF(float(x), float(point.pixel_y)))
            )
            self._overlays.add_scene_item(self.LAYER_NAME, handle)
            self._handles.append(handle)

    def point_image_x(self, index: int) -> float | None:
        if not 0 <= index < len(self._handles):
            return None
        point = self._scene_to_image(self._handles[index].scenePos())
        return None if point is None else float(point.x())

    def _position_is_valid(self, scene_point: QPointF) -> bool:
        return self._scene_to_image(scene_point) is not None

    def _handle_moved(self, index: int, scene_point: QPointF) -> None:
        point = self._scene_to_image(scene_point)
        if point is not None:
            self.pointMoved.emit(index, float(point.x()), float(point.y()))
