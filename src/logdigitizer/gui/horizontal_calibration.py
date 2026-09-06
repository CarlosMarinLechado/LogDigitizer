"""Interactive horizontal curve-calibration overlays for step 7.3.

Only ``pixel_x`` and the physical curve value belong to scientific calibration.
The vertical placement of each graphical handle is transient UI state and is
never persisted.

Each visible marker is a screen-size-stable T whose intersection at local
coordinate (0, 0) is the exact calibrated image-pixel anchor. For the two-point
scale, the lower pixel-X point faces right and the higher pixel-X point faces
left, independently of whether the physical scale itself is inverted.
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
)

from logdigitizer.domain.calibration import HorizontalCalibrationPoint
from .overlays import OverlayManager


class HorizontalCalibrationHandle(QGraphicsObject):
    """Movable screen-size-stable label plus colored inward-facing T marker."""

    LABEL_GAP = 6.0
    H_PADDING = 6.0
    V_PADDING = 3.0
    MIN_MARK_LENGTH = 28.0
    MAX_MARK_LENGTH = 60.0
    MARK_RATIO = 1.0
    ARM_LENGTH = 36.0
    DEFAULT_MARK_COLOR = QColor(0, 90, 220)

    def __init__(
        self,
        *,
        index: int,
        value: float,
        unit: str,
        position_is_valid: Callable[[QPointF], bool],
        moved_callback: Callable[[int, QPointF], None],
        edit_callback: Callable[[int], None],
        mark_color: QColor | str | None = None,
        arm_direction: int = 1,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.index = int(index)
        self.value = float(value)
        self.unit = str(unit)
        self._position_is_valid = position_is_valid
        self._moved_callback = moved_callback
        self._edit_callback = edit_callback
        self._mark_color = QColor(
            self.DEFAULT_MARK_COLOR if mark_color is None else mark_color
        )
        self._arm_direction = 1 if int(arm_direction) >= 0 else -1
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
        self.setZValue(41.0)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Drag freely in X/Y. The T intersection is the exact calibration "
            "anchor. Only image pixel X is stored scientifically. "
            "Double-click to edit the physical value."
        )

    @property
    def label_rect(self) -> QRectF:
        return QRectF(self._label_rect)

    @property
    def mark_length(self) -> float:
        return float(self._mark_length)

    @property
    def mark_color(self) -> QColor:
        return QColor(self._mark_color)

    @property
    def arm_direction(self) -> int:
        return int(self._arm_direction)

    @property
    def anchor_local_point(self) -> QPointF:
        return QPointF(0.0, 0.0)

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = 3.0
        half_stem = self._mark_length / 2.0
        arm_end = self._arm_direction * self.ARM_LENGTH
        left = min(self._label_rect.left(), -half, arm_end - half)
        right = max(self._label_rect.right(), half, arm_end + half)
        top = min(self._label_rect.top() - half, -half_stem - half)
        bottom = max(self._label_rect.bottom() + half, half_stem + half)
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

        pen = QPen(self._mark_color, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        half_stem = self._mark_length / 2.0
        painter.drawLine(
            QPointF(0.0, -half_stem),
            QPointF(0.0, half_stem),
        )
        painter.drawLine(
            QPointF(0.0, 0.0),
            QPointF(self._arm_direction * self.ARM_LENGTH, 0.0),
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
        text = f"{self.value:.6g}"
        return f"{text} {self.unit}" if self.unit else text

    def _recompute_geometry(self) -> None:
        metrics = QFontMetricsF(self._font)
        text_width = metrics.horizontalAdvance(self._label_text())
        label_width = text_width + 2.0 * self.H_PADDING
        label_height = metrics.height() + 2.0 * self.V_PADDING
        self._mark_length = min(
            self.MAX_MARK_LENGTH,
            max(self.MIN_MARK_LENGTH, label_height * self.MARK_RATIO),
        )
        half_stem = self._mark_length / 2.0
        self._label_rect = QRectF(
            -label_width / 2.0,
            -half_stem - self.LABEL_GAP - label_height,
            label_width,
            label_height,
        )


class HorizontalCalibrationSelection(QObject):
    pointMoved = Signal(int, float, float)
    editRequested = Signal(int)
    LAYER_NAME = "horizontal_calibration"

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
        self._handles: list[HorizontalCalibrationHandle] = []
        self._mark_color = QColor(
            HorizontalCalibrationHandle.DEFAULT_MARK_COLOR
        )

    @property
    def handles(self) -> tuple[HorizontalCalibrationHandle, ...]:
        return tuple(self._handles)

    def is_handle_item(self, item: QGraphicsItem | None) -> bool:
        return (
            isinstance(item, HorizontalCalibrationHandle)
            and item in self._handles
        )

    def set_mark_color(self, color: QColor | str) -> None:
        candidate = QColor(color)
        if not candidate.isValid():
            candidate = QColor(
                HorizontalCalibrationHandle.DEFAULT_MARK_COLOR
            )
        self._mark_color = candidate

    def clear(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._handles.clear()

    @staticmethod
    def _arm_directions(
        points: Sequence[HorizontalCalibrationPoint],
    ) -> dict[int, int]:
        if not points:
            return {}
        if len(points) == 1:
            return {0: 1}

        ordered = sorted(
            range(len(points)),
            key=lambda index: (float(points[index].pixel_x), index),
        )
        directions: dict[int, int] = {
            ordered[0]: 1,
            ordered[-1]: -1,
        }

        if len(ordered) > 2:
            midpoint = (
                float(points[ordered[0]].pixel_x)
                + float(points[ordered[-1]].pixel_x)
            ) / 2.0
            for index in ordered[1:-1]:
                directions[index] = (
                    1
                    if float(points[index].pixel_x) <= midpoint
                    else -1
                )
        return directions

    def set_points(
        self,
        points: Sequence[HorizontalCalibrationPoint],
        unit: str,
        *,
        default_y: float,
        preferred_y_by_value: Mapping[float, float] | None = None,
    ) -> None:
        existing_y: dict[float, float] = {}
        for handle in self._handles:
            point = self._scene_to_image(handle.scenePos())
            if point is not None:
                existing_y[handle.value] = float(point.y())

        self.clear()
        preferred = dict(preferred_y_by_value or {})
        directions = self._arm_directions(points)

        for index, point in enumerate(points):
            y = preferred.get(
                point.value,
                existing_y.get(point.value, default_y),
            )
            handle = HorizontalCalibrationHandle(
                index=index,
                value=point.value,
                unit=unit,
                position_is_valid=self._position_is_valid,
                moved_callback=self._handle_moved,
                edit_callback=self.editRequested.emit,
                mark_color=self._mark_color,
                arm_direction=directions.get(index, 1),
            )
            handle.setPos(
                self._image_to_scene(
                    QPointF(float(point.pixel_x), float(y))
                )
            )
            self._overlays.add_scene_item(self.LAYER_NAME, handle)
            self._handles.append(handle)

    def point_image_y(self, index: int) -> float | None:
        if not 0 <= index < len(self._handles):
            return None
        point = self._scene_to_image(self._handles[index].scenePos())
        return None if point is None else float(point.y())

    def _position_is_valid(self, scene_point: QPointF) -> bool:
        return self._scene_to_image(scene_point) is not None

    def _handle_moved(self, index: int, scene_point: QPointF) -> None:
        point = self._scene_to_image(scene_point)
        if point is not None:
            self.pointMoved.emit(index, float(point.x()), float(point.y()))
