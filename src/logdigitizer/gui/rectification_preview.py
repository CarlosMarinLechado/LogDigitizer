"""Zoomable, pannable non-destructive perspective preview."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QResizeEvent, QShowEvent, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .image_bridge import numpy_to_qimage


class PreviewImageViewer(QGraphicsView):
    """Small image-only viewer with zoom, pan, Fit and 1:1."""

    zoomChanged = Signal(float)
    ZOOM_STEP = 1.20
    MIN_ZOOM = 0.02
    MAX_ZOOM = 100.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._fit_on_first_show = True
        self._fit_mode = True

    @property
    def has_image(self) -> bool:
        return not self._pixmap_item.pixmap().isNull()

    @property
    def zoom_factor(self) -> float:
        return float(self.transform().m11())

    @property
    def pixmap(self) -> QPixmap:
        return self._pixmap_item.pixmap()

    def set_image(self, image: np.ndarray) -> None:
        pixmap = QPixmap.fromImage(numpy_to_qimage(image))
        self._pixmap_item.setPixmap(pixmap)
        self._pixmap_item.setPos(0.0, 0.0)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._fit_on_first_show = True
        self._fit_mode = True

    def fit_to_window(self) -> None:
        if not self.has_image:
            return
        self.resetTransform()
        self.fitInView(self._pixmap_item.boundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_mode = True
        self.zoomChanged.emit(self.zoom_factor)

    def actual_size(self) -> None:
        if not self.has_image:
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.centerOn(center)
        self._fit_mode = False
        self.zoomChanged.emit(self.zoom_factor)

    def zoom_in(self) -> None:
        self._zoom_at(self.viewport().rect().center(), self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self._zoom_at(self.viewport().rect().center(), 1.0 / self.ZOOM_STEP)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self.has_image or event.angleDelta().y() == 0:
            super().wheelEvent(event)
            return
        factor = self.ZOOM_STEP if event.angleDelta().y() > 0 else 1.0 / self.ZOOM_STEP
        self._zoom_at(event.position().toPoint(), factor)
        event.accept()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fit_on_first_show:
            self._fit_on_first_show = False
            QTimer.singleShot(0, self.fit_to_window)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode and self.has_image:
            self.fit_to_window()

    def _zoom_at(self, viewport_point: QPoint, factor: float) -> None:
        if not self.has_image:
            return
        current = self.zoom_factor
        proposed = current * factor
        if proposed < self.MIN_ZOOM:
            factor = self.MIN_ZOOM / current
        elif proposed > self.MAX_ZOOM:
            factor = self.MAX_ZOOM / current
        if abs(factor - 1.0) < 1.0e-12:
            return
        before = self.mapToScene(viewport_point)
        self.scale(factor, factor)
        after = self.mapToScene(viewport_point)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self._fit_mode = False
        self.zoomChanged.emit(self.zoom_factor)


class RectificationPreviewDialog(QDialog):
    """Display a rectified raster initially Fit to Window, with zoom and pan."""

    def __init__(
        self,
        image: np.ndarray,
        output_size: tuple[int, int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rectification Preview")
        self.resize(1000, 700)

        width, height = output_size
        self.info_label = QLabel(f"Preview only — output size: {width} × {height} px")
        self.zoom_label = QLabel("Zoom: —")

        self.zoom_out_button = QPushButton("−")
        self.zoom_in_button = QPushButton("+")
        self.fit_button = QPushButton("Fit")
        self.actual_button = QPushButton("1:1")

        controls = QHBoxLayout()
        controls.addWidget(self.zoom_out_button)
        controls.addWidget(self.zoom_in_button)
        controls.addWidget(self.fit_button)
        controls.addWidget(self.actual_button)
        controls.addSpacing(12)
        controls.addWidget(self.zoom_label)
        controls.addStretch(1)

        self.viewer = PreviewImageViewer(self)
        self.viewer.set_image(image)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addLayout(controls)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(buttons)

        self.zoom_out_button.clicked.connect(self.viewer.zoom_out)
        self.zoom_in_button.clicked.connect(self.viewer.zoom_in)
        self.fit_button.clicked.connect(self.viewer.fit_to_window)
        self.actual_button.clicked.connect(self.viewer.actual_size)
        self.viewer.zoomChanged.connect(self._update_zoom)

    @property
    def preview_pixmap(self) -> QPixmap:
        """Compatibility accessor retained for the existing Step 5 tests."""
        return self.viewer.pixmap

    def _update_zoom(self, factor: float) -> None:
        self.zoom_label.setText(f"Zoom: {factor * 100:.1f}%")
