"""Debounced live enhancement for the main ImageViewer."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QTimer

from logdigitizer.processing.enhancement import EnhancementSettings, enhance_image


class LiveEnhancementController(QObject):
    """Apply enhancement to a fixed rectified base without modifying that base."""

    DEBOUNCE_MS = 140

    def __init__(self, viewer, parent=None) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self._base_image: np.ndarray | None = None
        self._settings = EnhancementSettings()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._apply_now)

    @property
    def settings(self) -> EnhancementSettings:
        return self._settings

    @property
    def has_base_image(self) -> bool:
        return self._base_image is not None

    def set_base_image(self, image: np.ndarray, *, reset_settings: bool = False) -> None:
        self._base_image = np.array(image, copy=True)
        if reset_settings:
            self._settings = EnhancementSettings()
        self._timer.stop()
        self._apply_now()

    def clear_base_image(self) -> None:
        self._timer.stop()
        self._base_image = None
        self._settings = EnhancementSettings()

    def set_settings(self, settings: EnhancementSettings) -> None:
        self._settings = settings
        if self._base_image is not None:
            self._timer.start()

    def reset(self) -> None:
        self.set_settings(EnhancementSettings())

    def enhanced_image(self) -> np.ndarray:
        if self._base_image is None:
            raise ValueError("No rectified base image is available.")
        return enhance_image(self._base_image, self._settings)

    def _apply_now(self) -> None:
        if self._base_image is None:
            return
        self._viewer.set_display_image_array(
            enhance_image(self._base_image, self._settings),
            preserve_view=True,
        )
