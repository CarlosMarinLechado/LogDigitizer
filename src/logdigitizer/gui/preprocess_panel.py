"""Preprocessing panel: rectification followed by live Image Enhancement."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.processing.enhancement import EnhancementSettings

from .enhancement_controls import EnhancementControls
from .icons import (
    ICON_MUTED,
    ICON_SUCCESS,
    ICON_WARNING,
    app_icon,
    danger_icon,
)


RECTIFICATION_HELP_TEXT = (
    "Standard rectification uses four outer corner points.\n\n"
    "• Enable Define 4 corners\n"
    "• Click the four corners of the rectangular log area\n"
    "• Drag a corner to refine its position if needed\n"
    "• Preview the rectification before applying it\n"
    "• The complete scanned image is preserved\n\n"
    "Use Multipoint correction only when the lateral boundaries still need "
    "local straightening."
)

MULTIPOINT_HELP_TEXT = (
    "Use control pairs when the lateral boundaries remain curved or "
    "non-vertical after the standard four-corner rectification.\n\n"
    "• First define four valid outer corners\n"
    "• Enable Multipoint correction\n"
    "• Click Add control pair\n"
    "• Click the LEFT boundary first\n"
    "• Then click the corresponding RIGHT boundary at the same transverse level\n"
    "• Repeat at additional levels where the boundaries deviate\n"
    "• Use Remove last pair to remove the most recently added pair\n"
    "• Preview the result before applying the rectification\n\n"
    "The intermediate pairs provide additional references for straightening "
    "the lateral boundaries of the log."
)

ENHANCEMENT_HELP_TEXT = (
    "Image Enhancement is applied live to the main viewer while the rectified "
    "base image remains unchanged.\n\n"
    "• Black controls the black point\n"
    "• Midtones controls gamma / middle tones\n"
    "• White controls the white point\n"
    "• Use Reset to restore neutral values\n"
    "• Click Apply Enhancement to save the current enhanced working image"
)


def _help_button(action_text: str, tooltip: str) -> QToolButton:
    """Create one compact icon-only contextual help button."""
    button = QToolButton()
    button.setText(action_text)
    button.setAccessibleName(action_text)
    button.setToolTip(tooltip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIcon(app_icon("circle-info"))
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(30, 30)
    return button


def _action_button(
    icon_name: str,
    action_text: str,
    tooltip: str,
    *,
    danger: bool = False,
) -> QToolButton:
    """Create one compact secondary action button."""
    button = QToolButton()
    button.setText(action_text)
    button.setAccessibleName(action_text)
    button.setToolTip(tooltip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIcon(
        danger_icon(icon_name)
        if danger
        else app_icon(icon_name)
    )
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(32, 32)
    return button


def _set_status(label: QLabel, text: str, *, valid: bool | None) -> None:
    """Render one compact semantic status line."""
    label.setText(text)
    if valid is True:
        label.setStyleSheet(f"color: {ICON_SUCCESS};")
    elif valid is False:
        label.setStyleSheet(f"color: {ICON_WARNING};")
    else:
        label.setStyleSheet("")


def _set_pending_status(label: QLabel, text: str) -> None:
    """Render a neutral pending/informational state."""
    label.setText(text)
    label.setStyleSheet(f"color: {ICON_MUTED};")


class PreprocessPanel(QWidget):
    """Compact rectification and enhancement controls with contextual help."""

    rectificationModeToggled = Signal(bool)
    multipointModeToggled = Signal(bool)
    addControlPairRequested = Signal()
    removeControlPairRequested = Signal()
    clearRequested = Signal()
    previewRequested = Signal()
    applyRequested = Signal()

    enhancementSettingsChanged = Signal(object)
    enhancementApplyRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # ------------------------------------------------------------------
        # Rectification
        # ------------------------------------------------------------------
        self.mode_check = QCheckBox("Define 4 corners")
        self.mode_check.setToolTip(
            "Enable four-corner rectification capture on the image."
        )

        self.rectification_help_button = _help_button(
            "Rectification help",
            "Help for four-corner rectification.",
        )

        rectification_header_row = QHBoxLayout()
        rectification_header_row.setContentsMargins(0, 0, 0, 0)
        rectification_header_row.addWidget(self.mode_check)
        rectification_header_row.addStretch(1)
        rectification_header_row.addWidget(self.rectification_help_button)

        # Compatibility-only labels retained for existing controllers/tests.
        self.instructions = QLabel(
            "Mark the four corners of the rectangular log area and drag them "
            "to readjust. The complete scanned image will be preserved.",
            self,
        )
        self.instructions.hide()

        self.points_label = QLabel("Points: 0 / 4", self)
        self.points_label.hide()

        self.geometry_label = QLabel("Geometry: —", self)
        self.geometry_label.hide()

        self.rectification_status_label = QLabel("⚠ Add 4 corner points")
        self.rectification_status_label.setWordWrap(True)

        self.multipoint_check = QCheckBox("Multipoint correction")
        self.multipoint_check.setToolTip(
            "Enable advanced local straightening after four valid outer corners."
        )
        self.multipoint_help_button = _help_button(
            "Multipoint correction help",
            "How to use multipoint correction",
        )

        multipoint_row = QHBoxLayout()
        multipoint_row.setContentsMargins(0, 0, 0, 0)
        multipoint_row.addWidget(self.multipoint_check)
        multipoint_row.addStretch(1)
        multipoint_row.addWidget(self.multipoint_help_button)

        self.control_pairs_label = QLabel("Control pairs: 0")
        self.control_pairs_label.setWordWrap(True)

        self.add_control_pair_button = QPushButton("Add pair")
        self.add_control_pair_button.setToolTip(
            "Capture one control pair: click the left boundary first, then the "
            "corresponding right boundary."
        )
        self.remove_control_pair_button = QPushButton("Remove pair")
        self.remove_control_pair_button.setToolTip(
            "Remove the most recently added control pair."
        )

        advanced_actions = QHBoxLayout()
        advanced_actions.setContentsMargins(0, 0, 0, 0)
        advanced_actions.addWidget(self.add_control_pair_button)
        advanced_actions.addWidget(self.remove_control_pair_button)

        self.advanced_widget = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(self.control_pairs_label)
        advanced_layout.addLayout(advanced_actions)
        self.advanced_widget.hide()

        # Compatibility/state-only labels.
        self.size_label = QLabel("Reference grid: —", self)
        self.size_label.hide()
        self.result_label = QLabel("Applied result: —", self)
        self.result_label.setWordWrap(True)
        self.result_label.hide()

        self.clear_button = _action_button(
            "trash",
            "Clear rectification",
            "Clear all rectification corner points and intermediate control pairs.",
            danger=True,
        )
        self.preview_button = _action_button(
            "eye",
            "Preview rectification",
            "Preview the current rectification without saving it.",
        )
        self.apply_button = QPushButton("Apply Rectification")
        self.apply_button.setToolTip(
            "Apply the current rectification to create the rectified working base."
        )

        secondary_actions = QHBoxLayout()
        secondary_actions.setContentsMargins(0, 0, 0, 0)
        secondary_actions.addWidget(self.clear_button)
        secondary_actions.addWidget(self.preview_button)
        secondary_actions.addStretch(1)

        self.geometry_group = QGroupBox("Rectification")
        geometry_layout = QVBoxLayout(self.geometry_group)
        geometry_layout.addLayout(rectification_header_row)
        geometry_layout.addWidget(self.rectification_status_label)
        geometry_layout.addLayout(multipoint_row)
        geometry_layout.addWidget(self.advanced_widget)
        geometry_layout.addLayout(secondary_actions)
        geometry_layout.addWidget(self.apply_button)

        # ------------------------------------------------------------------
        # Image Enhancement
        # ------------------------------------------------------------------
        self.enhancement_group = QGroupBox("Image Enhancement")
        enhancement_layout = QVBoxLayout(self.enhancement_group)

        self.enhancement_help_button = _help_button(
            "Image enhancement help",
            "Help for Image Enhancement.",
        )

        self.help_label = QLabel(
            "Changes are shown live in the main canvas. "
            "The rectified base image remains unchanged.",
            self,
        )
        self.help_label.hide()

        self.enhancement_controls = EnhancementControls(self)

        # Move the existing Reset tool button from the controls' internal grid
        # into the block header. The same button/signal behavior is preserved.
        controls_layout = self.enhancement_controls.layout()
        if controls_layout is not None:
            controls_layout.removeWidget(self.enhancement_controls.reset_button)

        enhancement_header_row = QHBoxLayout()
        enhancement_header_row.setContentsMargins(0, 0, 0, 0)
        enhancement_header_row.addStretch(1)
        enhancement_header_row.addWidget(self.enhancement_controls.reset_button)
        enhancement_header_row.addWidget(self.enhancement_help_button)

        for value_label in (
            self.enhancement_controls.black_value,
            self.enhancement_controls.midtones_value,
            self.enhancement_controls.white_value,
        ):
            value_label.setMinimumWidth(48)
            value_label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )

        self.live_label = QLabel(
            "Move Black, Midtones or White to adjust the image levels "
            "directly in the main viewer.",
            self,
        )
        self.live_label.hide()

        self.apply_enhancement_button = QPushButton("Apply Enhancement")
        self.apply_enhancement_button.setToolTip(
            "Save the current rectified + enhanced raster as the final working image."
        )

        # Compatibility-only path/state label used by existing tests.
        self.enhancement_result_label = QLabel(
            "Working image: not applied",
            self,
        )
        self.enhancement_result_label.setWordWrap(True)
        self.enhancement_result_label.hide()

        self.enhancement_status_label = QLabel(
            "No pending enhancement changes"
        )
        self.enhancement_status_label.setWordWrap(True)

        enhancement_layout.addLayout(enhancement_header_row)
        enhancement_layout.addWidget(self.enhancement_controls)
        enhancement_layout.addWidget(self.apply_enhancement_button)
        enhancement_layout.addWidget(self.enhancement_status_label)

        root = QVBoxLayout(self)
        root.addWidget(self.geometry_group)
        root.addWidget(self.enhancement_group)
        root.addStretch(1)

        # ------------------------------------------------------------------
        # Signals
        # ------------------------------------------------------------------
        self.mode_check.toggled.connect(
            self.rectificationModeToggled.emit
        )
        self.multipoint_check.toggled.connect(
            self._multipoint_mode_toggled
        )
        self.add_control_pair_button.clicked.connect(
            self.addControlPairRequested.emit
        )
        self.remove_control_pair_button.clicked.connect(
            self.removeControlPairRequested.emit
        )
        self.clear_button.clicked.connect(
            self.clearRequested.emit
        )
        self.preview_button.clicked.connect(
            self.previewRequested.emit
        )
        self.apply_button.clicked.connect(
            self.applyRequested.emit
        )
        self.enhancement_controls.settingsChanged.connect(
            self._enhancement_controls_changed
        )
        self.apply_enhancement_button.clicked.connect(
            self.enhancementApplyRequested.emit
        )

        self.rectification_help_button.clicked.connect(
            self._show_rectification_help
        )
        self.multipoint_help_button.clicked.connect(
            self._show_multipoint_help
        )
        self.enhancement_help_button.clicked.connect(
            self._show_enhancement_help
        )

        self._image_available = False
        self._corner_point_count = 0
        self._corner_is_valid: bool | None = None
        self._corner_error: str | None = None
        self._control_pair_count = 0
        self._multipoint_is_valid: bool | None = None
        self._multipoint_error: str | None = None

        self.set_image_available(False)
        self.set_rectified_base_available(False)

    @property
    def enhancement_settings(self) -> EnhancementSettings:
        return self.enhancement_controls.settings

    @property
    def multipoint_enabled(self) -> bool:
        """Return whether advanced multipoint correction is selected."""
        return self.multipoint_check.isChecked()

    def set_enhancement_settings(
        self,
        settings: EnhancementSettings,
    ) -> None:
        """Synchronize controls without emitting a settings-change loop."""
        self.enhancement_controls.set_settings(settings)

    def set_image_available(self, available: bool) -> None:
        """Enable rectification actions only when a raster is loaded."""
        self._image_available = bool(available)
        self.geometry_group.setEnabled(True)

        if not self._image_available:
            self.mode_check.setChecked(False)
            self.mode_check.setEnabled(False)
            self.reset_rectification_state()
            self.set_rectified_base_available(False)
        else:
            self.mode_check.setEnabled(True)
            self._refresh_rectification_controls()

    def set_rectified_base_available(self, available: bool) -> None:
        """Enable enhancement controls only when a rectified base exists."""
        self.enhancement_group.setEnabled(bool(available))

    def enter_rectification_stage(self) -> None:
        """Return the panel to editable source-image rectification."""
        self.mode_check.setEnabled(True)
        self.set_rectified_base_available(False)
        self._refresh_rectification_controls()
        self.set_enhancement_settings(EnhancementSettings())

        self.enhancement_result_label.setText(
            "Working image: not applied"
        )
        _set_pending_status(
            self.enhancement_status_label,
            "No pending enhancement changes",
        )
        self._refresh_rectification_status()

    def enter_enhancement_stage(
        self,
        derived_path: Path | str,
        working_path: Path | str | None = None,
    ) -> None:
        """Reflect that rectification is accepted and enhancement is available."""
        self.mode_check.setChecked(False)
        self.mode_check.setEnabled(False)
        self.multipoint_check.setChecked(False)
        self.multipoint_check.setEnabled(False)
        self.add_control_pair_button.setEnabled(False)
        self.remove_control_pair_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.apply_button.setEnabled(False)

        self.set_applied_path(derived_path)
        self.set_rectified_base_available(True)
        self.set_enhancement_applied_path(working_path)

    def set_selection_state(
        self,
        *,
        point_count: int,
        is_valid: bool | None,
        error: str | None = None,
        estimated_size: tuple[int, int] | None = None,
    ) -> None:
        """Update four-corner geometry while preserving standard behavior."""
        self._corner_point_count = int(point_count)
        self._corner_is_valid = is_valid
        self._corner_error = error

        self.points_label.setText(
            f"Points: {point_count} / 4"
        )

        if point_count != 4:
            self.geometry_label.setText(
                "Geometry: incomplete"
            )
            self.size_label.setText(
                "Reference grid: —"
            )
        elif is_valid is True:
            self.geometry_label.setText(
                "Geometry: valid"
            )
            if estimated_size:
                width, height = estimated_size
                self.size_label.setText(
                    f"Reference grid: {width} × {height} px"
                )
            else:
                self.size_label.setText(
                    "Reference grid: —"
                )
        elif is_valid is False:
            self.geometry_label.setText(
                f"Geometry: invalid — {error or 'Invalid quadrilateral'}"
            )
            self.size_label.setText(
                "Reference grid: —"
            )
        else:
            self.geometry_label.setText(
                "Geometry: pending validation"
            )
            self.size_label.setText(
                "Reference grid: —"
            )

        self._refresh_rectification_status()
        self._refresh_rectification_controls()

    def set_multipoint_state(
        self,
        *,
        control_pair_count: int,
        is_valid: bool | None,
        error: str | None = None,
    ) -> None:
        """Update optional intermediate-pair state and action availability."""
        self._control_pair_count = max(0, int(control_pair_count))
        self._multipoint_is_valid = is_valid
        self._multipoint_error = error

        if self._control_pair_count == 0:
            self.control_pairs_label.setText(
                "Control pairs: 0"
            )
        elif is_valid is True:
            self.control_pairs_label.setText(
                f"Control pairs: {self._control_pair_count} — valid"
            )
        elif is_valid is False:
            self.control_pairs_label.setText(
                f"Control pairs: {self._control_pair_count} — "
                f"invalid: {error or 'Invalid multipoint geometry'}"
            )
        else:
            self.control_pairs_label.setText(
                f"Control pairs: {self._control_pair_count} — "
                "pending validation"
            )

        self._refresh_rectification_controls()

    def set_applied_path(
        self,
        path: Path | str | None,
    ) -> None:
        """Retain the persisted rectified-raster reference without showing its path."""
        self.result_label.setText(
            "Applied result: —"
            if path is None
            else f"Applied result: {path}"
        )

        if path is None:
            self._refresh_rectification_status()
        else:
            _set_status(
                self.rectification_status_label,
                "✓ Rectification applied",
                valid=True,
            )
            self.rectification_status_label.setToolTip("")

    def set_enhancement_applied_path(
        self,
        path: Path | str | None,
    ) -> None:
        """Retain working-raster state without displaying its filesystem path."""
        self.enhancement_result_label.setText(
            "Working image: not applied"
            if path is None
            else f"Working image: {path}"
        )

        if path is None:
            _set_pending_status(
                self.enhancement_status_label,
                "No pending enhancement changes",
            )
        else:
            _set_status(
                self.enhancement_status_label,
                "✓ Enhancement applied",
                valid=True,
            )
        self.enhancement_status_label.setToolTip("")

    def mark_enhancement_pending(self) -> None:
        """Indicate that live settings differ from the last saved working image."""
        if self.enhancement_group.isEnabled():
            self.enhancement_result_label.setText(
                "Working image: changes not applied"
            )
            _set_pending_status(
                self.enhancement_status_label,
                "⚠ Enhancement changes not applied",
            )

    def reset_rectification_state(self) -> None:
        """Reset transient standard and advanced geometric state."""
        self._corner_point_count = 0
        self._corner_is_valid = None
        self._corner_error = None
        self._control_pair_count = 0
        self._multipoint_is_valid = None
        self._multipoint_error = None

        self.points_label.setText("Points: 0 / 4")
        self.geometry_label.setText("Geometry: —")
        self.size_label.setText("Reference grid: —")
        self.control_pairs_label.setText("Control pairs: 0")
        self.result_label.setText("Applied result: —")
        self.multipoint_check.setChecked(False)
        self.advanced_widget.hide()
        self._refresh_rectification_status()
        self._refresh_rectification_controls()

    def _multipoint_mode_toggled(self, enabled: bool) -> None:
        self.advanced_widget.setVisible(bool(enabled))
        self._refresh_rectification_controls()
        self.multipointModeToggled.emit(bool(enabled))

    def _refresh_rectification_status(self) -> None:
        """Show only the single most useful rectification state."""
        count = self._corner_point_count
        if count <= 0:
            _set_status(
                self.rectification_status_label,
                "⚠ Add 4 corner points",
                valid=False,
            )
            self.rectification_status_label.setToolTip("")
            return

        if count < 4:
            _set_status(
                self.rectification_status_label,
                f"⚠ {count} / 4 corners defined",
                valid=False,
            )
            self.rectification_status_label.setToolTip("")
            return

        if self._corner_is_valid is True:
            _set_status(
                self.rectification_status_label,
                "✓ Rectification geometry valid",
                valid=True,
            )
            self.rectification_status_label.setToolTip("")
            return

        if self._corner_is_valid is False:
            _set_status(
                self.rectification_status_label,
                "⚠ Rectification geometry invalid",
                valid=False,
            )
            self.rectification_status_label.setToolTip(
                self._corner_error or "Invalid quadrilateral"
            )
            return

        _set_status(
            self.rectification_status_label,
            "⚠ Rectification geometry pending validation",
            valid=False,
        )
        self.rectification_status_label.setToolTip("")

    def _refresh_rectification_controls(self) -> None:
        """Resolve standard vs advanced action availability in one place."""
        image_available = self._image_available
        corners_ready = bool(
            image_available
            and self._corner_point_count == 4
            and self._corner_is_valid is True
        )

        self.clear_button.setEnabled(
            image_available
            and (self._corner_point_count > 0 or self._control_pair_count > 0)
        )
        self.multipoint_check.setEnabled(corners_ready)

        advanced = bool(self.multipoint_check.isChecked())
        self.advanced_widget.setVisible(advanced)

        self.add_control_pair_button.setEnabled(corners_ready and advanced)
        self.remove_control_pair_button.setEnabled(
            corners_ready and advanced and self._control_pair_count > 0
        )

        if advanced:
            advanced_ready = bool(
                corners_ready
                and self._control_pair_count > 0
                and self._multipoint_is_valid is True
            )
            self.preview_button.setEnabled(advanced_ready)
            self.apply_button.setEnabled(advanced_ready)
        else:
            self.preview_button.setEnabled(corners_ready)
            self.apply_button.setEnabled(corners_ready)

    def _show_rectification_help(self) -> None:
        QMessageBox.information(
            self,
            "Rectification",
            RECTIFICATION_HELP_TEXT,
        )

    def _show_multipoint_help(self) -> None:
        QMessageBox.information(
            self,
            "Multipoint correction",
            MULTIPOINT_HELP_TEXT,
        )

    def _show_enhancement_help(self) -> None:
        QMessageBox.information(
            self,
            "Image Enhancement",
            ENHANCEMENT_HELP_TEXT,
        )

    def _enhancement_controls_changed(
        self,
        settings: EnhancementSettings,
    ) -> None:
        self.mark_enhancement_pending()
        self.enhancementSettingsChanged.emit(settings)
