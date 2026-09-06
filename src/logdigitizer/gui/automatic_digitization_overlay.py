"""Transient raw-trace overlay for semi-automatic digitization (step 8.4C)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
)

from logdigitizer.domain.curve_display import DEFAULT_CURVE_DISPLAY_COLOR
from logdigitizer.domain.digitization import TracePoint

from .overlays import OverlayManager


class AutomaticRawPathItem(QGraphicsPathItem):
    """One visible contiguous run of accepted raw samples."""

    LINE_WIDTH_PX = 2.0

    def __init__(self, path: QPainterPath, *, color: str) -> None:
        super().__init__(path)
        pen = QPen(QColor(color), self.LINE_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(32.0)


class AutomaticLossMarkerItem(QGraphicsEllipseItem):
    """Small visual cue showing where confidence-based tracking stopped."""

    RADIUS_PX = 6.0

    def __init__(self, *, color: str) -> None:
        radius = self.RADIUS_PX
        super().__init__(-radius, -radius, 2.0 * radius, 2.0 * radius)
        pen = QPen(QColor(color), 1.8)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(33.0)


class AutomaticDigitizationOverlay:
    """Render accepted raw samples without visually inventing gap bridges."""

    LAYER_NAME = "automatic_digitization_draft"
    CONTIGUOUS_MAX_DY_PX = 1.01

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
    ) -> None:
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._display_color = DEFAULT_CURVE_DISPLAY_COLOR
        self._samples: tuple[TracePoint, ...] = ()
        self._path_items: list[AutomaticRawPathItem] = []
        self._loss_marker: AutomaticLossMarkerItem | None = None

    @property
    def raw_samples_px(self) -> tuple[TracePoint, ...]:
        return self._samples

    @property
    def path_items(self) -> tuple[AutomaticRawPathItem, ...]:
        return tuple(self._path_items)

    @property
    def loss_marker(self) -> AutomaticLossMarkerItem | None:
        return self._loss_marker

    def set_display_color(self, color: str) -> None:
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            raise ValueError(f"Invalid automatic trace display color: {color!r}")
        self._display_color = qcolor.name().upper()
        if self._samples:
            self.set_samples(self._samples)

    def set_samples(
        self,
        samples: Sequence[TracePoint],
        *,
        lost_y_px: float | None = None,
    ) -> None:
        """Compatibility wrapper for one raw trace."""
        self.set_sample_groups(
            (tuple(samples),),
            current_samples=tuple(samples),
            lost_y_px=lost_y_px,
        )

    def set_sample_groups(
        self,
        groups: Sequence[Sequence[TracePoint]],
        *,
        current_samples: Sequence[TracePoint] = (),
        lost_y_px: float | None = None,
    ) -> None:
        """Render persisted raw-only sections plus an optional current draft.

        Each group is kept visually independent. Missing raster rows inside one
        group are also split, so Stop/Click/Resume gaps are never bridged.
        """
        self._samples = tuple(current_samples)
        self._overlays.clear_layer(self.LAYER_NAME)
        self._path_items.clear()
        self._loss_marker = None

        for group in groups:
            samples = tuple(group)
            for run in _split_contiguous_runs(samples):
                if len(run) < 2:
                    continue
                path = QPainterPath(
                    self._image_to_scene(
                        QPointF(run[0].x_px, run[0].y_px)
                    )
                )
                for point in run[1:]:
                    scene_point = self._image_to_scene(
                        QPointF(point.x_px, point.y_px)
                    )
                    path.lineTo(scene_point)
                item = AutomaticRawPathItem(
                    path,
                    color=self._display_color,
                )
                self._overlays.add_item(self.LAYER_NAME, item)
                self._path_items.append(item)

        current = tuple(current_samples)
        if lost_y_px is not None and current:
            marker = AutomaticLossMarkerItem(color=self._display_color)
            # The tracker knows the Y where confidence failed but not a reliable
            # X there; anchor the cue to the last accepted current-draft X.
            scene_point = self._image_to_scene(
                QPointF(current[-1].x_px, float(lost_y_px))
            )
            marker.setPos(scene_point)
            self._overlays.add_item(self.LAYER_NAME, marker)
            self._loss_marker = marker

    def clear(self) -> None:
        self._samples = ()
        self._path_items.clear()
        self._loss_marker = None
        self._overlays.clear_layer(self.LAYER_NAME)


def _split_contiguous_runs(
    samples: Sequence[TracePoint],
) -> tuple[tuple[TracePoint, ...], ...]:
    if not samples:
        return ()

    runs: list[list[TracePoint]] = [[samples[0]]]
    for point in samples[1:]:
        previous = runs[-1][-1]
        if point.y_px - previous.y_px <= AutomaticDigitizationOverlay.CONTIGUOUS_MAX_DY_PX:
            runs[-1].append(point)
        else:
            runs.append([point])
    return tuple(tuple(run) for run in runs)
