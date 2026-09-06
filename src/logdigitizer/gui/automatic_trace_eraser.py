"""Transient range preview for erasing saved automatic trace samples."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from .overlays import OverlayManager


class AutomaticTraceEraserOverlay:
    """Render the inclusive image-Y interval selected by an eraser drag."""

    LAYER_NAME = "automatic_trace_eraser"

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
    ) -> None:
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._item: QGraphicsRectItem | None = None
        self._range_y_px: tuple[float, float] | None = None

    @property
    def range_y_px(self) -> tuple[float, float] | None:
        return self._range_y_px

    @property
    def item(self) -> QGraphicsRectItem | None:
        return self._item

    def clear(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._item = None
        self._range_y_px = None

    def set_range(
        self,
        start_y_px: float,
        end_y_px: float,
        *,
        image_width_px: float,
    ) -> None:
        """Show a translucent red band across the complete image width."""
        minimum_y, maximum_y = sorted(
            (float(start_y_px), float(end_y_px))
        )
        width = max(1.0, float(image_width_px))
        top_left = self._image_to_scene(QPointF(0.0, minimum_y))
        bottom_right = self._image_to_scene(QPointF(width, maximum_y))

        self.clear()
        item = QGraphicsRectItem(QRectF(top_left, bottom_right).normalized())
        pen = QPen(QColor(220, 35, 35, 220), 1.5)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setBrush(QBrush(QColor(230, 40, 40, 55)))
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
            False,
        )
        item.setZValue(60.0)
        self._overlays.add_scene_item(self.LAYER_NAME, item)
        self._item = item
        self._range_y_px = (minimum_y, maximum_y)
