"""Editable standard and multipoint rectification overlay.

The established four-corner workflow remains unchanged. Once those corners are
complete, optional intermediate left/right control pairs can be added. Each
pair represents the same transverse physical section of the distorted log.

Visible markers/labels/lines remain grouped. Draggable hit-targets are direct
scene items so QGraphicsItemGroup cannot intercept independent drag events.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
)

from logdigitizer.processing.geometry import (
    ImagePoint,
    InvalidControlPairsError,
    InvalidQuadrilateralError,
    RectificationControlPair,
    apply_homography,
    compute_perspective_transform,
    estimate_rectified_size,
    validate_control_pairs,
    validate_quad,
)
from .overlays import OverlayManager


class RectificationHandle(QGraphicsEllipseItem):
    """Transparent direct-scene hit target used to drag one corner."""

    HIT_RADIUS = 10.0

    def __init__(
        self,
        index: int,
        on_moved: Callable[[int, QPointF], None],
        on_released: Callable[[int], None],
    ) -> None:
        r = self.HIT_RADIUS
        super().__init__(-r, -r, 2.0 * r, 2.0 * r)
        self.index = index
        self._on_moved = on_moved
        self._on_released = on_released
        self._suppress_callback = False

        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self.setZValue(40.0)
        self.setToolTip(f"Drag rectification corner {index + 1}")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def set_scene_position(self, point: QPointF) -> None:
        self._suppress_callback = True
        try:
            self.setPos(point)
        finally:
            self._suppress_callback = False

    def itemChange(self, change, value):  # noqa: N802 - Qt API
        result = super().itemChange(change, value)
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._suppress_callback
            and self.scene() is not None
        ):
            self._on_moved(self.index, self.scenePos())
        return result

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._on_released(self.index)


class RectificationControlHandle(QGraphicsEllipseItem):
    """Direct-scene hit target for one intermediate control-pair endpoint."""

    HIT_RADIUS = 10.0

    def __init__(
        self,
        pair_index: int,
        side: str,
        on_moved: Callable[[int, str, QPointF], None],
        on_released: Callable[[int, str], None],
    ) -> None:
        if side not in {"left", "right"}:
            raise ValueError("side must be 'left' or 'right'.")

        r = self.HIT_RADIUS
        super().__init__(-r, -r, 2.0 * r, 2.0 * r)
        self.pair_index = int(pair_index)
        self.side = side
        self._on_moved = on_moved
        self._on_released = on_released
        self._suppress_callback = False

        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(255, 255, 255, 1)))
        self.setZValue(42.0)
        self.setToolTip(
            f"Drag control pair {pair_index + 1} {side} point"
        )
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def set_scene_position(self, point: QPointF) -> None:
        self._suppress_callback = True
        try:
            self.setPos(point)
        finally:
            self._suppress_callback = False

    def itemChange(self, change, value):  # noqa: N802 - Qt API
        result = super().itemChange(change, value)
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._suppress_callback
            and self.scene() is not None
        ):
            self._on_moved(self.pair_index, self.side, self.scenePos())
        return result

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._on_released(self.pair_index, self.side)


class RectificationSelection(QObject):
    """Store/render four corners plus optional intermediate control pairs."""

    pointsChanged = Signal(object)
    geometryValidationChanged = Signal(bool, object, object)
    controlPairsChanged = Signal(object)
    multipointValidationChanged = Signal(bool, object)

    LAYER_NAME = "rectification"
    MAX_POINTS = 4
    MARKER_RADIUS = 6.0
    CONTROL_MARKER_RADIUS = 5.0

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
        self._points: list[ImagePoint] = []
        self._handles: list[RectificationHandle] = []
        self._markers: list[QGraphicsEllipseItem] = []
        self._labels: list[QGraphicsSimpleTextItem] = []
        self._lines: list[QGraphicsLineItem] = []

        self._extra_control_pairs: list[RectificationControlPair] = []
        self._pending_control_left: ImagePoint | None = None
        self._control_handles: list[RectificationControlHandle] = []
        self._control_markers: list[QGraphicsEllipseItem] = []
        self._control_labels: list[QGraphicsSimpleTextItem] = []
        self._control_cross_lines: list[QGraphicsLineItem] = []
        self._control_boundary_lines: list[QGraphicsLineItem] = []
        self._pending_marker: QGraphicsEllipseItem | None = None
        self._pending_label: QGraphicsSimpleTextItem | None = None

        self._visible = True
        self._is_geometry_valid: bool | None = None
        self._validation_error: str | None = None
        self._estimated_size: tuple[int, int] | None = None
        self._multipoint_is_geometry_valid: bool | None = None
        self._multipoint_validation_error: str | None = None

    @property
    def points(self) -> tuple[ImagePoint, ...]:
        return tuple(self._points)

    @property
    def is_complete(self) -> bool:
        return len(self._points) == self.MAX_POINTS

    @property
    def is_geometry_valid(self) -> bool | None:
        return self._is_geometry_valid

    @property
    def validation_error(self) -> str | None:
        return self._validation_error

    @property
    def estimated_size(self) -> tuple[int, int] | None:
        return self._estimated_size

    @property
    def extra_control_pairs(self) -> tuple[RectificationControlPair, ...]:
        """Return only user-added intermediate left/right pairs."""
        return tuple(self._extra_control_pairs)

    @property
    def has_pending_control_point(self) -> bool:
        """Return whether the next pair is waiting for its right endpoint."""
        return self._pending_control_left is not None

    @property
    def multipoint_is_geometry_valid(self) -> bool | None:
        return self._multipoint_is_geometry_valid

    @property
    def multipoint_validation_error(self) -> str | None:
        return self._multipoint_validation_error

    @property
    def control_pairs(self) -> tuple[RectificationControlPair, ...]:
        """Return complete processing pairs: top, intermediates, bottom.

        The outer two pairs are derived from the four established corners. The
        intermediate pairs are sorted in the outer homography frame so the user
        may add them in any vertical order.
        """
        if not self.is_complete:
            return ()

        ordered = validate_quad(self._points)
        top = RectificationControlPair(ordered[0], ordered[1])
        bottom = RectificationControlPair(ordered[3], ordered[2])

        if not self._extra_control_pairs:
            return (top, bottom)

        outer_points = (top.left, top.right, bottom.right, bottom.left)
        base = compute_perspective_transform(outer_points)

        ranked: list[tuple[float, int, RectificationControlPair]] = []
        for index, pair in enumerate(self._extra_control_pairs):
            mapped = apply_homography(
                (pair.left, pair.right),
                base.transform_matrix,
            )
            center_y = float((mapped[0, 1] + mapped[1, 1]) * 0.5)
            ranked.append((center_y, index, pair))

        extras = tuple(pair for _y, _index, pair in sorted(ranked))
        return (top, *extras, bottom)

    def add_point(self, x: float, y: float) -> bool:
        if self.is_complete:
            return False
        self._points.append(ImagePoint(float(x), float(y)))
        self._rebuild_overlay()
        self._reset_validation_state()
        self._reset_multipoint_validation_state()
        self.pointsChanged.emit(self.points)
        if self.is_complete:
            self._validate_geometry()
            self._validate_multipoint_geometry()
        return True

    def set_points(self, points: Sequence[ImagePoint | Sequence[float]]) -> None:
        if len(points) > self.MAX_POINTS:
            raise ValueError("Rectification accepts at most four points.")
        restored: list[ImagePoint] = []
        for point in points:
            if isinstance(point, ImagePoint):
                restored.append(point)
            else:
                if len(point) != 2:
                    raise ValueError("Each persisted rectification point needs x and y.")
                restored.append(ImagePoint(float(point[0]), float(point[1])))

        self._points = restored
        self._extra_control_pairs.clear()
        self._pending_control_left = None
        self._rebuild_overlay()
        self._reset_validation_state()
        self._reset_multipoint_validation_state()
        self.pointsChanged.emit(self.points)
        self.controlPairsChanged.emit(self.extra_control_pairs)
        if self.is_complete:
            self._validate_geometry()
            self._validate_multipoint_geometry()

    def set_multipoint_control_pairs(
        self,
        control_pairs: Sequence[RectificationControlPair | Sequence[Sequence[float]]],
    ) -> None:
        """Restore a complete persisted pair mesh, including outer pairs."""
        validated = validate_control_pairs(control_pairs)
        top = validated[0]
        bottom = validated[-1]
        self._points = [
            top.left,
            top.right,
            bottom.right,
            bottom.left,
        ]
        self._extra_control_pairs = list(validated[1:-1])
        self._pending_control_left = None
        self._rebuild_overlay()
        self._reset_validation_state()
        self._reset_multipoint_validation_state()
        self.pointsChanged.emit(self.points)
        self.controlPairsChanged.emit(self.extra_control_pairs)
        self._validate_geometry()
        self._validate_multipoint_geometry()

    def add_control_pair_point(self, x: float, y: float) -> bool:
        """Capture left then right endpoint for one intermediate pair."""
        if not self.is_complete:
            return False

        point = ImagePoint(float(x), float(y))
        if self._pending_control_left is None:
            self._pending_control_left = point
            self._rebuild_overlay()
            return True

        self._extra_control_pairs.append(
            RectificationControlPair(
                self._pending_control_left,
                point,
            )
        )
        self._pending_control_left = None
        self._rebuild_overlay()
        self._reset_multipoint_validation_state()
        self.controlPairsChanged.emit(self.extra_control_pairs)
        self._validate_multipoint_geometry()
        return True

    def cancel_pending_control_pair(self) -> None:
        if self._pending_control_left is None:
            return
        self._pending_control_left = None
        self._rebuild_overlay()

    def remove_last_control_pair(self) -> bool:
        """Remove the most recently added intermediate pair."""
        self.cancel_pending_control_pair()
        if not self._extra_control_pairs:
            return False
        self._extra_control_pairs.pop()
        self._rebuild_overlay()
        self._reset_multipoint_validation_state()
        self.controlPairsChanged.emit(self.extra_control_pairs)
        self._validate_multipoint_geometry()
        return True

    def clear_control_pairs(self) -> None:
        """Clear intermediate pairs while preserving the four corners."""
        changed = bool(self._extra_control_pairs or self._pending_control_left)
        self._extra_control_pairs.clear()
        self._pending_control_left = None
        self._rebuild_overlay()
        self._reset_multipoint_validation_state()
        if changed:
            self.controlPairsChanged.emit(self.extra_control_pairs)
        self._validate_multipoint_geometry()

    def clear(self) -> None:
        self._points.clear()
        self._extra_control_pairs.clear()
        self._pending_control_left = None
        self._handles.clear()
        self._markers.clear()
        self._labels.clear()
        self._lines.clear()
        self._clear_control_item_lists()
        self._overlays.clear_layer(self.LAYER_NAME)
        self._reset_validation_state()
        self._reset_multipoint_validation_state()
        self.pointsChanged.emit(self.points)
        self.controlPairsChanged.emit(self.extra_control_pairs)

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._overlays.set_visible(self.LAYER_NAME, self._visible)

    def is_handle_item(self, item: QGraphicsItem | None) -> bool:
        return item in self._handles or item in self._control_handles

    def _rebuild_overlay(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._handles.clear()
        self._markers.clear()
        self._labels.clear()
        self._lines.clear()
        self._clear_control_item_lists()

        marker_pen = QPen(QColor(20, 20, 20))
        marker_pen.setWidthF(1.0)
        marker_brush = QBrush(QColor(255, 215, 0))

        for index, point in enumerate(self._points):
            scene_pos = self._image_to_scene(QPointF(point.x, point.y))

            r = self.MARKER_RADIUS
            marker = QGraphicsEllipseItem(-r, -r, 2.0 * r, 2.0 * r)
            marker.setPen(marker_pen)
            marker.setBrush(marker_brush)
            marker.setZValue(20.0)
            marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            self._overlays.add_item(self.LAYER_NAME, marker)
            self._set_grouped_scene_position(marker, scene_pos)
            self._markers.append(marker)

            label = QGraphicsSimpleTextItem(str(index + 1))
            label.setBrush(QBrush(QColor(255, 255, 255)))
            font = QFont()
            font.setBold(True)
            label.setFont(font)
            label.setZValue(21.0)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            self._overlays.add_item(self.LAYER_NAME, label)
            self._labels.append(label)

            handle = RectificationHandle(index, self._handle_moved, self._handle_released)
            self._overlays.add_scene_item(self.LAYER_NAME, handle)
            handle.set_scene_position(scene_pos)
            self._handles.append(handle)

        line_count = max(0, len(self._points) - 1)
        if len(self._points) == self.MAX_POINTS:
            line_count += 1

        line_pen = QPen(QColor(255, 215, 0))
        line_pen.setWidthF(2.0)
        line_pen.setCosmetic(True)
        for _ in range(line_count):
            line = QGraphicsLineItem()
            line.setPen(line_pen)
            line.setZValue(10.0)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._overlays.add_item(self.LAYER_NAME, line)
            self._lines.append(line)

        self._build_control_overlay_items(marker_pen)
        self._update_overlay_geometry()
        self._overlays.set_visible(self.LAYER_NAME, self._visible)

    def _build_control_overlay_items(self, marker_pen: QPen) -> None:
        control_brush = QBrush(QColor(80, 220, 255))
        control_pen = QPen(QColor(80, 220, 255))
        control_pen.setWidthF(1.5)
        control_pen.setCosmetic(True)

        for pair_index, pair in enumerate(self._extra_control_pairs):
            for side, point in (("left", pair.left), ("right", pair.right)):
                scene_pos = self._image_to_scene(QPointF(point.x, point.y))
                r = self.CONTROL_MARKER_RADIUS
                marker = QGraphicsEllipseItem(-r, -r, 2.0 * r, 2.0 * r)
                marker.setPen(marker_pen)
                marker.setBrush(control_brush)
                marker.setZValue(22.0)
                marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                marker.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                    True,
                )
                self._overlays.add_item(self.LAYER_NAME, marker)
                self._set_grouped_scene_position(marker, scene_pos)
                self._control_markers.append(marker)

                label_text = f"L{pair_index + 1}" if side == "left" else f"R{pair_index + 1}"
                label = QGraphicsSimpleTextItem(label_text)
                label.setBrush(control_brush)
                font = QFont()
                font.setBold(True)
                label.setFont(font)
                label.setZValue(23.0)
                label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                label.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                    True,
                )
                self._overlays.add_item(self.LAYER_NAME, label)
                self._control_labels.append(label)

                handle = RectificationControlHandle(
                    pair_index,
                    side,
                    self._control_handle_moved,
                    self._control_handle_released,
                )
                self._overlays.add_scene_item(self.LAYER_NAME, handle)
                handle.set_scene_position(scene_pos)
                self._control_handles.append(handle)

            cross = QGraphicsLineItem()
            cross.setPen(control_pen)
            cross.setZValue(12.0)
            cross.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._overlays.add_item(self.LAYER_NAME, cross)
            self._control_cross_lines.append(cross)

        # Two segmented envelopes: top -> intermediate controls -> bottom.
        boundary_segment_count = 0
        if self.is_complete and self._extra_control_pairs:
            boundary_segment_count = 2 * (len(self._extra_control_pairs) + 1)
        for _ in range(boundary_segment_count):
            line = QGraphicsLineItem()
            line.setPen(control_pen)
            line.setZValue(11.0)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._overlays.add_item(self.LAYER_NAME, line)
            self._control_boundary_lines.append(line)

        if self._pending_control_left is not None:
            point = self._pending_control_left
            scene_pos = self._image_to_scene(QPointF(point.x, point.y))
            r = self.CONTROL_MARKER_RADIUS
            marker = QGraphicsEllipseItem(-r, -r, 2.0 * r, 2.0 * r)
            marker.setPen(marker_pen)
            marker.setBrush(control_brush)
            marker.setZValue(22.0)
            marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            marker.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                True,
            )
            self._overlays.add_item(self.LAYER_NAME, marker)
            self._set_grouped_scene_position(marker, scene_pos)
            self._pending_marker = marker

            label = QGraphicsSimpleTextItem("L…")
            label.setBrush(control_brush)
            label.setZValue(23.0)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            label.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                True,
            )
            self._overlays.add_item(self.LAYER_NAME, label)
            self._pending_label = label
            self._set_grouped_scene_position(label, scene_pos + QPointF(8.0, -18.0))

    def _handle_moved(self, index: int, scene_point: QPointF) -> None:
        image_point = self._scene_to_image(scene_point)
        if image_point is None:
            previous = self._points[index]
            self._handles[index].set_scene_position(
                self._image_to_scene(QPointF(previous.x, previous.y))
            )
            return

        self._points[index] = ImagePoint(float(image_point.x()), float(image_point.y()))
        self._update_overlay_geometry()
        self._reset_validation_state()
        self._reset_multipoint_validation_state()
        self.pointsChanged.emit(self.points)

    def _handle_released(self, index: int) -> None:
        del index
        if self.is_complete:
            self._validate_geometry()
            self._validate_multipoint_geometry()

    def _control_handle_moved(
        self,
        pair_index: int,
        side: str,
        scene_point: QPointF,
    ) -> None:
        image_point = self._scene_to_image(scene_point)
        if image_point is None:
            pair = self._extra_control_pairs[pair_index]
            previous = pair.left if side == "left" else pair.right
            handle = self._find_control_handle(pair_index, side)
            if handle is not None:
                handle.set_scene_position(
                    self._image_to_scene(QPointF(previous.x, previous.y))
                )
            return

        pair = self._extra_control_pairs[pair_index]
        moved = ImagePoint(float(image_point.x()), float(image_point.y()))
        self._extra_control_pairs[pair_index] = RectificationControlPair(
            moved if side == "left" else pair.left,
            moved if side == "right" else pair.right,
        )
        self._update_control_overlay_geometry()
        self._reset_multipoint_validation_state()
        self.controlPairsChanged.emit(self.extra_control_pairs)

    def _control_handle_released(self, pair_index: int, side: str) -> None:
        del pair_index, side
        self._validate_multipoint_geometry()

    def _find_control_handle(
        self,
        pair_index: int,
        side: str,
    ) -> RectificationControlHandle | None:
        for handle in self._control_handles:
            if handle.pair_index == pair_index and handle.side == side:
                return handle
        return None

    def _update_overlay_geometry(self) -> None:
        if self._handles:
            scene_points = [handle.scenePos() for handle in self._handles]

            for marker, scene_point in zip(self._markers, scene_points):
                self._set_grouped_scene_position(marker, scene_point)

            for index, (label, scene_point) in enumerate(
                zip(self._labels, scene_points),
                start=1,
            ):
                label.setText(str(index))
                self._set_grouped_scene_position(label, scene_point + QPointF(8.0, -18.0))

            segments = [(a, b) for a, b in zip(scene_points, scene_points[1:])]
            if len(scene_points) == self.MAX_POINTS:
                segments.append((scene_points[-1], scene_points[0]))

            for line_item, (start, end) in zip(self._lines, segments):
                self._set_line_scene_points(line_item, start, end)

        self._update_control_overlay_geometry()

    def _update_control_overlay_geometry(self) -> None:
        handle_positions: dict[tuple[int, str], QPointF] = {
            (handle.pair_index, handle.side): handle.scenePos()
            for handle in self._control_handles
        }

        for pair_index, _pair in enumerate(self._extra_control_pairs):
            left = handle_positions.get((pair_index, "left"))
            right = handle_positions.get((pair_index, "right"))
            if left is None or right is None:
                continue

            marker_offset = pair_index * 2
            if marker_offset + 1 < len(self._control_markers):
                self._set_grouped_scene_position(
                    self._control_markers[marker_offset], left
                )
                self._set_grouped_scene_position(
                    self._control_markers[marker_offset + 1], right
                )
            if marker_offset + 1 < len(self._control_labels):
                self._set_grouped_scene_position(
                    self._control_labels[marker_offset],
                    left + QPointF(8.0, -18.0),
                )
                self._set_grouped_scene_position(
                    self._control_labels[marker_offset + 1],
                    right + QPointF(8.0, -18.0),
                )
            if pair_index < len(self._control_cross_lines):
                self._set_line_scene_points(
                    self._control_cross_lines[pair_index], left, right
                )

        if self.is_complete and self._extra_control_pairs:
            try:
                processing_pairs = self.control_pairs
            except (InvalidQuadrilateralError, InvalidControlPairsError):
                processing_pairs = ()

            if processing_pairs:
                left_chain = [
                    self._image_to_scene(QPointF(pair.left.x, pair.left.y))
                    for pair in processing_pairs
                ]
                right_chain = [
                    self._image_to_scene(QPointF(pair.right.x, pair.right.y))
                    for pair in processing_pairs
                ]
                boundary_segments = [
                    *zip(left_chain, left_chain[1:]),
                    *zip(right_chain, right_chain[1:]),
                ]
                for line, (start, end) in zip(
                    self._control_boundary_lines,
                    boundary_segments,
                ):
                    self._set_line_scene_points(line, start, end)

        if self._pending_control_left is not None:
            scene_pos = self._image_to_scene(
                QPointF(
                    self._pending_control_left.x,
                    self._pending_control_left.y,
                )
            )
            if self._pending_marker is not None:
                self._set_grouped_scene_position(self._pending_marker, scene_pos)
            if self._pending_label is not None:
                self._set_grouped_scene_position(
                    self._pending_label,
                    scene_pos + QPointF(8.0, -18.0),
                )

    def _set_grouped_scene_position(self, item: QGraphicsItem, scene_point: QPointF) -> None:
        parent = item.parentItem()
        item.setPos(scene_point if parent is None else parent.mapFromScene(scene_point))

    @staticmethod
    def _set_line_scene_points(
        line_item: QGraphicsLineItem,
        start: QPointF,
        end: QPointF,
    ) -> None:
        parent = line_item.parentItem()
        if parent is None:
            start_local, end_local = start, end
        else:
            start_local = parent.mapFromScene(start)
            end_local = parent.mapFromScene(end)
        line_item.setLine(
            start_local.x(),
            start_local.y(),
            end_local.x(),
            end_local.y(),
        )

    def _validate_geometry(self) -> None:
        try:
            validate_quad(self._points)
            size = estimate_rectified_size(self._points)
        except InvalidQuadrilateralError as exc:
            self._is_geometry_valid = False
            self._validation_error = str(exc)
            self._estimated_size = None
        else:
            self._is_geometry_valid = True
            self._validation_error = None
            self._estimated_size = size
        self.geometryValidationChanged.emit(
            bool(self._is_geometry_valid),
            self._validation_error,
            self._estimated_size,
        )

    def _validate_multipoint_geometry(self) -> None:
        if not self.is_complete or not self._extra_control_pairs:
            self._reset_multipoint_validation_state()
            self.multipointValidationChanged.emit(False, None)
            return

        try:
            validate_control_pairs(self.control_pairs)
        except (InvalidControlPairsError, InvalidQuadrilateralError) as exc:
            self._multipoint_is_geometry_valid = False
            self._multipoint_validation_error = str(exc)
        else:
            self._multipoint_is_geometry_valid = True
            self._multipoint_validation_error = None
        self.multipointValidationChanged.emit(
            bool(self._multipoint_is_geometry_valid),
            self._multipoint_validation_error,
        )

    def _reset_validation_state(self) -> None:
        self._is_geometry_valid = None
        self._validation_error = None
        self._estimated_size = None

    def _reset_multipoint_validation_state(self) -> None:
        self._multipoint_is_geometry_valid = None
        self._multipoint_validation_error = None

    def _clear_control_item_lists(self) -> None:
        self._control_handles.clear()
        self._control_markers.clear()
        self._control_labels.clear()
        self._control_cross_lines.clear()
        self._control_boundary_lines.clear()
        self._pending_marker = None
        self._pending_label = None
