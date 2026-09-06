"""Depth and curve-calibration controls for steps 7.2 and 7.3."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from math import isfinite

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.domain.calibration import (
    CalibrationError,
    CurveDefinition,
    DEFAULT_HORIZONTAL_SCALE_ID,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
    HorizontalCalibrationPoint,
    HorizontalScaleProfile,
    ScaleType,
)
from logdigitizer.domain.states import CurveCalibrationReadiness
from logdigitizer.domain.curve_display import (
    CURVE_DISPLAY_PALETTE,
    DEFAULT_CURVE_DISPLAY_COLOR,
    normalize_curve_display_color,
)
from logdigitizer.domain.curve_catalog import (
    OTHER_CURVE_KEY,
    curve_catalog_entries,
    curve_catalog_entry,
    curve_catalog_entry_for_parameter,
)

from .depth_calibration import depth_unit_label
from .icons import (
    ICON_SUCCESS,
    ICON_WARNING,
    app_icon,
    danger_icon,
)


DEPTH_HELP_TEXT = (
    "Add at least 2 depth reference points on the rectified image.\n\n"
    "• Click “Add Y Point”, then click on the image to add a depth reference\n"
    "• Each point stores a depth value and its vertical pixel position\n"
    "• You may drag a point to refine its position\n"
    "• Only pixel Y is used for the scientific calibration\n"
    "• Use m or ft as needed for the segment"
)

CURVE_HELP_TEXT = (
    "Select a curve and calibrate its horizontal scale for this segment.\n\n"
    "• Curves belong to the whole project\n"
    "• X calibration is independent for each segment\n"
    "• Mark the curve as present if it appears in this segment\n"
    "• Add 2 X calibration points for the selected scale\n"
    "• Use + Add Scale only when the curve has another printed scale\n"
    "• The scale name is generated from its two calibrated values\n"
    "• Once valid, the curve will be ready for digitization"
)


def _icon_button(
    icon_name: str,
    action_text: str,
    tooltip: str,
    *,
    danger: bool = False,
) -> QToolButton:
    """Create one compact, accessible icon-only tool button."""
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
    button.setFixedSize(30, 30)
    return button


def _set_status(label: QLabel, text: str, *, valid: bool | None) -> None:
    """Render a compact semantic status line without changing application logic."""
    label.setText(text)
    if valid is True:
        label.setStyleSheet(f"color: {ICON_SUCCESS};")
    elif valid is False:
        label.setStyleSheet(f"color: {ICON_WARNING};")
    else:
        label.setStyleSheet("")


class CurveComboItemDelegate(QStyledItemDelegate):
    """Paint only the curve mnemonic in its assigned display color."""

    def paint(self, painter, option, index) -> None:  # noqa: D401 - Qt API
        mnemonic = str(index.data(CurveColorComboBox.CURVE_MNEMONIC_ROLE) or "")
        color_text = str(index.data(CurveColorComboBox.CURVE_COLOR_ROLE) or "")
        if not mnemonic or not color_text:
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        full_text = styled.text
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else None
        if style is None:
            super().paint(painter, option, index)
            return

        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            styled,
            painter,
            styled.widget,
        )
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            styled,
            styled.widget,
        )

        metrics = QFontMetrics(styled.font)
        baseline = (
            text_rect.top()
            + (text_rect.height() - metrics.height()) // 2
            + metrics.ascent()
        )
        x = text_rect.left()

        painter.save()
        painter.setFont(styled.font)
        painter.setPen(QColor(color_text))
        painter.drawText(x, baseline, mnemonic)
        x += metrics.horizontalAdvance(mnemonic)

        remainder = (
            full_text[len(mnemonic):]
            if full_text.startswith(mnemonic)
            else full_text
        )
        neutral = (
            styled.palette.color(QPalette.ColorRole.HighlightedText)
            if styled.state & QStyle.StateFlag.State_Selected
            else styled.palette.color(QPalette.ColorRole.Text)
        )
        painter.setPen(neutral)
        available = max(0, text_rect.right() - x + 1)
        painter.drawText(
            x,
            baseline,
            metrics.elidedText(
                remainder,
                Qt.TextElideMode.ElideRight,
                available,
            ),
        )
        painter.restore()


class CurveColorComboBox(QComboBox):
    """Curve selector whose mnemonic uses the project curve display color."""

    CURVE_MNEMONIC_ROLE = int(Qt.ItemDataRole.UserRole) + 101
    CURVE_COLOR_ROLE = CURVE_MNEMONIC_ROLE + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(CurveComboItemDelegate(self))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)

        full_text = option.currentText
        mnemonic = str(self.currentData(self.CURVE_MNEMONIC_ROLE) or "")
        color_text = str(self.currentData(self.CURVE_COLOR_ROLE) or "")
        option.currentText = ""

        painter.drawComplexControl(
            QStyle.ComplexControl.CC_ComboBox,
            option,
        )

        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        metrics = QFontMetrics(self.font())
        baseline = (
            text_rect.top()
            + (text_rect.height() - metrics.height()) // 2
            + metrics.ascent()
        )
        x = text_rect.left()

        if mnemonic and color_text:
            painter.setPen(QColor(color_text))
            painter.drawText(x, baseline, mnemonic)
            x += metrics.horizontalAdvance(mnemonic)
            remainder = (
                full_text[len(mnemonic):]
                if full_text.startswith(mnemonic)
                else full_text
            )
        else:
            remainder = full_text

        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        available = max(0, text_rect.right() - x + 1)
        painter.drawText(
            x,
            baseline,
            metrics.elidedText(
                remainder,
                Qt.TextElideMode.ElideRight,
                available,
            ),
        )


class DepthValueDialog(QDialog):
    """Ask explicitly for a depth value; no automatic next-value suggestion."""

    def __init__(
        self,
        unit: DepthUnit,
        *,
        initial_depth: float | None = None,
        title: str = "Add depth calibration point",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._unit = unit
        self._depth_value: float | None = None
        self.setWindowTitle(title)

        self.depth_edit = QLineEdit()
        self.depth_edit.setPlaceholderText("Enter depth")
        if initial_depth is not None:
            self.depth_edit.setText(f"{float(initial_depth):g}")
            self.depth_edit.selectAll()

        unit_label = QLabel(depth_unit_label(unit))

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.addWidget(self.depth_edit, 1)
        value_row.addWidget(unit_label)
        value_widget = QWidget()
        value_widget.setLayout(value_row)

        form = QFormLayout()
        form.addRow("Depth", value_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(330, 120)
        self.depth_edit.setFocus()

    @property
    def depth_value(self) -> float:
        if self._depth_value is None:
            raise RuntimeError("The dialog has not been accepted.")
        return self._depth_value

    def _validate_and_accept(self) -> None:
        raw = self.depth_edit.text().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            QMessageBox.warning(
                self,
                "Depth calibration",
                "Enter a numeric depth value.",
            )
            return

        if not isfinite(value) or value < 0:
            QMessageBox.warning(
                self,
                "Depth calibration",
                "Depth must be a finite value greater than or equal to 0.",
            )
            return

        self._depth_value = value
        self.accept()


class HorizontalValueDialog(QDialog):
    """Ask for one physical X-axis value without any automatic prefill."""

    def __init__(
        self,
        curve: CurveDefinition,
        *,
        initial_value: float | None = None,
        title: str = "Add X calibration point",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.curve = curve
        self._value: float | None = None
        self.setWindowTitle(title)

        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("Enter curve value")
        if initial_value is not None:
            self.value_edit.setText(f"{float(initial_value):g}")
            self.value_edit.selectAll()

        unit_label = QLabel(curve.unit or "—")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.value_edit, 1)
        row.addWidget(unit_label)
        row_widget = QWidget()
        row_widget.setLayout(row)

        form = QFormLayout()
        form.addRow(f"{curve.mnemonic} value", row_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(350, 120)
        self.value_edit.setFocus()

    @property
    def value(self) -> float:
        if self._value is None:
            raise RuntimeError("The dialog has not been accepted.")
        return self._value

    def _validate_and_accept(self) -> None:
        raw = self.value_edit.text().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            QMessageBox.warning(
                self,
                "X calibration",
                "Enter a numeric curve value.",
            )
            return

        if not isfinite(value):
            QMessageBox.warning(
                self,
                "X calibration",
                "Curve value must be finite.",
            )
            return
        if self.curve.scale_type is ScaleType.LOGARITHMIC and value <= 0:
            QMessageBox.warning(
                self,
                "X calibration",
                "Logarithmic calibration requires a value greater than 0.",
            )
            return

        self._value = value
        self.accept()


class CurveDefinitionDialog(QDialog):
    """Create/edit one global curve with catalog-assisted dropdowns."""

    def __init__(
        self,
        *,
        initial_curve: CurveDefinition | None = None,
        suggested_display_color: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._curve_values: dict[str, object] | None = None
        self.setWindowTitle(
            "Edit curve" if initial_curve is not None else "Add curve"
        )

        self.parameter_combo = QComboBox()
        for entry in curve_catalog_entries():
            self.parameter_combo.addItem(entry.parameter, entry.key)
        self.parameter_combo.addItem("Other…", OTHER_CURVE_KEY)

        self.name_edit = QLineEdit()
        self.mnemonic_combo = QComboBox()
        self.mnemonic_combo.setEditable(True)
        self.unit_combo = QComboBox()
        self.unit_combo.setEditable(True)

        self.scale_combo = QComboBox()
        self.scale_combo.addItem("Linear", ScaleType.LINEAR.value)
        self.scale_combo.addItem("Logarithmic", ScaleType.LOGARITHMIC.value)

        self.description_edit = QLineEdit()

        self.color_combo = QComboBox()
        for color in CURVE_DISPLAY_PALETTE:
            self.color_combo.addItem(color.label, color.hex_color)
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(30, 18)
        self.color_preview.setToolTip(
            "Display color used for this curve in the Digitization workspace."
        )
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.addWidget(self.color_combo, 1)
        color_row.addWidget(self.color_preview)
        color_widget = QWidget()
        color_widget.setLayout(color_row)

        form = QFormLayout()
        form.addRow("Parameter", self.parameter_combo)
        form.addRow("Name", self.name_edit)
        form.addRow("Mnemonic", self.mnemonic_combo)
        form.addRow("Unit", self.unit_combo)
        form.addRow("Scale type", self.scale_combo)
        form.addRow("Display color", color_widget)
        form.addRow("Description", self.description_edit)

        help_label = QLabel(
            "Catalog values are suggestions only. Name, mnemonic, unit and "
            "scale remain editable; choose Other for an unlisted curve."
        )
        help_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(help_label)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(480, 290)

        self.parameter_combo.currentIndexChanged.connect(
            self._parameter_changed
        )
        self.color_combo.currentIndexChanged.connect(
            self._update_color_preview
        )

        initial_color = (
            suggested_display_color
            if suggested_display_color is not None
            else DEFAULT_CURVE_DISPLAY_COLOR
        )
        self._set_display_color(initial_color)

        if initial_curve is None:
            self._parameter_changed()
        else:
            self._set_initial_curve(initial_curve)
        self._update_color_preview()

    @property
    def values(self) -> dict[str, object]:
        if self._curve_values is None:
            raise RuntimeError("The dialog has not been accepted.")
        return dict(self._curve_values)

    def _set_initial_curve(self, curve: CurveDefinition) -> None:
        entry = curve_catalog_entry_for_parameter(curve.name)
        self.parameter_combo.blockSignals(True)
        if entry is None:
            index = self.parameter_combo.findData(OTHER_CURVE_KEY)
        else:
            index = self.parameter_combo.findData(entry.key)
        self.parameter_combo.setCurrentIndex(max(0, index))
        self.parameter_combo.blockSignals(False)

        self._populate_choices(entry)
        self.name_edit.setText(curve.name)
        self._set_editable_combo_text(self.mnemonic_combo, curve.mnemonic)
        self._set_editable_combo_text(self.unit_combo, curve.unit)
        scale_index = self.scale_combo.findData(curve.scale_type.value)
        if scale_index >= 0:
            self.scale_combo.setCurrentIndex(scale_index)
        self.description_edit.setText(curve.description)
        self._set_display_color(curve.display_color)

    def _set_display_color(self, color: str) -> None:
        normalized = normalize_curve_display_color(color)
        index = self.color_combo.findData(normalized)
        if index >= 0:
            self.color_combo.setCurrentIndex(index)
        self._update_color_preview()

    def _update_color_preview(self, *_args) -> None:
        color = str(
            self.color_combo.currentData() or DEFAULT_CURVE_DISPLAY_COLOR
        )
        self.color_preview.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555; "
            "border-radius: 2px;"
        )

    def _parameter_changed(self, *_args) -> None:
        key = str(self.parameter_combo.currentData() or OTHER_CURVE_KEY)
        entry = curve_catalog_entry(key)
        self._populate_choices(entry)

        if entry is None:
            self.name_edit.clear()
            self.mnemonic_combo.setEditText("")
            self.unit_combo.setEditText("")
            self.description_edit.clear()
            self.scale_combo.setCurrentIndex(
                self.scale_combo.findData(ScaleType.LINEAR.value)
            )
            return

        self.name_edit.setText(entry.parameter)
        self.mnemonic_combo.setCurrentIndex(0)
        self.unit_combo.setCurrentIndex(0)
        scale_index = self.scale_combo.findData(entry.suggested_scale.value)
        if scale_index >= 0:
            self.scale_combo.setCurrentIndex(scale_index)
        self.description_edit.setText(entry.description)

    def _populate_choices(self, entry) -> None:
        self.mnemonic_combo.blockSignals(True)
        self.unit_combo.blockSignals(True)
        self.mnemonic_combo.clear()
        self.unit_combo.clear()
        if entry is not None:
            self.mnemonic_combo.addItems(list(entry.mnemonics))
            self.unit_combo.addItems(list(entry.units))
        self.mnemonic_combo.blockSignals(False)
        self.unit_combo.blockSignals(False)

    @staticmethod
    def _set_editable_combo_text(combo: QComboBox, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(text)

    def _validate_and_accept(self) -> None:
        name = self.name_edit.text().strip()
        mnemonic = self.mnemonic_combo.currentText().strip()
        unit = self.unit_combo.currentText().strip()
        description = self.description_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Curve", "Curve name cannot be empty.")
            return
        if not mnemonic:
            QMessageBox.warning(self, "Curve", "Curve mnemonic cannot be empty.")
            return

        self._curve_values = {
            "name": name,
            "mnemonic": mnemonic,
            "unit": unit,
            "scale_type": ScaleType(str(self.scale_combo.currentData())),
            "description": description,
            "display_color": normalize_curve_display_color(
                str(self.color_combo.currentData())
            ),
        }
        self.accept()


class CalibrationPanel(QWidget):
    """Compact Y/X calibration UI with contextual help and integrated status."""

    captureModeToggled = Signal(bool)
    clearRequested = Signal()
    deleteRequested = Signal(int)
    editRequested = Signal(int)
    unitChanged = Signal(object)

    curveAddRequested = Signal()
    curveEditRequested = Signal(str)
    curveRemoveRequested = Signal(str)
    curveSelectionChanged = Signal(str)
    curvePresenceChanged = Signal(str, bool)
    horizontalCaptureModeToggled = Signal(bool)
    horizontalClearRequested = Signal(str)
    horizontalDeleteRequested = Signal(str, int)
    horizontalEditRequested = Signal(str, int)
    horizontalScaleAddRequested = Signal(str)
    horizontalScaleRemoveRequested = Signal(str, str)
    horizontalScaleSelectionChanged = Signal(str, str)

    DEPTH_TABLE_MAX_HEIGHT = 155
    X_TABLE_MAX_HEIGHT = 92

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image_available = False
        self._project_available = False
        self._segment_available = False
        self._curves: tuple[CurveDefinition, ...] = ()
        self._curve_present = True
        self._scale_profiles: tuple[HorizontalScaleProfile, ...] = ()

        # ------------------------------ Y calibration ---------------------
        # Compatibility-only reference: explanatory content moved to Help.
        self.instructions = QLabel(
            "Add depth references on the rectified image. Drag a control in any "
            "direction; only pixel Y is used scientifically.",
            self,
        )
        self.instructions.hide()

        self.unit_combo = QComboBox()
        self.unit_combo.addItem("m", DepthUnit.METERS.value)
        self.unit_combo.addItem("ft", DepthUnit.FEET.value)
        self.unit_combo.setToolTip(
            "Choose the depth unit used for the Y calibration of this segment."
        )

        self.depth_help_button = _icon_button(
            "circle-info",
            "Depth calibration help",
            "Help for depth calibration.",
        )

        unit_row = QHBoxLayout()
        unit_row.setContentsMargins(0, 0, 0, 0)
        unit_row.addWidget(QLabel("Unit:"))
        unit_row.addWidget(self.unit_combo, 1)
        unit_row.addStretch(1)
        unit_row.addWidget(self.depth_help_button)

        self.availability_label = QLabel(
            "⚠ Apply rectification before depth calibration"
        )
        self.availability_label.setWordWrap(True)

        self.capture_check = QCheckBox("Add Y Point")
        self.capture_check.setToolTip(
            "Start adding depth calibration points on the image."
        )

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Pixel Y", "Depth"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.table.setMaximumHeight(self.DEPTH_TABLE_MAX_HEIGHT)
        self.table.setMinimumHeight(105)
        self.table.setToolTip(
            "Depth calibration points for this segment."
        )

        self.validation_label = QLabel("⚠ Add at least 2 depth points")
        self.validation_label.setWordWrap(True)

        self.edit_button = _icon_button(
            "pen-to-square",
            "Edit depth point",
            "Edit the selected depth point.",
        )
        self.delete_button = _icon_button(
            "trash",
            "Delete depth point",
            "Delete the selected depth point.",
            danger=True,
        )
        self.clear_button = _icon_button(
            "eraser",
            "Clear depth calibration",
            "Remove all depth calibration points from this segment.",
            danger=True,
        )

        depth_action_row = QHBoxLayout()
        depth_action_row.setContentsMargins(0, 0, 0, 0)
        depth_action_row.addWidget(self.edit_button)
        depth_action_row.addWidget(self.delete_button)
        depth_action_row.addWidget(self.clear_button)
        depth_action_row.addStretch(1)

        self.group = QGroupBox("Depth calibration (Y)")
        self.group.setToolTip(
            "Define the vertical depth calibration for the active segment."
        )
        group_layout = QVBoxLayout(self.group)
        group_layout.setSpacing(7)
        group_layout.addLayout(unit_row)
        group_layout.addWidget(self.availability_label)
        group_layout.addWidget(self.capture_check)
        group_layout.addWidget(self.table)
        group_layout.addLayout(depth_action_row)
        group_layout.addWidget(self.validation_label)

        # -------------------------- Curve / X calibration -----------------
        # Compatibility-only reference: explanatory content moved to Help.
        self.curve_help = QLabel(
            "Curves are global to the project; X calibration is independent "
            "for each scanned segment.",
            self,
        )
        self.curve_help.hide()

        self.curve_combo = CurveColorComboBox()
        self.curve_combo.setToolTip(
            "Select the curve to calibrate horizontally in this segment."
        )

        self.add_curve_button = _icon_button(
            "plus",
            "Add Curve",
            "Add a new curve to the project.",
        )
        self.edit_curve_button = _icon_button(
            "pen-to-square",
            "Edit Curve",
            "Edit the selected curve definition.",
        )
        self.remove_curve_button = _icon_button(
            "trash",
            "Remove Curve",
            "Remove the selected curve from the project.",
            danger=True,
        )
        self.curve_help_button = _icon_button(
            "circle-info",
            "Curve calibration help",
            "Help for curve calibration.",
        )

        curve_row = QHBoxLayout()
        curve_row.setContentsMargins(0, 0, 0, 0)
        curve_row.addWidget(QLabel("Curve:"))
        curve_row.addWidget(self.curve_combo, 1)
        curve_row.addWidget(self.add_curve_button)
        curve_row.addWidget(self.edit_curve_button)
        curve_row.addWidget(self.remove_curve_button)
        curve_row.addWidget(self.curve_help_button)

        # Compatibility label: keep the established plain-text API used by
        # controllers/tests, while the visible row below can color only the
        # mnemonic without exposing rich-text markup through ``text()``.
        self.curve_details_label = QLabel("No curve defined.", self)
        self.curve_details_label.setWordWrap(False)
        self.curve_details_label.setToolTip("Selected curve properties.")
        self.curve_details_label.hide()

        self.curve_mnemonic_label = QLabel("", self)
        self.curve_mnemonic_label.setWordWrap(False)
        self.curve_mnemonic_label.setStyleSheet("font-weight: 600;")
        self.curve_metadata_label = QLabel("No curve defined.", self)
        self.curve_metadata_label.setWordWrap(False)

        curve_details_row = QHBoxLayout()
        curve_details_row.setContentsMargins(0, 0, 0, 0)
        curve_details_row.setSpacing(0)
        curve_details_row.addWidget(self.curve_mnemonic_label)
        curve_details_row.addWidget(self.curve_metadata_label)
        curve_details_row.addStretch(1)

        self.curve_present_check = QCheckBox("Curve present in this image")
        self.curve_present_check.setChecked(True)
        self.curve_present_check.setToolTip(
            "Indicate whether the selected curve is visible in the current image."
        )

        self.scale_profile_combo = QComboBox()
        self.scale_profile_combo.setToolTip(
            "Select which printed horizontal scale is being calibrated."
        )
        self.add_scale_button = _icon_button(
            "plus",
            "Add Scale",
            "Add another horizontal scale for this curve and segment.",
        )
        self.remove_scale_button = _icon_button(
            "trash",
            "Remove Scale",
            "Remove the selected additional scale.",
            danger=True,
        )
        scale_profile_row = QHBoxLayout()
        scale_profile_row.setContentsMargins(0, 0, 0, 0)
        scale_profile_row.addWidget(QLabel("Scale:"))
        scale_profile_row.addWidget(self.scale_profile_combo, 1)
        scale_profile_row.addWidget(self.add_scale_button)
        scale_profile_row.addWidget(self.remove_scale_button)

        self.x_capture_check = QCheckBox("Add X Point")
        self.x_capture_check.setToolTip(
            "Start adding horizontal calibration points for the selected curve."
        )

        self.x_table = QTableWidget(0, 3)
        self.x_table.setHorizontalHeaderLabels(["#", "Pixel X", "Value"])
        self.x_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.x_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.x_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.x_table.verticalHeader().setVisible(False)
        self.x_table.horizontalHeader().setStretchLastSection(True)
        self.x_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.x_table.setMaximumHeight(self.X_TABLE_MAX_HEIGHT)
        self.x_table.setMinimumHeight(72)
        self.x_table.setToolTip(
            "Horizontal calibration points for the selected curve in this segment."
        )

        self.x_validation_label = QLabel("⚠ Select a curve")
        self.x_validation_label.setWordWrap(True)

        self.x_edit_button = _icon_button(
            "pen-to-square",
            "Edit X calibration point",
            "Edit the selected X calibration point.",
        )
        self.x_delete_button = _icon_button(
            "trash",
            "Delete X calibration point",
            "Delete the selected X calibration point.",
            danger=True,
        )
        self.x_clear_button = _icon_button(
            "eraser",
            "Clear X calibration",
            "Remove all X calibration points for the selected curve in this segment.",
            danger=True,
        )

        x_action_row = QHBoxLayout()
        x_action_row.setContentsMargins(0, 0, 0, 0)
        x_action_row.addWidget(self.x_edit_button)
        x_action_row.addWidget(self.x_delete_button)
        x_action_row.addWidget(self.x_clear_button)
        x_action_row.addStretch(1)

        self.curve_group = QGroupBox("Curve calibration (X)")
        self.curve_group.setToolTip(
            "Define the horizontal calibration for the selected curve in this segment."
        )
        curve_layout = QVBoxLayout(self.curve_group)
        curve_layout.setSpacing(8)
        curve_layout.addLayout(curve_row)
        curve_layout.addLayout(curve_details_row)
        curve_layout.addWidget(self.curve_present_check)
        curve_layout.addLayout(scale_profile_row)
        curve_layout.addWidget(self.x_capture_check)
        curve_layout.addWidget(self.x_table)
        curve_layout.addLayout(x_action_row)
        curve_layout.addWidget(self.x_validation_label)

        # ---------------- compatibility-only legacy QC --------------------
        # Keep objects and state updates so existing controller/tests remain
        # compatible, but remove the redundant summary from the visible UI.
        self.qc_depth_label = QLabel("Depth Y     — Not calibrated")
        self.qc_curve_label = QLabel("Curve X     — No curve selected")
        self.qc_ready_label = QLabel("Select a curve to check readiness.")
        self.qc_ready_label.setWordWrap(True)

        self.qc_group = QGroupBox("Calibration check")
        qc_layout = QVBoxLayout(self.qc_group)
        qc_layout.setSpacing(3)
        qc_layout.addWidget(self.qc_depth_label)
        qc_layout.addWidget(self.qc_curve_label)
        qc_layout.addWidget(self.qc_ready_label)
        self.qc_group.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.group)
        layout.addWidget(self.curve_group)
        layout.addWidget(self.qc_group)
        layout.addStretch(1)

        # Y signals
        self.capture_check.toggled.connect(self.captureModeToggled.emit)
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.edit_button.clicked.connect(self._emit_edit)
        self.delete_button.clicked.connect(self._emit_delete)
        self.clear_button.clicked.connect(self.clearRequested.emit)
        self.table.itemSelectionChanged.connect(self._update_button_state)
        self.depth_help_button.clicked.connect(self._show_depth_help)

        # Curve/X signals
        self.add_curve_button.clicked.connect(self.curveAddRequested.emit)
        self.edit_curve_button.clicked.connect(self._emit_curve_edit)
        self.remove_curve_button.clicked.connect(self._emit_curve_remove)
        self.curve_combo.currentIndexChanged.connect(self._curve_changed)
        self.curve_present_check.toggled.connect(self._presence_toggled)
        self.x_capture_check.toggled.connect(
            self.horizontalCaptureModeToggled.emit
        )
        self.x_edit_button.clicked.connect(self._emit_x_edit)
        self.x_delete_button.clicked.connect(self._emit_x_delete)
        self.x_clear_button.clicked.connect(self._emit_x_clear)
        self.x_table.itemSelectionChanged.connect(self._update_x_button_state)
        self.scale_profile_combo.currentIndexChanged.connect(
            self._scale_profile_changed
        )
        self.add_scale_button.clicked.connect(self._emit_scale_add)
        self.remove_scale_button.clicked.connect(self._emit_scale_remove)
        self.curve_help_button.clicked.connect(self._show_curve_help)

        self.set_points((), DepthUnit.METERS)
        self.set_project_available(False)
        self.set_curves(())
        self.set_curve_presence(True, segment_available=False)
        self.set_image_available(False)
        self.set_calibration_check(
            depth_valid=False,
            curve=None,
            readiness=None,
            depth_point_count=0,
            x_point_count=0,
        )

    @property
    def unit(self) -> DepthUnit:
        return DepthUnit(str(self.unit_combo.currentData()).upper())

    @property
    def selected_curve_id(self) -> str | None:
        value = self.curve_combo.currentData()
        return str(value) if value else None

    @property
    def curve_present(self) -> bool:
        return self._curve_present

    @property
    def selected_scale_id(self) -> str:
        value = self.scale_profile_combo.currentData()
        return str(value or DEFAULT_HORIZONTAL_SCALE_ID)

    def selected_curve(self) -> CurveDefinition | None:
        curve_id = self.selected_curve_id
        if curve_id is None:
            return None
        return next(
            (curve for curve in self._curves if curve.curve_id == curve_id),
            None,
        )

    def set_project_available(self, available: bool) -> None:
        self._project_available = bool(available)
        self.add_curve_button.setEnabled(self._project_available)
        self._update_curve_button_state()
        self._update_presence_state()

    def set_image_available(self, available: bool) -> None:
        self._image_available = bool(available)
        if not self._image_available and self.capture_check.isChecked():
            self.capture_check.setChecked(False)
        if not self._image_available and self.x_capture_check.isChecked():
            self.x_capture_check.setChecked(False)

        self.capture_check.setEnabled(self._image_available)
        self.table.setEnabled(self._image_available)
        self.unit_combo.setEnabled(
            self._image_available and self.table.rowCount() == 0
        )

        if self._image_available:
            _set_status(
                self.availability_label,
                "✓ Rectified image ready",
                valid=True,
            )
            self.availability_label.setToolTip(
                "The rectified working image is available for Y calibration."
            )
        else:
            _set_status(
                self.availability_label,
                "⚠ Apply rectification before depth calibration",
                valid=False,
            )
            self.availability_label.setToolTip(
                "Depth calibration requires a rectified image."
            )

        self._update_button_state()
        self._update_x_button_state()

    def set_capture_mode(self, enabled: bool) -> None:
        self.capture_check.setChecked(bool(enabled) and self._image_available)

    def set_horizontal_capture_mode(self, enabled: bool) -> None:
        allowed = (
            bool(enabled)
            and self._image_available
            and self.selected_curve_id is not None
            and self._curve_present
            and self.x_table.rowCount() < 2
        )
        self.x_capture_check.setChecked(allowed)

    def set_points(
        self,
        points: Sequence[DepthCalibrationPoint],
        unit: DepthUnit,
    ) -> None:
        self.unit_combo.blockSignals(True)
        wanted = self.unit_combo.findData(unit.value)
        if wanted >= 0:
            self.unit_combo.setCurrentIndex(wanted)
        self.unit_combo.blockSignals(False)

        self.table.setRowCount(len(points))
        label = depth_unit_label(unit)
        for row, point in enumerate(points):
            values = (
                str(row + 1),
                f"{point.pixel_y:.2f}",
                f"{point.depth:.4g} {label}",
            )
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))

        self.unit_combo.setEnabled(
            self._image_available and len(points) == 0
        )

        if not points:
            _set_status(
                self.validation_label,
                "⚠ Add at least 2 depth points",
                valid=False,
            )
            self.validation_label.setToolTip("")
        elif len(points) == 1:
            _set_status(
                self.validation_label,
                "⚠ Add at least one more depth point",
                valid=False,
            )
            self.validation_label.setToolTip("")
        else:
            try:
                DepthCalibration(tuple(points), unit)
            except CalibrationError as exc:
                _set_status(
                    self.validation_label,
                    "⚠ Y calibration invalid",
                    valid=False,
                )
                self.validation_label.setToolTip(
                    f"Check point order and depth consistency. {exc}"
                )
            else:
                _set_status(
                    self.validation_label,
                    f"✓ Y calibration valid ({len(points)} points)",
                    valid=True,
                )
                self.validation_label.setToolTip("")

        self._update_button_state()

    def set_curves(
        self,
        curves: Sequence[CurveDefinition],
        selected_curve_id: str | None = None,
    ) -> None:
        previous = selected_curve_id or self.selected_curve_id
        self._curves = tuple(curves)

        self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        for curve in self._curves:
            self.curve_combo.addItem(
                f"{curve.mnemonic} — {curve.name}",
                curve.curve_id,
            )
            row = self.curve_combo.count() - 1
            self.curve_combo.setItemData(
                row,
                curve.mnemonic,
                CurveColorComboBox.CURVE_MNEMONIC_ROLE,
            )
            self.curve_combo.setItemData(
                row,
                curve.display_color,
                CurveColorComboBox.CURVE_COLOR_ROLE,
            )
        if previous:
            index = self.curve_combo.findData(previous)
            if index >= 0:
                self.curve_combo.setCurrentIndex(index)
        self.curve_combo.blockSignals(False)

        self.curve_combo.setEnabled(bool(self._curves))
        self._update_curve_details()
        self._update_curve_button_state()
        self._update_presence_state()

    def set_curve_presence(
        self,
        present: bool,
        *,
        segment_available: bool,
    ) -> None:
        """Set checkbox state without emitting a persistence request."""
        self._segment_available = bool(segment_available)
        self._curve_present = bool(present)
        self.curve_present_check.blockSignals(True)
        self.curve_present_check.setChecked(self._curve_present)
        self.curve_present_check.blockSignals(False)
        if not self._curve_present and self.x_capture_check.isChecked():
            self.x_capture_check.setChecked(False)
        self._update_presence_state()
        self._update_x_button_state()

    def set_horizontal_scales(
        self,
        profiles: Sequence[HorizontalScaleProfile],
        curve: CurveDefinition | None,
        *,
        selected_scale_id: str | None = None,
        curve_present: bool = True,
    ) -> None:
        """Refresh the scale selector and the one shared X-point table."""
        previous = selected_scale_id or self.selected_scale_id
        normalized = tuple(profiles)
        if curve is not None and not normalized:
            # UI-only draft: the first profile is persisted with the first
            # calibration point, exactly like projects created before this UI.
            normalized = (
                HorizontalScaleProfile(DEFAULT_HORIZONTAL_SCALE_ID, ()),
            )
        self._scale_profiles = normalized

        self.scale_profile_combo.blockSignals(True)
        self.scale_profile_combo.clear()
        for profile in normalized:
            label = (
                profile.display_name(curve)
                if curve is not None
                else profile.scale_id
            )
            self.scale_profile_combo.addItem(label, profile.scale_id)
        index = self.scale_profile_combo.findData(previous)
        if index < 0 and self.scale_profile_combo.count():
            index = 0
        self.scale_profile_combo.setCurrentIndex(index)
        self.scale_profile_combo.blockSignals(False)

        selected = next(
            (
                profile
                for profile in normalized
                if profile.scale_id == self.selected_scale_id
            ),
            None,
        )
        self.set_horizontal_points(
            () if selected is None else selected.points,
            curve,
            curve_present=curve_present,
        )
        self._update_scale_button_state()

    def set_horizontal_points(
        self,
        points: Sequence[HorizontalCalibrationPoint],
        curve: CurveDefinition | None,
        *,
        curve_present: bool = True,
    ) -> None:
        if self.x_capture_check.isChecked() and (
            curve is None
            or len(points) >= 2
            or not self._image_available
            or not curve_present
        ):
            self.x_capture_check.setChecked(False)

        visible_points = tuple(points) if curve_present else ()
        self.x_table.setRowCount(len(visible_points))
        unit = curve.unit if curve is not None else ""
        for row, point in enumerate(visible_points):
            value_text = f"{point.value:.6g}"
            if unit:
                value_text += f" {unit}"
            values = (
                str(row + 1),
                f"{point.pixel_x:.2f}",
                value_text,
            )
            for column, text in enumerate(values):
                self.x_table.setItem(row, column, QTableWidgetItem(text))

        if curve is None:
            _set_status(
                self.x_validation_label,
                "⚠ Select a curve",
                valid=False,
            )
            self.x_validation_label.setToolTip("")
        elif not curve_present:
            _set_status(
                self.x_validation_label,
                f"⚠ {curve.mnemonic} not present — X calibration not required",
                valid=False,
            )
            self.x_validation_label.setToolTip("")
        elif not points:
            _set_status(
                self.x_validation_label,
                f"⚠ Calibrate {curve.mnemonic} before digitization",
                valid=False,
            )
            self.x_validation_label.setToolTip("")
        elif len(points) == 1:
            _set_status(
                self.x_validation_label,
                "⚠ Add at least one more X point",
                valid=False,
            )
            self.x_validation_label.setToolTip("")
        else:
            try:
                HorizontalCalibration.from_points(
                    (points[0], points[1]),
                    curve.scale_type,
                )
            except CalibrationError as exc:
                _set_status(
                    self.x_validation_label,
                    f"⚠ {curve.mnemonic} X calibration invalid",
                    valid=False,
                )
                self.x_validation_label.setToolTip(
                    f"Check point order and calibration consistency. {exc}"
                )
            else:
                _set_status(
                    self.x_validation_label,
                    f"✓ {curve.mnemonic} X calibration valid ({len(points)} points)",
                    valid=True,
                )
                self.x_validation_label.setToolTip("")

        self._update_x_button_state()
        self._update_scale_button_state()

    def set_calibration_check(
        self,
        *,
        depth_valid: bool,
        curve: CurveDefinition | None,
        readiness: CurveCalibrationReadiness | None,
        depth_point_count: int,
        x_point_count: int,
    ) -> None:
        """Maintain legacy QC state while integrating readiness into X status."""
        # Legacy hidden labels: preserve exact behavior for compatibility.
        if depth_valid:
            self.qc_depth_label.setText(
                f"Depth Y     ✓ Valid ({depth_point_count} points)"
            )
        elif depth_point_count:
            self.qc_depth_label.setText(
                f"Depth Y     ⚠ Not valid ({depth_point_count} point(s))"
            )
        else:
            self.qc_depth_label.setText("Depth Y     — Not calibrated")

        if curve is None or readiness is None:
            self.qc_curve_label.setText("Curve X     — No curve selected")
            self.qc_ready_label.setText("Select a curve to check readiness.")
            _set_status(
                self.x_validation_label,
                "⚠ Select a curve",
                valid=False,
            )
            return

        if readiness is CurveCalibrationReadiness.NOT_PRESENT:
            self.qc_curve_label.setText(
                f"{curve.mnemonic} X      — Not present"
            )
            self.qc_ready_label.setText(
                f"No calibration required for {curve.mnemonic} in this segment."
            )
            _set_status(
                self.x_validation_label,
                f"⚠ {curve.mnemonic} not present — X calibration not required",
                valid=False,
            )
        elif readiness is CurveCalibrationReadiness.DEPTH_NOT_CALIBRATED:
            if x_point_count == 2:
                self.qc_curve_label.setText(
                    f"{curve.mnemonic} X      ✓ Valid"
                )
            elif x_point_count:
                self.qc_curve_label.setText(
                    f"{curve.mnemonic} X      ⚠ {x_point_count}/2 points"
                )
            else:
                self.qc_curve_label.setText(
                    f"{curve.mnemonic} X      — Not calibrated"
                )
            self.qc_ready_label.setText(
                "⚠ Calibrate Depth Y before digitization."
            )
            _set_status(
                self.x_validation_label,
                "⚠ Complete Y calibration first",
                valid=False,
            )
        elif readiness is CurveCalibrationReadiness.X_NOT_CALIBRATED:
            if x_point_count:
                self.qc_curve_label.setText(
                    f"{curve.mnemonic} X      ⚠ {x_point_count}/2 points"
                )
                _set_status(
                    self.x_validation_label,
                    "⚠ Add at least one more X point",
                    valid=False,
                )
            else:
                self.qc_curve_label.setText(
                    f"{curve.mnemonic} X      ⚠ Missing"
                )
                _set_status(
                    self.x_validation_label,
                    f"⚠ Calibrate {curve.mnemonic} before digitization",
                    valid=False,
                )
            self.qc_ready_label.setText(
                f"⚠ Calibrate {curve.mnemonic} before digitization."
            )
        else:
            self.qc_curve_label.setText(
                f"{curve.mnemonic} X      ✓ Valid"
            )
            self.qc_ready_label.setText("✓ Ready for digitization")
            if x_point_count == 2:
                _set_status(
                    self.x_validation_label,
                    f"✓ {curve.mnemonic} X calibration valid (2 points)",
                    valid=True,
                )
            elif x_point_count:
                _set_status(
                    self.x_validation_label,
                    "⚠ Add at least one more X point to this scale",
                    valid=False,
                )
            else:
                _set_status(
                    self.x_validation_label,
                    f"⚠ Calibrate the selected {curve.mnemonic} scale",
                    valid=False,
                )

    def selected_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return int(rows[0].row())

    def selected_x_index(self) -> int | None:
        rows = self.x_table.selectionModel().selectedRows()
        if not rows:
            return None
        return int(rows[0].row())

    def _unit_changed(self, *_args) -> None:
        self.unitChanged.emit(self.unit)

    def _emit_edit(self) -> None:
        index = self.selected_index()
        if index is not None:
            self.editRequested.emit(index)

    def _emit_delete(self) -> None:
        index = self.selected_index()
        if index is not None:
            self.deleteRequested.emit(index)

    def _curve_changed(self, *_args) -> None:
        if self.x_capture_check.isChecked():
            self.x_capture_check.setChecked(False)
        self._update_curve_details()
        curve_id = self.selected_curve_id
        if curve_id:
            self.curveSelectionChanged.emit(curve_id)
        self._update_curve_button_state()
        self._update_presence_state()
        self._update_x_button_state()

    def _scale_profile_changed(self, *_args) -> None:
        if self.x_capture_check.isChecked():
            self.x_capture_check.setChecked(False)
        profile = next(
            (
                item
                for item in self._scale_profiles
                if item.scale_id == self.selected_scale_id
            ),
            None,
        )
        self.set_horizontal_points(
            () if profile is None else profile.points,
            self.selected_curve(),
            curve_present=self._curve_present,
        )
        curve_id = self.selected_curve_id
        if curve_id:
            self.horizontalScaleSelectionChanged.emit(
                curve_id,
                self.selected_scale_id,
            )
        self._update_scale_button_state()

    def _emit_scale_add(self) -> None:
        curve_id = self.selected_curve_id
        if curve_id:
            self.horizontalScaleAddRequested.emit(curve_id)

    def _emit_scale_remove(self) -> None:
        curve_id = self.selected_curve_id
        if curve_id:
            self.horizontalScaleRemoveRequested.emit(
                curve_id,
                self.selected_scale_id,
            )

    def _presence_toggled(self, checked: bool) -> None:
        self._curve_present = bool(checked)
        curve_id = self.selected_curve_id
        if curve_id:
            self.curvePresenceChanged.emit(curve_id, self._curve_present)
        self._update_x_button_state()

    def _emit_curve_edit(self) -> None:
        curve_id = self.selected_curve_id
        if curve_id:
            self.curveEditRequested.emit(curve_id)

    def _emit_curve_remove(self) -> None:
        curve_id = self.selected_curve_id
        if curve_id:
            self.curveRemoveRequested.emit(curve_id)

    def _emit_x_edit(self) -> None:
        curve_id = self.selected_curve_id
        index = self.selected_x_index()
        if curve_id is not None and index is not None:
            self.horizontalEditRequested.emit(curve_id, index)

    def _emit_x_delete(self) -> None:
        curve_id = self.selected_curve_id
        index = self.selected_x_index()
        if curve_id is not None and index is not None:
            self.horizontalDeleteRequested.emit(curve_id, index)

    def _emit_x_clear(self) -> None:
        curve_id = self.selected_curve_id
        if curve_id is not None:
            self.horizontalClearRequested.emit(curve_id)

    def _show_depth_help(self) -> None:
        QMessageBox.information(
            self,
            "Depth calibration (Y)",
            DEPTH_HELP_TEXT,
        )

    def _show_curve_help(self) -> None:
        QMessageBox.information(
            self,
            "Curve calibration (X)",
            CURVE_HELP_TEXT,
        )

    def _update_curve_details(self) -> None:
        curve = self.selected_curve()
        if curve is None:
            self.curve_details_label.setText("No curve defined.")
            self.curve_details_label.setToolTip(
                "No curve is currently selected."
            )
            self.curve_mnemonic_label.clear()
            self.curve_mnemonic_label.setStyleSheet("font-weight: 600;")
            self.curve_metadata_label.setText("No curve defined.")
            self.curve_metadata_label.setToolTip(
                "No curve is currently selected."
            )
            return

        scale = (
            "Linear"
            if curve.scale_type is ScaleType.LINEAR
            else "Logarithmic"
        )
        unit = curve.unit or "unitless"
        plain_text = f"{curve.mnemonic} · {unit} · {scale}"
        self.curve_details_label.setText(plain_text)

        self.curve_mnemonic_label.setText(curve.mnemonic)
        self.curve_mnemonic_label.setStyleSheet(
            f"color: {curve.display_color}; font-weight: 600;"
        )
        self.curve_metadata_label.setText(f" · {unit} · {scale}")

        tooltip = (
            "Selected curve properties."
            if not curve.description
            else curve.description
        )
        self.curve_details_label.setToolTip(tooltip)
        self.curve_mnemonic_label.setToolTip(tooltip)
        self.curve_metadata_label.setToolTip(tooltip)
        self.curve_combo.setToolTip(
            "Select the curve to calibrate horizontally in this segment."
            + (
                f"\n\n{curve.description}"
                if curve.description
                else ""
            )
        )

    def _update_curve_button_state(self) -> None:
        has_curve = self.selected_curve_id is not None
        self.edit_curve_button.setEnabled(
            self._project_available and has_curve
        )
        self.remove_curve_button.setEnabled(
            self._project_available and has_curve
        )

    def _update_presence_state(self) -> None:
        has_curve = self.selected_curve_id is not None
        self.curve_present_check.setEnabled(
            self._project_available and self._segment_available and has_curve
        )
        self._update_scale_button_state()

    def _update_scale_button_state(self) -> None:
        has_curve = self.selected_curve_id is not None
        can_work = bool(
            self._image_available
            and self._project_available
            and self._segment_available
            and has_curve
            and self._curve_present
        )
        profiles = self._scale_profiles
        all_complete = bool(profiles) and all(
            profile.complete for profile in profiles
        )
        self.scale_profile_combo.setEnabled(can_work and len(profiles) > 1)
        self.add_scale_button.setEnabled(can_work and all_complete)
        self.remove_scale_button.setEnabled(
            can_work
            and self.selected_scale_id != DEFAULT_HORIZONTAL_SCALE_ID
        )

    def _update_button_state(self) -> None:
        has_selection = self.selected_index() is not None
        self.edit_button.setEnabled(self._image_available and has_selection)
        self.delete_button.setEnabled(self._image_available and has_selection)
        self.clear_button.setEnabled(
            self._image_available and self.table.rowCount() > 0
        )

    def _update_x_button_state(self) -> None:
        has_curve = self.selected_curve_id is not None
        has_selection = self.selected_x_index() is not None
        point_count = self.x_table.rowCount()
        can_work = self._image_available and has_curve and self._curve_present
        self.x_table.setEnabled(can_work)
        self.x_capture_check.setEnabled(can_work and point_count < 2)
        self.x_edit_button.setEnabled(can_work and has_selection)
        self.x_delete_button.setEnabled(can_work and has_selection)
        self.x_clear_button.setEnabled(can_work and point_count > 0)
        self._update_scale_button_state()
