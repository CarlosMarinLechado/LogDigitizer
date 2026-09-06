"""Persistent multi-curve overlay for saved digitized sections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import inf, nextafter

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
)

from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint
from logdigitizer.processing.manual_digitization import sample_pchip_trace

from .overlays import OverlayManager


class SavedTracePathItem(QGraphicsPathItem):
    """One saved section rendered in its project curve's display color."""

    ACTIVE_WIDTH_PX = 2.4
    INACTIVE_WIDTH_PX = 1.5
    SELECTED_WIDTH_PX = 3.4
    DIMMED_OPACITY = 0.42

    def __init__(
        self,
        path: QPainterPath,
        *,
        curve_id: str,
        section_id: str,
        color: str,
        active: bool,
        dim_inactive: bool,
        sample_count: int,
        selectable: bool = False,
        selected: bool = False,
        selection_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(path)
        self.curve_id = str(curve_id)
        self.section_id = str(section_id)
        self.display_color = str(color)
        self.active = bool(active)
        self.sample_count = int(sample_count)
        self.selectable = bool(selectable)
        self.selected = bool(selected)
        self._selection_callback = selection_callback

        if self.selected:
            width = self.SELECTED_WIDTH_PX
        elif self.active:
            width = self.ACTIVE_WIDTH_PX
        else:
            width = self.INACTIVE_WIDTH_PX

        pen = QPen(QColor(self.display_color), width)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton
            if self.selectable
            else Qt.MouseButton.NoButton
        )
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self.selectable
            else Qt.CursorShape.ArrowCursor
        )
        self.setOpacity(
            1.0
            if self.selected
            else (
                self.DIMMED_OPACITY
                if dim_inactive and not self.active
                else 1.0
            )
        )
        self.setZValue(26.0 if self.selected else (22.0 if self.active else 20.0))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            self.selectable
            and event.button() == Qt.MouseButton.LeftButton
            and self._selection_callback is not None
        ):
            self._selection_callback(self.curve_id, self.section_id)
            event.accept()
            return
        super().mousePressEvent(event)


