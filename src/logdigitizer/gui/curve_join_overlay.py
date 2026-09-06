"""Non-interactive guides for calibrated cross-image overlaps."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from .overlays import OverlayManager


@dataclass(frozen=True, slots=True)
class ImageOverlapGuideDefinition:
    """One calibrated overlap boundary in active-image coordinates."""

    y_px: float
    text: str
    label_above: bool
    tooltip: str


class ImageOverlapGuideLabelItem(QGraphicsItem):
    """Screen-size-stable red instruction positioned around one guide."""

    H_PADDING = 6.0
    V_PADDING = 3.0
    LABEL_GAP = 5.0
    LEFT_OFFSET = 8.0
    TEXT_COLOR = QColor(220, 0, 0)
    BACKGROUND_COLOR = QColor(255, 255, 255, 220)

    def __init__(
        self,
        text: str,
        *,
        label_above: bool,
    ) -> None:
        super().__init__()
        self.text = str(text)
        self.label_above = bool(label_above)
        self._font = QFont()
        self._font.setPointSizeF(9.0)
        self._font.setBold(True)

        metrics = QFontMetricsF(self._font)
        text_rect = metrics.boundingRect(self.text)
        width = text_rect.width() + 2.0 * self.H_PADDING
        height = text_rect.height() + 2.0 * self.V_PADDING
        top = (
            -self.LABEL_GAP - height
            if self.label_above
            else self.LABEL_GAP
        )
        self._label_rect = QRectF(
            self.LEFT_OFFSET,
            top,
            width,
            height,
        )

        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(26.0)

    @property
    def label_rect(self) -> QRectF:
        return QRectF(self._label_rect)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt API
        return self._label_rect.adjusted(-1.0, -1.0, 1.0, 1.0)

    def paint(  # noqa: N802 - Qt API
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.BACKGROUND_COLOR)
        painter.drawRoundedRect(self._label_rect, 3.0, 3.0)
        painter.setFont(self._font)
        painter.setPen(self.TEXT_COLOR)
        painter.drawText(
            self._label_rect.adjusted(
                self.H_PADDING,
                self.V_PADDING,
                -self.H_PADDING,
                -self.V_PADDING,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text,
        )
        painter.restore()


class ImageOverlapGuideOverlay:
    """Draw full-width red dashed overlap guides and instructions."""

    LAYER_NAME = "image_overlap_guides"
    LINE_COLOR = QColor(220, 0, 0)

    def __init__(
        self,
        overlays: OverlayManager,
        image_to_scene: Callable[[QPointF], QPointF],
    ) -> None:
        self._overlays = overlays
        self._image_to_scene = image_to_scene
        self._line_items: list[QGraphicsLineItem] = []
        self._label_items: list[ImageOverlapGuideLabelItem] = []

    @property
    def line_items(self) -> tuple[QGraphicsLineItem, ...]:
        return tuple(self._line_items)

    @property
    def label_items(self) -> tuple[ImageOverlapGuideLabelItem, ...]:
        return tuple(self._label_items)

    def clear(self) -> None:
        self._overlays.clear_layer(self.LAYER_NAME)
        self._line_items.clear()
        self._label_items.clear()

    def set_guides(
        self,
        definitions: Sequence[ImageOverlapGuideDefinition],
        *,
        image_width: float,
    ) -> None:
        self.clear()
        width = max(0.0, float(image_width))
        if width <= 0.0:
            return

        for definition in definitions:
            start = self._image_to_scene(QPointF(0.0, definition.y_px))
            end = self._image_to_scene(QPointF(width, definition.y_px))
            item = QGraphicsLineItem(
                start.x(),
                start.y(),
                end.x(),
                end.y(),
            )
            pen = QPen(self.LINE_COLOR, 2.0, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setToolTip(definition.tooltip)
            item.setZValue(25.0)
            self._overlays.add_item(self.LAYER_NAME, item)
            self._line_items.append(item)

            label = ImageOverlapGuideLabelItem(
                definition.text,
                label_above=definition.label_above,
            )
            label.setPos(start)
            label.setToolTip(definition.tooltip)
            self._overlays.add_item(self.LAYER_NAME, label)
            self._label_items.append(label)
