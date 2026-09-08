"""Compact automatic tracing controls (8.4D.14)."""

from __future__ import annotations

from math import pow

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.processing.automatic_digitization import AutomaticTraceSettings

from .digitization_panel import DigitizationPanel
from .icons import ICON_ACTIVE, ICON_MUTED, ICON_SUCCESS, app_icon, danger_icon


AUTOMATIC_TRACE_HELP = (
    "Automatic tracing\n\n"
    "\u2022 Select the curve, scale and scale offset before starting.\n"
    "\u2022 Click Start trace, then click the curve in the image. The tracker "
    "follows the curve downward.\n"
    "\u2022 If the tracker leaves the curve, click the correct curve at a lower "
    "position. The incorrect lower part of the draft is removed and tracing "
    "resumes from the new point.\n"
    "\u2022 Click Finish to save the automatic trace.\n\n"
    "Clear draft discards only the unfinished automatic trace.\n\n"
    "Erase range activates a vertical selection tool anywhere on the image. "
    "Automatic trace geometry inside the selected Y range is removed; "
    "manual digitization is preserved.\n\n"
    "The curve, scale, scale offset and tracker settings are locked while an "
    "automatic trace is active. Line width is detected automatically from the "
    "initial click."
)


def _automatic_icon_button(
    icon_name: str,
    action_text: str,
    *,
    tooltip: str,
    danger: bool = False,
) -> QToolButton:
    """Create a compact icon-only automatic tracing action."""
    button = QToolButton()
    button.setText(action_text)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIcon(
        danger_icon(icon_name)
        if danger
        else app_icon(icon_name)
    )
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(32, 32)
    button.setAccessibleName(action_text)
    button.setToolTip(tooltip)
    return button