class ScaleOffsetConnectorItem(QGraphicsPathItem):
    """Faint dashed cue for a scale-profile or wrap-around transition."""

    LINE_WIDTH_PX = 1.2
    ACTIVE_OPACITY = 0.52
    DIMMED_OPACITY = 0.24

    def __init__(
        self,
        path: QPainterPath,
        *,
        curve_id: str,
        upper_section_id: str,
        lower_section_id: str,
        upper_scale_id: str,
        lower_scale_id: str,
        upper_offset: int,
        lower_offset: int,
        color: str,
        active: bool,
        dim_inactive: bool,
    ) -> None:
        super().__init__(path)
        self.curve_id = str(curve_id)
        self.upper_section_id = str(upper_section_id)
        self.lower_section_id = str(lower_section_id)
        self.upper_scale_id = str(upper_scale_id)
        self.lower_scale_id = str(lower_scale_id)
        self.upper_offset = int(upper_offset)
        self.lower_offset = int(lower_offset)

        pen = QPen(QColor(str(color)), self.LINE_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setOpacity(
            self.DIMMED_OPACITY
            if dim_inactive and not active
            else self.ACTIVE_OPACITY
        )
        # Keep the cue below the scientific trace paths.
        self.setZValue(18.0)


class SavedTraceNodeItem(QGraphicsEllipseItem):
    """Selectable control-point marker for one already persisted section."""

    RADIUS_PX = 4.5
    SELECTED_RADIUS_PX = 6.0

    def __init__(
        self,
        *,
        curve_id: str,
        section_id: str,
        node_index: int,
        point: TracePoint,
        color: str,
        selected: bool,
        image_to_scene: Callable[[QPointF], QPointF],
        selection_callback: Callable[[str, str, int], None],
    ) -> None:
        is_selected = bool(selected)
        radius = self.SELECTED_RADIUS_PX if is_selected else self.RADIUS_PX
        super().__init__(-radius, -radius, 2.0 * radius, 2.0 * radius)

        self.curve_id = str(curve_id)
        self.section_id = str(section_id)
        self.node_index = int(node_index)
        self.point_px = point
        self.selected = is_selected
        self._selection_callback = selection_callback

        base = QColor(str(color))
        pen = QPen(base.darker(155), 2.2 if self.selected else 1.4)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(base if self.selected else base.lighter(175))
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setPos(image_to_scene(QPointF(point.x_px, point.y_px)))
        self.setZValue(32.0 if self.selected else 31.0)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._selection_callback(
                self.curve_id,
                self.section_id,
                self.node_index,
            )
            event.accept()
            return
        super().mousePressEvent(event)


class SavedDigitizedCurvesOverlay(QObject):
    """Render persisted curves and, in edit-selection mode, their control nodes."""

    sectionSelected = Signal(str, str)
    nodeSelected = Signal(str, str, int)
    nodeInsertRequested = Signal(str, str, float, float)
    nodeDeleteRequested = Signal(str, str, int)
    nodeMoveCommitted = Signal(str, str, int, float, float)

    LAYER_NAME = "saved_digitized_curves"
    PREVIEW_MAX_STEP_Y_PX = 1.0
    MIN_NODE_VERTICAL_GAP_PX = 0.25
    MAX_SCALE_OFFSET_CONNECTOR_GAP_Y_PX = 20.0

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._path_items: list[SavedTracePathItem] = []
        self._connector_items: list[ScaleOffsetConnectorItem] = []
        self._node_items: list[SavedTraceNodeItem] = []
        self._selected_curve_id: str | None = None
        self._selected_section_id: str | None = None
        self._selected_points: tuple[TracePoint, ...] = ()

    @property
    def path_items(self) -> tuple[SavedTracePathItem, ...]:
        return tuple(self._path_items)

    @property
    def node_items(self) -> tuple[SavedTraceNodeItem, ...]:
        return tuple(self._node_items)

    @property
    def connector_items(self) -> tuple[ScaleOffsetConnectorItem, ...]:
        return tuple(self._connector_items)

    def clear(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._path_items.clear()
        self._connector_items.clear()
        self._node_items.clear()
        self._selected_curve_id = None
        self._selected_section_id = None
        self._selected_points = ()

    def set_curves(
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
        """Replace rendered saved geometry for the current segment context.

        ``show_all_saved=False`` is a focus mode: the active curve remains
        visible and only non-active curves are filtered out.

        Manual/processed sections are rendered from their editable control
        points. Raw-only automatic sections are rendered directly from their
        tracker samples, so active and inactive curves follow the same color,
        width and opacity rules regardless of their digitization source.

        ``selection_enabled`` makes editable sections belonging to the active
        curve clickable. Selecting one section reveals its stored control
        nodes; raw-only automatic sections remain visible but are not exposed
        as node-editable geometry.
        """
        self.clear()

        curve_by_id = {curve.curve_id: curve for curve in curves}
        selected_points: tuple[TracePoint, ...] = ()
        selected_color: str | None = None
        selected_curve: str | None = None

        for digitized in digitizations:
            if not show_all_saved and digitized.curve_id != active_curve_id:
                continue
            curve = curve_by_id.get(digitized.curve_id)
            if curve is None:
                continue
            active = curve.curve_id == active_curve_id
            rendered_sections = []

            for section in digitized.sections:
                editable = len(section.control_points_px) >= 2
                points = (
                    section.control_points_px
                    if editable
                    else section.raw_samples_px
                )
                if len(points) < 2:
                    continue
                rendered_sections.append((section, points))
                preview = (
                    sample_pchip_trace(
                        points,
                        max_step_y_px=self.PREVIEW_MAX_STEP_Y_PX,
                    )
                    if editable
                    else points
                )
                if len(preview) < 2:
                    continue

                first_scene = self._image_to_scene(
                    QPointF(preview[0].x_px, preview[0].y_px)
                )
                path = QPainterPath(first_scene)
                for point in preview[1:]:
                    scene_point = self._image_to_scene(
                        QPointF(point.x_px, point.y_px)
                    )
                    path.lineTo(scene_point)

                selected = bool(
                    selection_enabled
                    and active
                    and editable
                    and section.section_id == selected_section_id
                )
                selectable = bool(selection_enabled and active and editable)
                item = SavedTracePathItem(
                    path,
                    curve_id=curve.curve_id,
                    section_id=section.section_id,
                    color=curve.display_color,
                    active=active,
                    dim_inactive=dim_inactive,
                    sample_count=len(preview),
                    selectable=selectable,
                    selected=selected,
                    selection_callback=self.request_section_selection,
                )
                self._overlays.add_item(self.LAYER_NAME, item)
                self._path_items.append(item)

                if selected:
                    selected_points = tuple(points)
                    selected_color = curve.display_color
                    selected_curve = curve.curve_id

            for (upper, upper_points), (lower, lower_points) in zip(
                rendered_sections,
                rendered_sections[1:],
            ):
                if (
                    upper.scale_id == lower.scale_id
                    and upper.scale_offset == lower.scale_offset
                ):
                    continue
                gap_y_px = lower_points[0].y_px - upper_points[-1].y_px
                if not (
                    0.0 <= gap_y_px
                    <= self.MAX_SCALE_OFFSET_CONNECTOR_GAP_Y_PX
                ):
                    continue

                first_scene = self._image_to_scene(
                    QPointF(
                        upper_points[-1].x_px,
                        upper_points[-1].y_px,
                    )
                )
                last_scene = self._image_to_scene(
                    QPointF(
                        lower_points[0].x_px,
                        lower_points[0].y_px,
                    )
                )
                connector_path = QPainterPath(first_scene)
                connector_path.lineTo(last_scene)
                connector = ScaleOffsetConnectorItem(
                    connector_path,
                    curve_id=curve.curve_id,
                    upper_section_id=upper.section_id,
                    lower_section_id=lower.section_id,
                    upper_scale_id=upper.scale_id,
                    lower_scale_id=lower.scale_id,
                    upper_offset=upper.scale_offset,
                    lower_offset=lower.scale_offset,
                    color=curve.display_color,
                    active=active,
                    dim_inactive=dim_inactive,
                )
                self._overlays.add_item(self.LAYER_NAME, connector)
                self._connector_items.append(connector)

        if (
            selection_enabled
            and selected_section_id is not None
            and selected_curve is not None
            and selected_color is not None
        ):
            for index, point in enumerate(selected_points):
                node = SavedTraceNodeItem(
                    curve_id=selected_curve,
                    section_id=selected_section_id,
                    node_index=index,
                    point=point,
                    color=selected_color,
                    selected=index == selected_node_index,
                    image_to_scene=self._image_to_scene,
                    selection_callback=self.request_node_selection,
                )
                self._overlays.add_scene_item(self.LAYER_NAME, node)
                self._node_items.append(node)

            self._selected_curve_id = selected_curve
            self._selected_section_id = selected_section_id
            self._selected_points = selected_points

    def request_node_delete(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
    ) -> bool:
        """Request deletion of one visible control node in edit mode.

        Nodes are only rendered for the currently selected section, so this
        method also verifies that the supplied identifiers still match the
        overlay selection before emitting the destructive request.
        """
        if (
            str(curve_id) != self._selected_curve_id
            or str(section_id) != self._selected_section_id
        ):
            return False
        index = int(node_index)
        if not 0 <= index < len(self._selected_points):
            return False
        self.nodeDeleteRequested.emit(
            str(curve_id),
            str(section_id),
            index,
        )
        return True

    def preview_node_move(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
        x_px: float,
        y_px: float,
        *,
        image_width: int,
        image_height: int,
    ) -> TracePoint | None:
        """Preview one node move without touching persisted project data.

        The vertical coordinate is clamped so the editable polyline remains a
        single-valued ``X=f(Y)`` curve.  The node can approach a neighbour as
        closely as floating-point representation permits, but can never cross
        it or share the same ``y_px``.
        """
        if (
            str(curve_id) != self._selected_curve_id
            or str(section_id) != self._selected_section_id
            or not 0 <= int(node_index) < len(self._selected_points)
            or image_width <= 0
            or image_height <= 0
        ):
            return None

        index = int(node_index)
        points = list(self._selected_points)

        max_x = nextafter(float(image_width), -inf)
        max_y = nextafter(float(image_height), -inf)
        x = min(max(float(x_px), 0.0), max_x)
        y = min(max(float(y_px), 0.0), max_y)

        lower_y = 0.0
        upper_y = max_y
        if index > 0:
            previous_y = points[index - 1].y_px
            lower_y = previous_y + self.MIN_NODE_VERTICAL_GAP_PX
        if index + 1 < len(points):
            next_y = points[index + 1].y_px
            upper_y = next_y - self.MIN_NODE_VERTICAL_GAP_PX

        # Extremely close legacy points may leave less than the normal visual
        # margin. Fall back to the smallest representable strict interval.
        if lower_y > upper_y:
            lower_y = (
                nextafter(points[index - 1].y_px, inf)
                if index > 0
                else 0.0
            )
            upper_y = (
                nextafter(points[index + 1].y_px, -inf)
                if index + 1 < len(points)
                else max_y
            )

        y = min(max(y, lower_y), upper_y)
        point = TracePoint(x, y)
        points[index] = point
        self._selected_points = tuple(points)

        preview = sample_pchip_trace(
            self._selected_points,
            max_step_y_px=self.PREVIEW_MAX_STEP_Y_PX,
        )
        selected_path = next(
            (item for item in self._path_items if item.selected),
            None,
        )
        if selected_path is not None and len(preview) >= 2:
            first_scene = self._image_to_scene(
                QPointF(preview[0].x_px, preview[0].y_px)
            )
            path = QPainterPath(first_scene)
            for sample in preview[1:]:
                scene_point = self._image_to_scene(
                    QPointF(sample.x_px, sample.y_px)
                )
                path.lineTo(scene_point)
            selected_path.setPath(path)
            selected_path.sample_count = len(preview)

        moved_node = next(
            (item for item in self._node_items if item.node_index == index),
            None,
        )
        if moved_node is not None:
            moved_node.point_px = point
            moved_node.setPos(
                self._image_to_scene(QPointF(point.x_px, point.y_px))
            )

        return point

    def request_node_insert(
        self,
        curve_id: str,
        section_id: str,
        x_px: float,
        y_px: float,
    ) -> None:
        """Request insertion of one editable control node on a saved section."""
        self.nodeInsertRequested.emit(
            str(curve_id),
            str(section_id),
            float(x_px),
            float(y_px),
        )

    def request_node_move_commit(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
        point: TracePoint,
    ) -> None:
        """Request persistence of the final previewed node position."""
        self.nodeMoveCommitted.emit(
            str(curve_id),
            str(section_id),
            int(node_index),
            float(point.x_px),
            float(point.y_px),
        )

    def request_section_selection(self, curve_id: str, section_id: str) -> None:
        """Emit a section-selection request from graphics or viewer hit-testing."""
        self.sectionSelected.emit(str(curve_id), str(section_id))

    def request_node_selection(
        self,
        curve_id: str,
        section_id: str,
        node_index: int,
    ) -> None:
        """Emit a node-selection request from graphics or viewer hit-testing."""
        self.nodeSelected.emit(
            str(curve_id),
            str(section_id),
            int(node_index),
        )
