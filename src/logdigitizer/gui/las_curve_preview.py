"""Modeless visual preview of the final LAS curve reconstruction."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from math import ceil, floor, isfinite, log10

from PySide6.QtCore import QLocale, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from logdigitizer.application.project_service import (
    LasCurveTransformTransition,
    ProjectService,
)
from logdigitizer.domain.calibration import DepthUnit, ScaleType
from logdigitizer.gui.icons import app_icon
from logdigitizer.io.las_export import (
    LasCurveGap,
    LasExportError,
    LasExportPlan,
    LasExportPreview,
)


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Floating-point editor that accepts either decimal point or comma."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setLocale(QLocale.c())

    def validate(self, text: str, pos: int):  # noqa: N802 - Qt API
        normalized = str(text).replace(",", ".")
        state, _normalized_text, _normalized_pos = super().validate(
            normalized,
            pos,
        )
        return state, text, pos

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API
        return f"{value:.12g}"

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt API
        value_text = str(text).strip()

        suffix = self.suffix().strip()
        if suffix and value_text.endswith(suffix):
            value_text = value_text[: -len(suffix)].strip()

        prefix = self.prefix().strip()
        if prefix and value_text.startswith(prefix):
            value_text = value_text[len(prefix) :].strip()

        if "," in value_text and "." not in value_text:
            value_text = value_text.replace(",", ".")

        try:
            return float(value_text)
        except ValueError:
            return float(self.value())


class _NavigableTransitionMarker(QGraphicsPathItem):
    """Fixed-size QC marker with arrow cursor and double-click navigation."""

    def __init__(
        self,
        *,
        kind: str,
        pen: QPen,
        brush: QBrush,
        callback: Callable[[], None],
    ) -> None:
        super().__init__()
        self.kind = str(kind)
        self._navigation_callback = callback

        path = QPainterPath()
        if self.kind == "scale":
            path.moveTo(0.0, -5.5)
            path.lineTo(5.5, 0.0)
            path.lineTo(0.0, 5.5)
            path.lineTo(-5.5, 0.0)
            path.closeSubpath()
        else:
            path.addEllipse(QRectF(-4.5, -4.5, 9.0, 9.0))

        self.setPath(path)
        self.setPen(pen)
        self.setBrush(brush)
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setZValue(10.0)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

        # ScrollHandDrag gives the view a hand cursor. An explicit item cursor
        # makes these small QC markers much easier to target.
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def trigger_navigation(self) -> None:
        self._navigation_callback()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() is Qt.MouseButton.LeftButton:
            self.trigger_navigation()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)



class _FixedScreenTextItem(QGraphicsTextItem):
    """Text anchored in scene coordinates with a fixed pixel offset."""

    def __init__(
        self,
        text: str,
        *,
        color: QColor,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> None:
        super().__init__(str(text))
        self.setDefaultTextColor(color)
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self._offset_x = float(offset_x)
        self._offset_y = float(offset_y)

    def set_anchor(
        self,
        scene_x: float,
        scene_y: float,
        *,
        horizontal: str = "left",
        vertical: str = "top",
    ) -> None:
        """Place the item at a scene anchor plus a screen-space offset."""
        rect = self.boundingRect()

        local_x = self._offset_x
        if horizontal == "center":
            local_x -= rect.width() / 2.0
        elif horizontal == "right":
            local_x -= rect.width()

        local_y = self._offset_y
        if vertical == "center":
            local_y -= rect.height() / 2.0
        elif vertical == "bottom":
            local_y -= rect.height()

        self.setPos(float(scene_x), float(scene_y))
        transform = QTransform()
        transform.translate(local_x, local_y)
        self.setTransform(transform)


class LasCurveGraphicsView(QGraphicsView):
    """Interactive value-versus-depth plot drawn only with Qt graphics."""

    navigationRequested = Signal(str, str, float)

    BASE_PLOT_WIDTH = 820.0
    BASE_PLOT_HEIGHT = 1050.0
    LEFT_MARGIN = 92.0
    RIGHT_MARGIN = 34.0
    TOP_MARGIN = 34.0
    BOTTOM_MARGIN = 70.0
    MIN_DEPTH_STRETCH = 0.25
    MAX_DEPTH_STRETCH = 8.0
    DEPTH_AXIS_PADDING_FRACTION = 0.02

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.setBackgroundBrush(QColor("#FFFFFF"))

        self._depths: tuple[float, ...] = ()
        self._values: tuple[float | None, ...] = ()
        self._curve_id = ""
        self._mnemonic = ""
        self._unit = ""
        self._depth_unit = "m"
        self._curve_color = QColor("#1F5F8B")
        self._scale_type = ScaleType.LINEAR
        self._depth_stretch = 1.0
        self._depth_min = 0.0
        self._depth_max = 1.0
        self._value_min = 0.0
        self._value_max = 1.0
        self.depth_ticks: tuple[float, ...] = ()
        self.value_ticks: tuple[float, ...] = ()
        self._transitions: tuple[LasCurveTransformTransition, ...] = ()
        self._gaps: tuple[LasCurveGap, ...] = ()
        self._show_qc_markers = True
        self.segment_count = 0
        self.transition_marker_count = 0
        self.offset_marker_count = 0
        self.scale_marker_count = 0
        self.gap_connector_count = 0
        self.transition_marker_positions: tuple[QPointF, ...] = ()
        self.transition_marker_items: tuple[
            _NavigableTransitionMarker, ...
        ] = ()
        self.gap_connector_endpoints: tuple[
            tuple[QPointF, QPointF], ...
        ] = ()

    @property
    def depth_stretch(self) -> float:
        return self._depth_stretch

    def set_curve(
        self,
        *,
        depths: tuple[float, ...],
        values: tuple[float | None, ...],
        mnemonic: str,
        unit: str,
        depth_unit: str,
        color: str,
        scale_type: ScaleType | str,
        transitions: tuple[LasCurveTransformTransition, ...] = (),
        gaps: tuple[LasCurveGap, ...] = (),
        curve_id: str = "",
    ) -> None:
        self._depths = tuple(float(depth) for depth in depths)
        self._values = tuple(
            None if value is None else float(value)
            for value in values
        )
        self._curve_id = str(curve_id)
        self._mnemonic = str(mnemonic)
        self._unit = str(unit)
        self._depth_unit = str(depth_unit)

        curve_color = QColor(str(color))
        self._curve_color = (
            curve_color
            if curve_color.isValid()
            else QColor("#1F5F8B")
        )
        self._scale_type = (
            scale_type
            if isinstance(scale_type, ScaleType)
            else ScaleType(str(scale_type).lower())
        )
        self._transitions = tuple(transitions)
        self._gaps = tuple(gaps)

        self._render_scene()
        self.fit_full_curve()

    def clear_curve(self, message: str = "No LAS curve data available.") -> None:
        self._depths = ()
        self._values = ()
        self.segment_count = 0
        self.transition_marker_count = 0
        self.offset_marker_count = 0
        self.scale_marker_count = 0
        self.gap_connector_count = 0
        self.transition_marker_positions = ()
        self.transition_marker_items = ()
        self.gap_connector_endpoints = ()
        scene = self.scene()
        scene.clear()
        text = scene.addText(message)
        text.setDefaultTextColor(QColor("#6B7280"))
        text.setPos(24.0, 24.0)
        scene.setSceneRect(0.0, 0.0, 760.0, 520.0)
        self.resetTransform()

    def set_qc_markers_visible(self, visible: bool) -> None:
        """Show or hide all LAS visual-QC markers together."""
        requested = bool(visible)
        if requested == self._show_qc_markers:
            return
        self._show_qc_markers = requested
        self._render_scene()

    def stretch_depth(self) -> None:
        self._set_depth_stretch(self._depth_stretch * 1.5)

    def compress_depth(self) -> None:
        self._set_depth_stretch(self._depth_stretch / 1.5)

    def _set_depth_stretch(self, value: float) -> None:
        bounded = min(
            max(float(value), self.MIN_DEPTH_STRETCH),
            self.MAX_DEPTH_STRETCH,
        )
        if abs(bounded - self._depth_stretch) <= 1e-12:
            return

        center_ratio = 0.5
        if self._depths and self._depth_max > self._depth_min:
            center_scene = self.mapToScene(self.viewport().rect().center())
            plot_top = self.TOP_MARGIN
            plot_height = self.BASE_PLOT_HEIGHT * self._depth_stretch
            if plot_height > 0:
                center_ratio = (center_scene.y() - plot_top) / plot_height
                center_ratio = min(max(center_ratio, 0.0), 1.0)

        self._depth_stretch = bounded
        self._render_scene()
        self.fit_width()

        new_height = self.BASE_PLOT_HEIGHT * self._depth_stretch
        center_y = self.TOP_MARGIN + center_ratio * new_height
        center_x = self.LEFT_MARGIN + self.BASE_PLOT_WIDTH / 2.0
        self.centerOn(QPointF(center_x, center_y))

    def fit_full_curve(self) -> None:
        rect = self.scene().sceneRect()
        if rect.isValid() and not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def fit_width(self) -> None:
        rect = self.scene().sceneRect()
        if not rect.isValid() or rect.isEmpty():
            return
        available = max(1, self.viewport().width() - 12)
        factor = available / max(rect.width(), 1.0)
        self.resetTransform()
        self.scale(factor, factor)

    def scene_y_for_depth(self, depth: float) -> float | None:
        """Return the current scene Y; useful for navigation and regression tests."""
        if not self._depths or self._depth_max <= self._depth_min:
            return None
        ratio = (float(depth) - self._depth_min) / (
            self._depth_max - self._depth_min
        )
        return (
            self.TOP_MARGIN
            + ratio * self.BASE_PLOT_HEIGHT * self._depth_stretch
        )

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        event.accept()

    @staticmethod
    def _format_tick(value: float) -> str:
        """Return clean axis labels, preferring ordinary integer notation."""
        number = float(value)
        nearest = round(number)
        if abs(number - nearest) <= 1e-9 * max(1.0, abs(number)):
            return str(int(nearest))
        return f"{number:.10g}"

    @staticmethod
    def _nice_linear_axis(
        minimum: float,
        maximum: float,
        *,
        target_ticks: int = 7,
        minimum_step: float | None = None,
    ) -> tuple[float, float, tuple[float, ...]]:
        """Build human-friendly 1/2/5 × 10^n linear ticks."""
        low = float(minimum)
        high = float(maximum)
        if high < low:
            low, high = high, low

        if abs(high - low) <= 1e-15:
            padding = max(
                float(minimum_step or 0.0),
                1.0 if low == 0.0 else abs(low) * 0.05,
            )
            if padding <= 0.0:
                padding = 1.0
            low -= padding
            high += padding

        span = high - low
        raw_step = span / max(1, int(target_ticks) - 1)
        exponent = floor(log10(raw_step)) if raw_step > 0.0 else 0
        magnitude = 10.0 ** exponent
        fraction = raw_step / magnitude

        if fraction <= 1.0:
            nice_fraction = 1.0
        elif fraction <= 2.0:
            nice_fraction = 2.0
        elif fraction <= 5.0:
            nice_fraction = 5.0
        else:
            nice_fraction = 10.0

        step = nice_fraction * magnitude
        if minimum_step is not None:
            step = max(step, float(minimum_step))

        axis_min = floor(low / step) * step
        axis_max = ceil(high / step) * step
        if axis_max <= axis_min:
            axis_max = axis_min + step

        count = int(round((axis_max - axis_min) / step))
        ticks = tuple(
            round(axis_min + index * step, 12)
            for index in range(count + 1)
        )
        return float(axis_min), float(axis_max), ticks

    @staticmethod
    def _logarithmic_axis(
        values: tuple[float, ...],
    ) -> tuple[
        float,
        float,
        tuple[float, ...],
        tuple[float, ...],
    ]:
        """Return decade bounds plus major and 2/5 minor logarithmic ticks."""
        positive = tuple(
            float(value)
            for value in values
            if isfinite(value) and float(value) > 0.0
        )
        if not positive:
            raise ValueError(
                "A logarithmic LAS curve requires positive sampled values."
            )

        minimum = min(positive)
        maximum = max(positive)
        first_decade = floor(log10(minimum))
        last_decade = ceil(log10(maximum))

        if first_decade == last_decade:
            # A constant power-of-ten curve still needs visible horizontal span.
            first_decade -= 1
            last_decade += 1

        axis_min = 10.0 ** first_decade
        axis_max = 10.0 ** last_decade
        major = tuple(
            10.0 ** exponent
            for exponent in range(first_decade, last_decade + 1)
        )

        minor: list[float] = []
        for exponent in range(first_decade, last_decade):
            decade = 10.0 ** exponent
            for multiplier in (2.0, 5.0):
                value = multiplier * decade
                if axis_min < value < axis_max:
                    minor.append(value)

        return (
            float(axis_min),
            float(axis_max),
            major,
            tuple(minor),
        )

    def _render_scene(self) -> None:
        scene = self.scene()
        scene.clear()
        self.segment_count = 0
        self.transition_marker_count = 0
        self.offset_marker_count = 0
        self.scale_marker_count = 0
        self.gap_connector_count = 0
        self.transition_marker_positions = ()
        self.transition_marker_items = ()
        self.gap_connector_endpoints = ()

        if not self._depths or len(self._depths) != len(self._values):
            self.clear_curve()
            return

        valid_values = tuple(
            float(value)
            for value in self._values
            if value is not None and isfinite(value)
        )
        if not valid_values:
            self.clear_curve("The selected LAS curve contains only NULL samples.")
            return

        valid_depths = tuple(
            float(depth)
            for depth, value in zip(self._depths, self._values)
            if self._is_plottable_value(value)
        )
        if not valid_depths:
            self.clear_curve("The selected LAS curve contains only NULL samples.")
            return

        curve_depth_min = min(valid_depths)
        curve_depth_max = max(valid_depths)
        curve_depth_span = curve_depth_max - curve_depth_min

        if curve_depth_span <= 1e-12:
            depth_padding = max(1.0, abs(curve_depth_min) * 0.02)
        else:
            depth_padding = max(
                0.5,
                curve_depth_span * self.DEPTH_AXIS_PADDING_FRACTION,
            )

        # Plot limits follow the useful depth range of the selected curve,
        # not the overall LAS START/STOP. Ticks are computed independently.
        self._depth_min = curve_depth_min - depth_padding
        self._depth_max = curve_depth_max + depth_padding

        _tick_axis_min, _tick_axis_max, depth_ticks = self._nice_linear_axis(
            curve_depth_min,
            curve_depth_max,
            target_ticks=7,
            minimum_step=1.0,
        )
        self.depth_ticks = tuple(
            tick
            for tick in depth_ticks
            if self._depth_min <= tick <= self._depth_max
        )

        log_minor_ticks: tuple[float, ...] = ()
        if self._scale_type is ScaleType.LOGARITHMIC:
            positive_values = tuple(
                value for value in valid_values if value > 0.0
            )
            if not positive_values:
                self.clear_curve(
                    "The selected logarithmic LAS curve has no positive samples."
                )
                return
            (
                self._value_min,
                self._value_max,
                self.value_ticks,
                log_minor_ticks,
            ) = self._logarithmic_axis(positive_values)
        else:
            (
                self._value_min,
                self._value_max,
                self.value_ticks,
            ) = self._nice_linear_axis(
                min(valid_values),
                max(valid_values),
                target_ticks=7,
            )

        plot_left = self.LEFT_MARGIN
        plot_top = self.TOP_MARGIN
        plot_width = self.BASE_PLOT_WIDTH
        plot_height = self.BASE_PLOT_HEIGHT * self._depth_stretch
        scene_width = (
            self.LEFT_MARGIN + plot_width + self.RIGHT_MARGIN
        )
        scene_height = (
            self.TOP_MARGIN + plot_height + self.BOTTOM_MARGIN
        )
        scene.setSceneRect(
            QRectF(0.0, 0.0, scene_width, scene_height)
        )

        axis_pen = QPen(QColor("#4A5560"))
        axis_pen.setWidthF(1.2)
        grid_pen = QPen(QColor("#E5E7EB"))
        grid_pen.setWidthF(0.8)
        curve_pen = QPen(self._curve_color)
        curve_pen.setWidthF(1.7)

        scene.addLine(
            plot_left,
            plot_top,
            plot_left,
            plot_top + plot_height,
            axis_pen,
        )
        scene.addLine(
            plot_left,
            plot_top + plot_height,
            plot_left + plot_width,
            plot_top + plot_height,
            axis_pen,
        )

        # Depth ticks: clean rounded values; depth increases downward.
        for depth in self.depth_ticks:
            y = self.scene_y_for_depth(depth)
            if y is None:
                continue
            scene.addLine(
                plot_left,
                y,
                plot_left + plot_width,
                y,
                grid_pen,
            )
            label = _FixedScreenTextItem(
                self._format_tick(depth),
                color=QColor("#4A5560"),
                offset_x=-8.0,
            )
            scene.addItem(label)
            label.set_anchor(
                plot_left,
                y,
                horizontal="right",
                vertical="center",
            )

        # Minor logarithmic X grid lines are intentionally unlabeled.
        if self._scale_type is ScaleType.LOGARITHMIC:
            minor_pen = QPen(QColor("#F0F2F4"))
            minor_pen.setWidthF(0.7)
            for value in log_minor_ticks:
                x = self._value_to_scene_x(value)
                scene.addLine(
                    x,
                    plot_top,
                    x,
                    plot_top + plot_height,
                    minor_pen,
                )

        # Curve-value major ticks.
        for value in self.value_ticks:
            x = self._value_to_scene_x(value)
            scene.addLine(
                x,
                plot_top,
                x,
                plot_top + plot_height,
                grid_pen,
            )
            label = _FixedScreenTextItem(
                self._format_tick(value),
                color=QColor("#4A5560"),
                offset_y=5.0,
            )
            scene.addItem(label)
            label.set_anchor(
                x,
                plot_top + plot_height,
                horizontal="center",
                vertical="top",
            )

        depth_title = _FixedScreenTextItem(
            f"Depth ({self._depth_unit})",
            color=QColor("#374151"),
            offset_x=-46.0,
        )
        scene.addItem(depth_title)
        depth_title.setRotation(-90.0)
        depth_title.set_anchor(
            plot_left,
            plot_top + plot_height / 2.0,
            horizontal="center",
            vertical="center",
        )

        x_title_text = self._mnemonic
        if self._unit:
            x_title_text += f" ({self._unit})"
        x_title = _FixedScreenTextItem(
            x_title_text,
            color=self._curve_color,
            offset_y=34.0,
        )
        scene.addItem(x_title)
        x_title.set_anchor(
            plot_left + plot_width / 2.0,
            plot_top + plot_height,
            horizontal="center",
            vertical="top",
        )

        path = QPainterPath()
        pen_down = False
        segments = 0
        for depth, value in zip(self._depths, self._values):
            if (
                value is None
                or not isfinite(value)
                or (
                    self._scale_type is ScaleType.LOGARITHMIC
                    and value <= 0.0
                )
            ):
                pen_down = False
                continue

            x = self._value_to_scene_x(value)
            y = self.scene_y_for_depth(depth)
            if y is None:
                continue
            if not pen_down:
                path.moveTo(x, y)
                pen_down = True
                segments += 1
            else:
                path.lineTo(x, y)

        self.segment_count = segments
        curve_item = scene.addPath(path, curve_pen)
        curve_item.setZValue(2.0)

        if self._show_qc_markers:
            # Unresolved LAS gaps: connect the two valid curve endpoints with
            # one thick red line. Automatically interpolated gaps are never
            # passed to this view and therefore remain visually silent.
            gap_pen = QPen(QColor("#D32F2F"))
            gap_pen.setWidthF(5.0)
            gap_pen.setCosmetic(True)
            gap_endpoints: list[tuple[QPointF, QPointF]] = []

            for gap in self._gaps:
                previous = self._scene_point_on_curve_at_depth(
                    gap.previous_depth
                )
                following = self._scene_point_on_curve_at_depth(
                    gap.next_depth
                )
                if previous is None or following is None:
                    continue

                connector = scene.addLine(
                    previous.x(),
                    previous.y(),
                    following.x(),
                    following.y(),
                    gap_pen,
                )
                connector.setZValue(8.0)
                connector.setToolTip(
                    "Gap requiring review\n"
                    f"Depth: {self._format_tick(gap.missing_start_depth)}"
                    f"–{self._format_tick(gap.missing_stop_depth)} "
                    f"{self._depth_unit}\n"
                    f"Missing LAS samples: {gap.missing_sample_count}"
                )
                gap_endpoints.append(
                    (QPointF(previous), QPointF(following))
                )
                self.gap_connector_count += 1

            self.gap_connector_endpoints = tuple(gap_endpoints)

            # Scale/offset transitions stay compact and fixed in screen size.
            # Circle = offset change. Diamond = scale change. No third symbol
            # is introduced; if malformed legacy data reports both, scale
            # change takes visual priority and the tooltip still reports both.
            marker_pen = QPen(QColor("#7A4E00"))
            marker_pen.setWidthF(1.2)
            marker_brush = QBrush(QColor("#F59E0B"))
            marker_positions: list[QPointF] = []
            marker_items: list[_NavigableTransitionMarker] = []

            for transition in self._transitions:
                point = self._scene_point_on_curve_at_depth(
                    transition.depth
                )
                if point is None:
                    continue

                tooltip = (
                    f"{transition.segment_name}\n"
                    f"Depth: {self._format_tick(transition.depth)} "
                    f"{self._depth_unit}\n"
                    f"{transition.label}\n"
                    "Double-click to navigate to this zone."
                )

                kind = "scale" if transition.scale_changed else "offset"
                marker = _NavigableTransitionMarker(
                    kind=kind,
                    pen=marker_pen,
                    brush=marker_brush,
                    callback=(
                        lambda item=transition: self.navigationRequested.emit(
                            self._curve_id,
                            item.segment_id,
                            float(item.depth),
                        )
                    ),
                )
                scene.addItem(marker)

                if kind == "scale":
                    self.scale_marker_count += 1
                else:
                    self.offset_marker_count += 1

                marker.setPos(point)
                marker.setToolTip(tooltip)

                marker_positions.append(QPointF(point))
                marker_items.append(marker)
                self.transition_marker_count += 1

            self.transition_marker_positions = tuple(marker_positions)
            self.transition_marker_items = tuple(marker_items)

    def _is_plottable_value(self, value: float | None) -> bool:
        if value is None or not isfinite(value):
            return False
        return not (
            self._scale_type is ScaleType.LOGARITHMIC
            and float(value) <= 0.0
        )

    def _scene_point_on_curve_at_depth(
        self,
        depth: float,
    ) -> QPointF | None:
        """Return one point on the currently rendered final LAS curve."""
        if not self._depths:
            return None

        target = float(depth)
        index = bisect_left(self._depths, target)
        tolerance = max(1e-10, abs(target) * 1e-12)

        if index < len(self._depths):
            sample_depth = self._depths[index]
            if abs(sample_depth - target) <= tolerance:
                value = self._values[index]
                if not self._is_plottable_value(value):
                    return None
                y = self.scene_y_for_depth(sample_depth)
                if y is None:
                    return None
                return QPointF(
                    self._value_to_scene_x(float(value)),
                    y,
                )

        if index == 0 or index >= len(self._depths):
            return None

        upper_depth = self._depths[index - 1]
        lower_depth = self._depths[index]
        upper_value = self._values[index - 1]
        lower_value = self._values[index]

        # Never bridge a real NULL break to place a transition marker.
        if not (
            self._is_plottable_value(upper_value)
            and self._is_plottable_value(lower_value)
        ):
            return None

        span = lower_depth - upper_depth
        if abs(span) <= 1e-15:
            return None

        ratio = (target - upper_depth) / span
        x0 = self._value_to_scene_x(float(upper_value))
        x1 = self._value_to_scene_x(float(lower_value))
        y = self.scene_y_for_depth(target)
        if y is None:
            return None
        return QPointF(x0 + ratio * (x1 - x0), y)

    def _value_to_scene_x(self, value: float) -> float:
        number = float(value)
        if self._scale_type is ScaleType.LOGARITHMIC:
            if number <= 0.0:
                raise ValueError(
                    "Logarithmic curve preview requires positive values."
                )
            ratio = (
                log10(number) - log10(self._value_min)
            ) / (
                log10(self._value_max) - log10(self._value_min)
            )
        else:
            ratio = (number - self._value_min) / (
                self._value_max - self._value_min
            )
        return self.LEFT_MARGIN + ratio * self.BASE_PLOT_WIDTH


class LasCurvePreviewDialog(QDialog):
    """Visual validation of the exact sampled curve matrix prepared for LAS."""

    navigationRequested = Signal(str, str, float)
    stepChanged = Signal(float)

    def __init__(
        self,
        service: ProjectService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.current_plan: LasExportPlan | None = None
        self.current_qc_preview: LasExportPreview | None = None
        self._curve_infos = ()
        self._initializing_step = False

        self.setWindowTitle("LAS Curve Preview")
        self.setWindowIcon(app_icon("chart-line"))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1080, 780)
        self.setMinimumSize(640, 600)

        controls = QGridLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(6)
        self.controls_layout = controls

        controls.addWidget(QLabel("Curve"), 0, 0)
        self.curve_combo = QComboBox()
        self.curve_combo.setMinimumWidth(100)
        self.curve_combo.setToolTip(
            "Select the LAS curve. The full name and unit are available "
            "in the item tooltip."
        )
        controls.addWidget(self.curve_combo, 0, 1)

        controls.addWidget(QLabel("STEP"), 0, 2)
        self.step_spin = CompactDoubleSpinBox()
        self.step_spin.setDecimals(12)
        self.step_spin.setRange(0.000000000001, 1_000_000_000.0)
        self.step_spin.setKeyboardTracking(False)
        self.step_spin.setAccelerated(True)
        controls.addWidget(self.step_spin, 0, 3)

        self.refresh_button = QPushButton(
            app_icon("rotate"),
            "Refresh",
        )
        self.refresh_button.setToolTip(
            "Rebuild the final LAS curve matrix from the current project."
        )
        controls.addWidget(self.refresh_button, 0, 4)

        self.show_qc_markers_check = QCheckBox("Show QC markers")
        self.show_qc_markers_check.setChecked(True)
        self.show_qc_markers_check.setToolTip(
            "Show scale changes, offset changes and unresolved LAS gaps."
        )
        controls.addWidget(self.show_qc_markers_check, 1, 0, 1, 2)
        # Compatibility alias for older external GUI tests/plugins.
        self.show_transitions_check = self.show_qc_markers_check

        self.compress_button = QPushButton(
            app_icon("down-left-and-up-right-to-center"),
            "Compress depth",
        )
        self.stretch_button = QPushButton(
            app_icon("up-right-and-down-left-from-center"),
            "Stretch depth",
        )
        self.fit_button = QPushButton(
            app_icon("expand"),
            "Fit full curve",
        )
        controls.addWidget(self.compress_button, 1, 2)
        controls.addWidget(self.stretch_button, 1, 3)
        controls.addWidget(self.fit_button, 1, 4)

        controls.setColumnStretch(1, 1)
        controls.setColumnStretch(5, 1)

        summary_group = QGroupBox("LAS reconstruction")
        summary_layout = QGridLayout(summary_group)
        self.curve_value = self._summary_item(summary_layout, 0, "Curve")
        self.start_value = self._summary_item(summary_layout, 1, "START")
        self.stop_value = self._summary_item(summary_layout, 2, "STOP")
        self.step_value = self._summary_item(summary_layout, 3, "STEP")
        self.samples_value = self._summary_item(summary_layout, 4, "Samples")

        self.info_label = QLabel(
            "This plot is built from the same sampled values that are prepared "
            "for LAS export. Missing values are shown as breaks in the curve. "
            "Use Show QC markers to inspect scale/offset changes and unresolved gaps."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #6B7280;")

        self.qc_legend_label = QLabel(
            '<span style="color:#F59E0B;font-size:15px;">●</span> '
            'Offset change&nbsp;&nbsp;&nbsp;'
            '<span style="color:#F59E0B;font-size:15px;">◆</span> '
            'Scale change&nbsp;&nbsp;&nbsp;'
            '<span style="color:#D32F2F;font-size:18px;">━</span> '
            'Gap requiring review'
        )
        self.qc_legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.qc_legend_label.setStyleSheet("color: #4A5560;")
        self.qc_legend_label.setToolTip(
            "Only unresolved LAS gaps are shown. Small automatically "
            "interpolated gaps are intentionally omitted."
        )

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #B42318;")

        self.curve_view = LasCurveGraphicsView()

        root = QVBoxLayout(self)
        root.addLayout(controls)
        root.addWidget(summary_group)
        root.addWidget(self.info_label)
        root.addWidget(self.qc_legend_label)
        root.addWidget(self.error_label)
        root.addWidget(self.curve_view, 1)

        self.curve_combo.currentIndexChanged.connect(
            self._render_selected_curve
        )
        self.step_spin.valueChanged.connect(self._step_changed)
        self.refresh_button.clicked.connect(self.refresh_from_project)
        self.compress_button.clicked.connect(self._compress_depth)
        self.stretch_button.clicked.connect(self._stretch_depth)
        self.fit_button.clicked.connect(self.curve_view.fit_full_curve)
        self.curve_view.navigationRequested.connect(
            self.navigationRequested.emit
        )
        self.show_qc_markers_check.toggled.connect(
            self._set_qc_markers_visible
        )

        self._reload_curve_infos()
        self._set_initial_step()
        self.refresh_from_project()

    @staticmethod
    def _summary_item(
        layout: QGridLayout,
        column: int,
        title: str,
    ) -> QLabel:
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label = QLabel("—")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(title_label, 0, column)
        layout.addWidget(value_label, 1, column)
        return value_label

    def _set_qc_markers_visible(self, visible: bool) -> None:
        self.qc_legend_label.setVisible(bool(visible))
        self.curve_view.set_qc_markers_visible(bool(visible))

    def _reload_curve_infos(self) -> None:
        self._curve_infos = tuple(
            info
            for info in self.service.get_las_export_curve_infos()
            if info.exportable
        )

    @property
    def curve_ids(self) -> tuple[str, ...]:
        return tuple(info.curve_id for info in self._curve_infos)

    def _set_initial_step(self) -> None:
        self._initializing_step = True
        try:
            step = self.service.get_last_las_step()
            if step is None:
                units = {
                    info.depth_unit
                    for info in self._curve_infos
                    if info.depth_unit is not None
                }
                resolution = self.service.get_first_image_vertical_resolution(
                    next(iter(units)) if len(units) == 1 else None
                )
                step = (
                    float(f"{resolution:.3g}")
                    if resolution is not None
                    else 0.2
                )
            self.step_spin.setValue(step)
        finally:
            self._initializing_step = False

    def sync_step_from_project(self) -> None:
        """Adopt a STEP changed in Quality Control or Export LAS."""
        step = self.service.get_last_las_step()
        if step is None or step == self.step_spin.value():
            return
        previous = self.step_spin.blockSignals(True)
        self.step_spin.setValue(step)
        self.step_spin.blockSignals(previous)
        self.refresh_from_project()

    def refresh_from_project(self) -> None:
        """Rebuild the exact final LAS sample matrix and redraw the selection."""
        selected_curve_id = self.curve_combo.currentData()
        self._reload_curve_infos()
        self.error_label.clear()
        self.current_plan = None
        self.current_qc_preview = None

        if not self.curve_ids:
            self.curve_combo.clear()
            self.curve_view.clear_curve(
                "No digitized curves are ready for LAS preview."
            )
            self._clear_summary()
            return

        try:
            qc_preview = self.service.preview_las_export(
                step_depth=self.step_spin.value(),
                curve_ids=self.curve_ids,
            )
            plan = self.service.prepare_las_export(
                step_depth=self.step_spin.value(),
                curve_ids=self.curve_ids,
            )
        except (LasExportError, RuntimeError, ValueError) as exc:
            self.error_label.setText(str(exc))
            self.curve_view.clear_curve("LAS curve preview is unavailable.")
            self._clear_summary()
            return

        self.current_plan = plan
        self.current_qc_preview = qc_preview
        depth_unit = self._depth_unit_text(plan.depth_unit)
        self.step_spin.setSuffix(f" {depth_unit}")

        previous = self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        selected_index = 0
        for index, column in enumerate(plan.columns):
            details = column.mnemonic
            if column.description:
                details += f" — {column.description}"
            if column.unit:
                details += f" [{column.unit}]"

            # Keep the selector compact when the preview window is narrow.
            # Full descriptive information remains available as a tooltip.
            self.curve_combo.addItem(column.mnemonic, column.curve_id)
            self.curve_combo.setItemData(
                index,
                details,
                Qt.ItemDataRole.ToolTipRole,
            )
            curve = self.service.get_curve(column.curve_id)
            item_color = QColor(curve.display_color)
            if item_color.isValid():
                self.curve_combo.setItemData(
                    index,
                    item_color,
                    Qt.ItemDataRole.ForegroundRole,
                )
            if column.curve_id == selected_curve_id:
                selected_index = index
        self.curve_combo.setCurrentIndex(selected_index)
        self.curve_combo.blockSignals(previous)

        self._render_selected_curve()

    def _step_changed(self, value: float) -> None:
        if self._initializing_step:
            return
        try:
            self.service.set_last_las_step(float(value))
        except (LasExportError, RuntimeError, ValueError) as exc:
            self.error_label.setText(str(exc))
            return
        self.stepChanged.emit(float(value))
        self.refresh_from_project()

    def _render_selected_curve(self, *_args) -> None:
        plan = self.current_plan
        curve_id = self.curve_combo.currentData()
        if plan is None or not curve_id:
            return

        column = next(
            (
                item
                for item in plan.columns
                if item.curve_id == curve_id
            ),
            None,
        )
        if column is None:
            return

        tooltip = self.curve_combo.itemData(
            self.curve_combo.currentIndex(),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.curve_combo.setToolTip(
            str(tooltip) if tooltip else column.mnemonic
        )

        values = tuple(plan.sampled.values_for(curve_id))
        depths = tuple(float(depth) for depth in plan.sampled.depths)
        depth_unit = self._depth_unit_text(plan.depth_unit)
        curve = self.service.get_curve(curve_id)

        transitions = self.service.get_las_curve_transform_transitions(
            curve_id
        )
        gaps = (
            tuple(
                gap
                for gap in self.current_qc_preview.internal_gaps
                if gap.curve_id == curve_id
            )
            if self.current_qc_preview is not None
            else ()
        )
        self.curve_view.set_curve(
            depths=depths,
            values=values,
            mnemonic=column.mnemonic,
            unit=column.unit,
            depth_unit=depth_unit,
            color=curve.display_color,
            scale_type=curve.scale_type,
            transitions=transitions,
            gaps=gaps,
            curve_id=curve_id,
        )

        curve_text = column.mnemonic
        if column.unit:
            curve_text += f" ({column.unit})"
        self.curve_value.setText(curve_text)
        self.curve_value.setStyleSheet(
            f"font-weight: 600; color: {curve.display_color};"
        )
        self.start_value.setText(f"{plan.start_depth:g} {depth_unit}")
        self.stop_value.setText(f"{plan.stop_depth:g} {depth_unit}")
        self.step_value.setText(f"{plan.step_depth:g} {depth_unit}")
        self.samples_value.setText(f"{plan.row_count:,}")

    def _compress_depth(self) -> None:
        self.curve_view.compress_depth()

    def _stretch_depth(self) -> None:
        self.curve_view.stretch_depth()

    def _clear_summary(self) -> None:
        for label in (
            self.curve_value,
            self.start_value,
            self.stop_value,
            self.step_value,
            self.samples_value,
        ):
            label.setText("—")
        self.curve_value.setStyleSheet("font-weight: 600;")

    @staticmethod
    def _depth_unit_text(unit: DepthUnit) -> str:
        return "m" if unit is DepthUnit.METERS else "ft"
