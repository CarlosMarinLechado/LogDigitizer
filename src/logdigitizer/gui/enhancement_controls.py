"""Reusable GUI controls for non-destructive image enhancement."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSlider,
    QToolButton,
    QWidget,
)

from logdigitizer.processing.enhancement import EnhancementSettings

from .icons import app_icon


class EnhancementControls(QWidget):
    """Simple black, midtone and white level controls without image logic."""

    settingsChanged = Signal(object)
    resetRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False

        self.black_slider = QSlider(Qt.Orientation.Horizontal)
        self.black_slider.setRange(0, 254)
        self.black_slider.setValue(0)
        self.black_slider.setToolTip(
            "Black point. Raise it to make dark curve pixels reach black sooner."
        )
        self.black_value = QLabel("0")
        self.black_value.setMinimumWidth(54)

        self.midtones_slider = QSlider(Qt.Orientation.Horizontal)
        self.midtones_slider.setRange(10, 300)
        self.midtones_slider.setValue(100)
        self.midtones_slider.setToolTip(
            "Midtones (gamma). 1.00 is neutral; values below 1.00 brighten "
            "midtones and values above 1.00 darken them."
        )
        self.midtones_value = QLabel("1.00")
        self.midtones_value.setMinimumWidth(54)

        self.white_slider = QSlider(Qt.Orientation.Horizontal)
        self.white_slider.setRange(1, 255)
        self.white_slider.setValue(255)
        self.white_slider.setToolTip(
            "White point. Lower it to push light paper and background tones "
            "towards white."
        )
        self.white_value = QLabel("255")
        self.white_value.setMinimumWidth(54)

        self.reset_button = QToolButton()
        self.reset_button.setText("Reset")
        self.reset_button.setAccessibleName("Reset")
        self.reset_button.setToolTip("Reset image enhancement")
        self.reset_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.reset_button.setIcon(app_icon("rotate-left"))
        self.reset_button.setIconSize(QSize(18, 18))
        self.reset_button.setFixedSize(30, 30)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Black"), 0, 0)
        layout.addWidget(self.black_slider, 0, 1)
        layout.addWidget(self.black_value, 0, 2)
        layout.addWidget(QLabel("Midtones"), 1, 0)
        layout.addWidget(self.midtones_slider, 1, 1)
        layout.addWidget(self.midtones_value, 1, 2)
        layout.addWidget(QLabel("White"), 2, 0)
        layout.addWidget(self.white_slider, 2, 1)
        layout.addWidget(self.white_value, 2, 2)
        layout.addWidget(
            self.reset_button,
            3,
            2,
            alignment=Qt.AlignmentFlag.AlignRight,
        )

        self.black_slider.valueChanged.connect(self._controls_changed)
        self.midtones_slider.valueChanged.connect(self._controls_changed)
        self.white_slider.valueChanged.connect(self._controls_changed)
        self.reset_button.clicked.connect(self.reset)

        self._sync_level_ranges()
        self._update_labels()

    @property
    def settings(self) -> EnhancementSettings:
        """Return current GUI values as scientific enhancement settings."""
        return EnhancementSettings(
            black_point=self.black_slider.value(),
            midtones=self.midtones_slider.value() / 100.0,
            white_point=self.white_slider.value(),
        )

    def set_settings(self, settings: EnhancementSettings) -> None:
        """Update controls without re-emitting settingsChanged."""
        self._updating = True
        try:
            # Temporarily restore full ranges so any valid pair can be loaded.
            self.black_slider.setRange(0, 254)
            self.white_slider.setRange(1, 255)

            self.black_slider.setValue(int(settings.black_point))
            self.midtones_slider.setValue(
                int(round(settings.midtones * 100.0))
            )
            self.white_slider.setValue(int(settings.white_point))
            self._sync_level_ranges()
            self._update_labels()
        finally:
            self._updating = False

    def reset(self) -> None:
        """Restore neutral visible enhancement settings."""
        self.set_settings(EnhancementSettings())
        self.settingsChanged.emit(self.settings)
        self.resetRequested.emit()

    def _controls_changed(self, *args) -> None:
        del args
        self._sync_level_ranges()
        self._update_labels()
        if not self._updating:
            self.settingsChanged.emit(self.settings)

    def _sync_level_ranges(self) -> None:
        """Prevent the black and white points from crossing."""
        self.black_slider.setMaximum(self.white_slider.value() - 1)
        self.white_slider.setMinimum(self.black_slider.value() + 1)

    def _update_labels(self) -> None:
        self.black_value.setText(str(self.black_slider.value()))
        self.midtones_value.setText(
            f"{self.midtones_slider.value() / 100.0:.2f}"
        )
        self.white_value.setText(str(self.white_slider.value()))