class AutomaticDigitizationPanel(DigitizationPanel):
    """Add simple tracker controls without changing the proven manual panel."""

    automaticStartRequested = Signal()
    automaticFinishRequested = Signal()
    automaticCancelRequested = Signal()
    automaticEraseToggled = Signal(bool)

    SLIDER_MIN = 0
    SLIDER_MAX = 100
    SLIDER_DEFAULT = 50

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._automatic_active = False
        self._automatic_complete = False
        self._automatic_can_finish = False
        self._automatic_can_start = False
        self._automatic_erase_active = False

        # ----------------------- advanced tracker controls -----------------
        self.automatic_sensitivity_slider = self._make_slider(
            "Controls how strongly curve pixels are detected. Increase it for "
            "faint curves; reduce it if noise or grid lines are captured."
        )
        self.automatic_search_width_slider = self._make_slider(
            "Maximum lateral distance searched for the curve. Larger values "
            "handle abrupt shifts but may jump to nearby lines.",
        )
        self.automatic_flexibility_slider = self._make_slider(
            "Controls how quickly the tracker can change direction. Increase "
            "it for irregular curves; reduce it for a smoother path.",
        )
        self.automatic_gap_tolerance_slider = self._make_slider(
            "Controls the size of discontinuities the tracker may bridge. "
            "High values can connect unrelated features."
        )

        self._slider_value_labels: dict[QSlider, QLabel] = {}
        controls = QFormLayout()
        controls.addRow(
            "Sensitivity:",
            self._slider_row(self.automatic_sensitivity_slider),
        )
        controls.addRow(
            "Search width:",
            self._slider_row(self.automatic_search_width_slider),
        )
        controls.addRow(
            "Flexibility:",
            self._slider_row(self.automatic_flexibility_slider),
        )
        controls.addRow(
            "Gap tolerance:",
            self._slider_row(self.automatic_gap_tolerance_slider),
        )

        self.automatic_line_width_label = QLabel(
            "Detected width: measured automatically at the first click"
        )
        self.automatic_line_width_label.setWordWrap(True)
        self.automatic_line_width_label.setToolTip(
            "Line width estimated from the initial click and used to adapt the "
            "tracker automatically."
        )

        self.automatic_tracker_settings = QWidget()
        tracker_layout = QVBoxLayout(self.automatic_tracker_settings)
        tracker_layout.setContentsMargins(0, 0, 0, 0)
        tracker_layout.addLayout(controls)
        tracker_layout.addWidget(self.automatic_line_width_label)
        self.automatic_tracker_settings.hide()

        # ----------------------------- run controls -------------------------
        self.automatic_status_label = QLabel(
            "\u25cf Automatic tracing unavailable"
        )
        self.automatic_status_label.setWordWrap(True)
        self._style_automatic_status()

        self.automatic_help_button = _automatic_icon_button(
            "circle-info",
            "Automatic trace help",
            tooltip="How automatic tracing works",
        )

        self.automatic_start_button = QPushButton("Start trace")
        self.automatic_start_button.setIcon(app_icon("play"))
        self.automatic_start_button.setIconSize(QSize(18, 18))
        self.automatic_start_button.setMinimumHeight(32)
        self.automatic_start_button.setToolTip(
            "Start a new automatic trace, then click the curve in the image."
        )

        self.automatic_finish_button = QPushButton("Finish")
        self.automatic_finish_button.setIcon(app_icon("check"))
        self.automatic_finish_button.setIconSize(QSize(18, 18))
        self.automatic_finish_button.setMinimumHeight(32)
        self.automatic_finish_button.setToolTip(
            "Accept and save the current automatic trace."
        )

        self.automatic_cancel_button = _automatic_icon_button(
            "trash",
            "Clear automatic draft",
            tooltip=(
                "Discard the unfinished automatic trace. Saved sections are "
                "not affected."
            ),
            danger=True,
        )
        self.automatic_erase_button = _automatic_icon_button(
            "eraser",
            "Erase automatic range",
            tooltip=(
                "Activate erase mode, then drag vertically anywhere on the image. "
                "Automatic trace geometry in the selected Y range is removed; "
                "manual digitization is preserved."
            ),
        )
        self.automatic_erase_button.setCheckable(True)

        self.automatic_settings_button = QToolButton()
        self.automatic_settings_button.setText("Advanced tracker settings")
        self.automatic_settings_button.setIcon(app_icon("chevron-right"))
        self.automatic_settings_button.setIconSize(QSize(14, 14))
        self.automatic_settings_button.setCheckable(True)
        self.automatic_settings_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.automatic_settings_button.setAccessibleName(
            "Advanced tracker settings"
        )
        self.automatic_settings_button.setToolTip(
            "Show advanced tracker settings."
        )
        self.automatic_settings_button.setStyleSheet(
            "QToolButton { border: none; padding: 3px 2px; text-align: left; } "
            "QToolButton:hover { background: #F3F4F6; border-radius: 3px; }"
        )

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.addWidget(self.automatic_status_label, 1)
        status_row.addWidget(self.automatic_help_button)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addWidget(self.automatic_start_button)
        action_row.addWidget(self.automatic_finish_button)
        action_row.addSpacing(8)
        action_row.addWidget(self.automatic_cancel_button)
        action_row.addWidget(self.automatic_erase_button)
        action_row.addStretch(1)

        settings_row = QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.addWidget(self.automatic_settings_button)
        settings_row.addStretch(1)

        group = QGroupBox("Automatic tracing")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(7)
        group_layout.addLayout(status_row)
        group_layout.addLayout(action_row)
        group_layout.addLayout(settings_row)
        group_layout.addWidget(self.automatic_tracker_settings)

        # Insert directly after Curve and before Manual digitization.
        self.automatic_group = group
        root = self.layout()
        root.insertWidget(1, group)

        self.automatic_start_button.clicked.connect(
            self.automaticStartRequested.emit
        )
        self.automatic_finish_button.clicked.connect(
            self.automaticFinishRequested.emit
        )
        self.automatic_cancel_button.clicked.connect(
            self.automaticCancelRequested.emit
        )
        self.automatic_erase_button.toggled.connect(
            self.automaticEraseToggled.emit
        )
        self.automatic_help_button.clicked.connect(
            self._show_automatic_help
        )
        self.automatic_settings_button.toggled.connect(
            self._set_tracker_settings_expanded
        )

        for slider in (
            self.automatic_sensitivity_slider,
            self.automatic_search_width_slider,
            self.automatic_flexibility_slider,
            self.automatic_gap_tolerance_slider,
        ):
            slider.valueChanged.connect(
                lambda _value, target=slider: self._update_slider_label(target)
            )
            self._update_slider_label(slider)

        self._sync_automatic_controls()

    @property
    def automatic_active(self) -> bool:
        return self._automatic_active

    @property
    def automatic_erase_active(self) -> bool:
        return self._automatic_erase_active

    @property
    def automatic_trace_settings(self) -> AutomaticTraceSettings:
        """Translate simple UI controls into the scientific tracker settings.

        The initial values incorporate the manual-trial results:
        sensitivity -> centre: contrast 0.07 / confidence 0.43
        search width -> centre: 28 px reference geometry
        flexibility -> centre: 10 px reference geometry
        gap tolerance -> centre: 2 tolerated misses

        Pixel values are only reference geometry; 8.4D.1/8.4D.2 subsequently
        scale them automatically from detected line thickness.
        """
        sensitivity = self.automatic_sensitivity_slider.value() / 100.0

        # High sensitivity means more willingness to accept faint curves.
        min_contrast_ratio = 0.11 - 0.08 * sensitivity
        min_confidence = 0.53 - 0.20 * sensitivity

        # Symmetric multiplicative ranges around the manual-trial defaults:
        # Search width: 14 .. 28 .. 56
        # Flexibility: 5 .. 10 .. 20
        search_scale = pow(
            2.0,
            (self.automatic_search_width_slider.value() - 50.0) / 50.0,
        )
        flexibility_scale = pow(
            2.0,
            (self.automatic_flexibility_slider.value() - 50.0) / 50.0,
        )

        search_half_width_px = max(1, int(round(28.0 * search_scale)))
        max_horizontal_step_px = max(0.5, 10.0 * flexibility_scale)

        # 0 .. 2 .. 4 at low / centre / high.
        max_consecutive_misses = int(
            round(4.0 * self.automatic_gap_tolerance_slider.value() / 100.0)
        )

        return AutomaticTraceSettings(
            search_half_width_px=search_half_width_px,
            min_contrast_ratio=min_contrast_ratio,
            max_horizontal_step_px=max_horizontal_step_px,
            min_confidence=min_confidence,
            max_consecutive_misses=max_consecutive_misses,
        )

    def set_detected_line_width(self, width_px: float | None) -> None:
        """Show automatic resolution adaptation as information only."""
        if width_px is None:
            self.automatic_line_width_label.setText(
                "Detected width: measured automatically at the first click"
            )
            return
        self.automatic_line_width_label.setText(
            f"Detected width: {float(width_px):.1f} px \u00b7 Automatic"
        )

    def set_context(self, *args, **kwargs) -> None:
        self._automatic_can_start = bool(kwargs.get("can_start", False))
        super().set_context(*args, **kwargs)
        if hasattr(self, "automatic_start_button"):
            if not self._automatic_active and not self._automatic_erase_active:
                self._set_default_automatic_status()
            self._sync_automatic_controls()

    def set_draft_state(self, tracing: bool, node_count: int) -> None:
        super().set_draft_state(tracing, node_count)
        if hasattr(self, "automatic_start_button"):
            self._sync_automatic_controls()

    def set_edit_state(self, editing: bool, **kwargs) -> None:
        super().set_edit_state(editing, **kwargs)
        if hasattr(self, "automatic_start_button"):
            self._sync_automatic_controls()

    def set_automatic_state(
        self,
        active: bool,
        *,
        complete: bool = False,
        can_finish: bool = False,
        status_text: str = "",
    ) -> None:
        self._automatic_active = bool(active)
        self._automatic_complete = bool(complete) and self._automatic_active
        self._automatic_can_finish = bool(can_finish) and self._automatic_active

        if status_text:
            self.automatic_status_label.setText(status_text)
        elif not self._automatic_active:
            self._set_default_automatic_status()
        elif self._automatic_complete:
            self.automatic_status_label.setText(
                "\u25cf Trace reached the image bottom"
            )
        else:
            self.automatic_status_label.setText(
                "\u25cf Automatic trace active"
            )

        self._style_automatic_status()
        self._sync_automatic_controls()

    def set_automatic_erase_state(self, active: bool) -> None:
        """Synchronize the checkable erase tool without re-emitting its signal."""
        self._automatic_erase_active = bool(active)
        blocker = QSignalBlocker(self.automatic_erase_button)
        self.automatic_erase_button.setChecked(self._automatic_erase_active)
        del blocker
        if self._automatic_erase_active:
            self.automatic_status_label.setText(
                "\u25cf Select the vertical range to erase"
            )
        elif not self._automatic_active:
            self._set_default_automatic_status()
        self._style_automatic_status()
        self._sync_automatic_controls()

    def _set_default_automatic_status(self) -> None:
        self.automatic_status_label.setText(
            "\u25cf Ready to start"
            if self._automatic_can_start
            else "\u25cf Automatic tracing unavailable"
        )
        self._style_automatic_status()

    def _style_automatic_status(self) -> None:
        """Give the established status text a quieter semantic color."""
        text = self.automatic_status_label.text()
        if "Ready to start" in text or "reached the image bottom" in text:
            color = ICON_SUCCESS
        elif "unavailable" in text:
            color = ICON_MUTED
        else:
            color = ICON_ACTIVE
        self.automatic_status_label.setStyleSheet(
            f"color: {color}; font-weight: 600;"
        )

    def _show_automatic_help(self) -> None:
        QMessageBox.information(
            self,
            "Automatic trace help",
            AUTOMATIC_TRACE_HELP,
        )

    def _set_tracker_settings_expanded(self, expanded: bool) -> None:
        """Show or hide advanced controls and expose the state with a chevron."""
        is_expanded = bool(expanded)
        self.automatic_tracker_settings.setVisible(is_expanded)
        self.automatic_settings_button.setIcon(
            app_icon("chevron-down" if is_expanded else "chevron-right")
        )
        self.automatic_settings_button.setToolTip(
            "Hide advanced tracker settings."
            if is_expanded
            else "Show advanced tracker settings."
        )

    def _make_slider(self, tooltip: str) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(self.SLIDER_MIN, self.SLIDER_MAX)
        slider.setValue(self.SLIDER_DEFAULT)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        slider.setToolTip(tooltip)
        return slider

    def _slider_row(self, slider: QSlider) -> QWidget:
        label = QLabel("Medium")
        label.setMinimumWidth(52)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._slider_value_labels[slider] = label

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        layout.addWidget(label)

        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _update_slider_label(self, slider: QSlider) -> None:
        label = self._slider_value_labels.get(slider)
        if label is None:
            return

        value = slider.value()
        if value < 35:
            text = "Low"
        elif value > 65:
            text = "High"
        else:
            text = "Medium"
        label.setText(text)

    def _sync_automatic_controls(self) -> None:
        manual_busy = bool(self.tracing or self.editing)
        capture_busy = manual_busy or self._automatic_erase_active
        self.automatic_start_button.setEnabled(
            self._automatic_can_start
            and not capture_busy
            and not self._automatic_active
        )
        self.automatic_finish_button.setEnabled(
            self._automatic_active
            and self._automatic_can_finish
            and not manual_busy
        )
        self.automatic_cancel_button.setEnabled(self._automatic_active)
        self.automatic_erase_button.setEnabled(
            self._automatic_erase_active
            or (
                self._automatic_can_start
                and self._saved_section_count > 0
                and not manual_busy
                and not self._automatic_active
            )
        )

        tracker_controls_enabled = (
            not capture_busy and not self._automatic_active
        )
        for widget in (
            self.automatic_sensitivity_slider,
            self.automatic_search_width_slider,
            self.automatic_flexibility_slider,
            self.automatic_gap_tolerance_slider,
        ):
            widget.setEnabled(tracker_controls_enabled)

        if self._automatic_active or self._automatic_erase_active:
            # Lock shared curve/manual/edit controls for the lifetime of the raw
            # automatic operation so ownership cannot change during capture.
            self.curve_combo.setEnabled(False)
            self.start_button.setEnabled(False)
            self.edit_button.setEnabled(False)
            self.scale_profile_combo.setEnabled(False)
            self.scale_offset_spin.setEnabled(False)
        else:
            self.curve_combo.setEnabled(
                bool(self._curves) and not self.tracing and not self.editing
            )
            self.start_button.setEnabled(
                self._can_start and not self.tracing and not self.editing
            )
            self.edit_button.setEnabled(
                self._can_edit and not self.tracing and not self.editing
            )
            self._sync_scale_offset_control()
