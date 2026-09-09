"""Project-level LAS 2.0 export dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import DepthUnit
from logdigitizer.gui.icons import ICON_WARNING, app_icon, colored_icon
from logdigitizer.io.las_export import (
    LasExportError,
    LasExportPlan,
    LasExportPreview,
    LasWellMetadata,
    serialize_las_2,
)
from logdigitizer.utils.paths import safe_project_stem


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Positive floating-point editor with stable decimal-point parsing.

    Qt normally parses QDoubleSpinBox text using the operating-system locale.
    LogDigitizer deliberately displays STEP with a decimal point, so on locales
    that use a decimal comma a displayed value such as ``0.25 m`` could be
    reinterpreted as ``25`` when ``interpretText()`` was called. Keep display
    and parsing symmetrical and accept either decimal point or decimal comma.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Use a stable dot-decimal locale internally. The custom validator and
        # valueFromText() still accept a comma when entered manually.
        self.setLocale(QLocale.c())

    def validate(self, text: str, pos: int):  # noqa: N802 - Qt API
        # QDoubleSpinBox validates before valueFromText() is called. Normalize
        # a decimal comma for validation so both "0.25" and "0,25" are accepted.
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

        # The widget displays a decimal point, but also accept a decimal comma
        # when the user types it manually.
        if "," in value_text and "." not in value_text:
            value_text = value_text.replace(",", ".")

        try:
            return float(value_text)
        except ValueError:
            return float(self.value())


class LasFilePreviewDialog(QDialog):
    """Read-only preview of the exact LAS 2.0 text to be exported."""

    def __init__(
        self,
        plan: LasExportPlan,
        metadata: LasWellMetadata,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.metadata = metadata
        self.las_text = serialize_las_2(plan, metadata)

        self.setWindowTitle("LAS Preview")
        self.setWindowIcon(app_icon("file-export"))
        self.setModal(True)
        self.resize(920, 700)
        self.setMinimumSize(700, 500)

        summary = QLabel(
            f"LAS 2.0 preview · {len(plan.columns)} curve(s) · "
            f"{plan.row_count:,} depth samples"
        )
        summary.setStyleSheet("font-weight: 600;")

        note = QLabel(
            "Read-only preview generated from the current export settings. "
            "No file is saved and project metadata is not changed."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6B7280;")

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        # QPlainTextEdit normalizes line endings for display; keep las_text above
        # as the exact CRLF serialization used by LAS 2.0.
        self.text_edit.setPlainText(self.las_text.replace("\r\n", "\n"))

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(note)
        layout.addWidget(self.text_edit, 1)
        layout.addWidget(self.button_box)


class LasExportDialog(QDialog):
    """Select curves, sampling and metadata, then export one LAS file."""

    def __init__(
        self,
        service: ProjectService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.exported_path: Path | None = None
        self.export_plan: LasExportPlan | None = None
        self.current_preview: LasExportPreview | None = None
        self.quality_control_requested = False
        self.quality_control_curve_ids: tuple[str, ...] = ()
        self._curve_infos = service.get_las_export_curve_infos()
        self._initial_metadata = service.get_las_metadata()

        self.setWindowTitle("Export LAS")
        self.setWindowIcon(app_icon("file-export"))
        self.setModal(True)
        self.resize(940, 680)
        self.setMinimumSize(780, 570)

        self.tabs = QTabWidget()
        self.data_tab = self._build_data_tab()
        self.metadata_tab = self._build_metadata_tab()
        self.tabs.addTab(self.data_tab, "Data and sampling")
        self.tabs.addTab(self.metadata_tab, "Well metadata")

        self.button_box = QDialogButtonBox()
        self.cancel_button = self.button_box.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip(
            "Preview the LAS 2.0 file using the current settings without saving."
        )
        self.button_box.addButton(
            self.preview_button,
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.export_button = QPushButton(
            app_icon("file-export"),
            "Export LAS…",
        )
        self.export_button.setDefault(True)
        self.button_box.addButton(
            self.export_button,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button.clicked.connect(self._show_file_preview)
        self.export_button.clicked.connect(self._export)

        root = QVBoxLayout(self)
        root.addWidget(self.tabs, 1)
        root.addWidget(self.button_box)

        self._populate_curves()
        self._populate_metadata()
        self._set_initial_step()
        self._initial_step = self.step_spin.value()
        self._refresh_preview()

    def done(self, result: int) -> None:
        """Remember an edited STEP even when closing without exporting."""
        # Keyboard tracking is disabled; commit any valid pending text first.
        self.step_spin.interpretText()
        step = self.step_spin.value()
        if step != self._initial_step:
            self.service.set_last_las_step(step)
        super().done(result)

    @property
    def selected_curve_ids(self) -> tuple[str, ...]:
        selected: list[str] = []
        for row in range(self.curve_table.rowCount()):
            item = self.curve_table.item(row, 0)
            if (
                item is not None
                and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
                and item.checkState() == Qt.CheckState.Checked
            ):
                selected.append(
                    str(item.data(Qt.ItemDataRole.UserRole))
                )
        return tuple(selected)

    @property
    def metadata(self) -> LasWellMetadata:
        return LasWellMetadata(
            company=self.company_edit.text(),
            well=self.well_edit.text(),
            field=self.field_edit.text(),
            location=self.location_edit.text(),
            province=self._initial_metadata.province,
            county=self.county_edit.text(),
            state=self.state_edit.text(),
            country=self.country_edit.text(),
            service_company=self.service_company_edit.text(),
            log_date=self.log_date_edit.text(),
            unique_well_id=self.unique_well_id_edit.text(),
            api_number=self.api_number_edit.text(),
            licence_number=self.licence_number_edit.text(),
            other_information=self.other_information_edit.toPlainText(),
        )

    def _build_data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        curves_group = QGroupBox("Curves")
        curves_layout = QVBoxLayout(curves_group)
        self.curve_table = QTableWidget(0, 5)
        self.curve_table.setHorizontalHeaderLabels(
            ("", "Mnemonic", "Curve", "Unit", "Digitized range")
        )
        self.curve_table.verticalHeader().setVisible(False)
        self.curve_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.curve_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.curve_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.curve_table.setMinimumHeight(175)
        self.curve_table.itemChanged.connect(self._refresh_preview)
        curves_layout.addWidget(self.curve_table)

        selection_row = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.clear_selection_button = QPushButton("Clear selection")
        self.select_all_button.clicked.connect(
            lambda: self._set_all_curves_checked(True)
        )
        self.clear_selection_button.clicked.connect(
            lambda: self._set_all_curves_checked(False)
        )
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_selection_button)
        selection_row.addStretch(1)
        curves_layout.addLayout(selection_row)
        layout.addWidget(curves_group)

        sampling_group = QGroupBox("Sampling")
        sampling_layout = QVBoxLayout(sampling_group)
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("STEP"))
        self.step_spin = CompactDoubleSpinBox()
        self.step_spin.setDecimals(12)
        self.step_spin.setRange(0.000000000001, 1_000_000_000.0)
        self.step_spin.setKeyboardTracking(False)
        self.step_spin.setAccelerated(True)
        self.step_spin.valueChanged.connect(self._refresh_preview)
        step_row.addWidget(self.step_spin)
        step_row.addStretch(1)
        sampling_layout.addLayout(step_row)

        summary = QFrame()
        summary.setFrameShape(QFrame.Shape.StyledPanel)
        summary_layout = QGridLayout(summary)
        self.coverage_value = self._summary_item(
            summary_layout,
            0,
            "Digitized coverage",
        )
        self.start_value = self._summary_item(
            summary_layout,
            1,
            "LAS START",
        )
        self.stop_value = self._summary_item(
            summary_layout,
            2,
            "LAS STOP",
        )
        self.rows_value = self._summary_item(
            summary_layout,
            3,
            "Estimated rows",
        )
        self.resolution_value = self._summary_item(
            summary_layout,
            4,
            "Pixel resolution",
        )
        sampling_layout.addWidget(summary)

        self.warning_frame = QFrame()
        self.warning_frame.setStyleSheet(
            "QFrame { background: #FFF7E6; border: 1px solid #E6B65C; "
            "border-radius: 4px; } QLabel { border: none; }"
        )
        warning_layout = QHBoxLayout(self.warning_frame)
        warning_icon = QLabel()
        warning_icon.setPixmap(
            colored_icon("triangle-exclamation", ICON_WARNING).pixmap(20, 20)
        )
        self.warning_label = QLabel(
            "STEP is smaller than the approximate pixel resolution. "
            "Export is still permitted."
        )
        self.warning_label.setWordWrap(True)
        warning_layout.addWidget(warning_icon)
        warning_layout.addWidget(self.warning_label, 1)
        sampling_layout.addWidget(self.warning_frame)

        self.gap_qc_frame = QFrame()
        self.gap_qc_frame.setFrameShape(QFrame.Shape.StyledPanel)
        gap_qc_layout = QHBoxLayout(self.gap_qc_frame)
        gap_qc_layout.setContentsMargins(10, 7, 10, 7)
        gap_qc_layout.setSpacing(10)

        self.gap_qc_status_label = QLabel()
        self.gap_qc_status_label.setWordWrap(True)
        self.gap_qc_status_label.setStyleSheet("font-weight: 600;")
        self.gap_qc_status_label.setToolTip(
            "Compact STEP-aware quality-control summary. "
            "Open Quality Control for gap locations and navigation."
        )
        gap_qc_layout.addWidget(self.gap_qc_status_label, 1)

        self.open_quality_control_button = QPushButton(
            app_icon("clipboard-check"),
            "Open Quality Control…",
        )
        self.open_quality_control_button.setToolTip(
            "Open the dedicated Quality Control window using the current "
            "curve selection and STEP."
        )
        self.open_quality_control_button.clicked.connect(
            self._request_quality_control
        )
        gap_qc_layout.addWidget(self.open_quality_control_button)
        sampling_layout.addWidget(self.gap_qc_frame)

        self.preview_error_label = QLabel()
        self.preview_error_label.setWordWrap(True)
        self.preview_error_label.setStyleSheet("color: #B42318;")
        sampling_layout.addWidget(self.preview_error_label)
        layout.addWidget(sampling_group)
        layout.addStretch(1)
        return page

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

    def _build_metadata_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        root = QVBoxLayout(container)

        columns = QHBoxLayout()
        left_form = QFormLayout()
        right_form = QFormLayout()
        self.well_edit = QLineEdit()
        self.unique_well_id_edit = QLineEdit()
        self.company_edit = QLineEdit()
        self.field_edit = self._optional_edit()
        self.location_edit = QLineEdit()
        self.county_edit = QLineEdit()
        self.state_edit = QLineEdit()
        self.country_edit = QLineEdit()
        self.service_company_edit = self._optional_edit()
        self.log_date_edit = QLineEdit()
        self.log_date_edit.setPlaceholderText("YYYY MM DD")
        self.api_number_edit = self._optional_edit()
        self.licence_number_edit = self._optional_edit()

        left_form.addRow("Well / borehole name", self.well_edit)
        left_form.addRow("Company", self.company_edit)
        left_form.addRow("Location", self.location_edit)
        left_form.addRow("State / region", self.state_edit)
        left_form.addRow("Service company", self.service_company_edit)
        left_form.addRow("API number", self.api_number_edit)

        right_form.addRow("Unique well ID", self.unique_well_id_edit)
        right_form.addRow("Field", self.field_edit)
        right_form.addRow("County", self.county_edit)
        right_form.addRow("Country", self.country_edit)
        right_form.addRow("Log date", self.log_date_edit)
        right_form.addRow("Licence number", self.licence_number_edit)

        left_widget = QWidget()
        left_widget.setLayout(left_form)
        right_widget = QWidget()
        right_widget.setLayout(right_form)
        columns.addWidget(left_widget, 1)
        columns.addWidget(right_widget, 1)
        root.addLayout(columns)

        other_form = QFormLayout()
        self.other_information_edit = QPlainTextEdit()
        self.other_information_edit.setMaximumHeight(110)
        other_form.addRow("Other information", self.other_information_edit)
        root.addLayout(other_form)
        note = QLabel("Unknown metadata may be left blank.")
        note.setStyleSheet("color: #6B7280;")
        root.addWidget(note)
        root.addStretch(1)

        scroll.setWidget(container)
        page_layout.addWidget(scroll)
        return page

    @staticmethod
    def _optional_edit() -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText("Optional")
        return edit

    def _populate_curves(self) -> None:
        self.curve_table.blockSignals(True)
        self.curve_table.setRowCount(len(self._curve_infos))
        for row, info in enumerate(self._curve_infos):
            check_item = QTableWidgetItem()
            check_item.setData(Qt.ItemDataRole.UserRole, info.curve_id)
            if info.exportable:
                check_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                check_item.setCheckState(Qt.CheckState.Checked)
            else:
                check_item.setFlags(Qt.ItemFlag.NoItemFlags)
                check_item.setCheckState(Qt.CheckState.Unchecked)
                check_item.setToolTip(info.unavailable_reason)
            self.curve_table.setItem(row, 0, check_item)

            values = (
                info.mnemonic,
                info.name,
                info.unit or "—",
                self._curve_range_text(info),
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    if info.exportable
                    else Qt.ItemFlag.NoItemFlags
                )
                if not info.exportable:
                    item.setToolTip(info.unavailable_reason)
                self.curve_table.setItem(row, column, item)
        self.curve_table.blockSignals(False)

    @staticmethod
    def _curve_range_text(info) -> str:
        if (
            not info.exportable
            or info.minimum_depth is None
            or info.maximum_depth is None
        ):
            return "No data"
        unit = LasExportDialog._unit_text(info.depth_unit)
        return (
            f"{info.minimum_depth:g}–{info.maximum_depth:g} {unit}"
        )

    def _populate_metadata(self) -> None:
        metadata = self._initial_metadata
        self.well_edit.setText(metadata.well)
        self.unique_well_id_edit.setText(metadata.unique_well_id)
        self.company_edit.setText(metadata.company)
        self.field_edit.setText(metadata.field)
        self.location_edit.setText(metadata.location)
        self.county_edit.setText(metadata.county)
        self.state_edit.setText(metadata.state)
        self.country_edit.setText(metadata.country)
        self.service_company_edit.setText(metadata.service_company)
        self.log_date_edit.setText(metadata.log_date)
        self.api_number_edit.setText(metadata.api_number)
        self.licence_number_edit.setText(metadata.licence_number)
        self.other_information_edit.setPlainText(metadata.other_information)

    def _set_initial_step(self) -> None:
        step = self.service.get_last_las_step()
        if step is None:
            units = {
                info.depth_unit
                for info in self._curve_infos
                if info.exportable and info.depth_unit is not None
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

    def _set_all_curves_checked(self, checked: bool) -> None:
        self.curve_table.blockSignals(True)
        state = (
            Qt.CheckState.Checked
            if checked
            else Qt.CheckState.Unchecked
        )
        for row in range(self.curve_table.rowCount()):
            item = self.curve_table.item(row, 0)
            if item is not None and bool(
                item.flags() & Qt.ItemFlag.ItemIsEnabled
            ):
                item.setCheckState(state)
        self.curve_table.blockSignals(False)
        self._refresh_preview()

    def _refresh_preview(self, *_args) -> None:
        self.current_preview = None
        self.warning_frame.hide()
        self.gap_qc_frame.hide()
        self.preview_error_label.clear()
        self.open_quality_control_button.setEnabled(False)
        self._clear_summary()
        curve_ids = self.selected_curve_ids
        if not curve_ids:
            self.preview_error_label.setText(
                "Select at least one digitized curve."
            )
            self.preview_button.setEnabled(False)
            self.export_button.setEnabled(False)
            return

        try:
            preview = self.service.preview_las_export(
                step_depth=self.step_spin.value(),
                curve_ids=curve_ids,
            )
        except (LasExportError, RuntimeError) as exc:
            self.preview_error_label.setText(str(exc))
            self.preview_button.setEnabled(False)
            self.export_button.setEnabled(False)
            return

        self.current_preview = preview
        unit = self._unit_text(preview.depth_unit)
        self.step_spin.setSuffix(f" {unit}")
        self.coverage_value.setText(
            f"{preview.coverage_start_depth:g}–"
            f"{preview.coverage_stop_depth:g} {unit}"
        )
        self.start_value.setText(f"{preview.start_depth:g} {unit}")
        self.stop_value.setText(f"{preview.stop_depth:g} {unit}")
        self.rows_value.setText(f"{preview.row_count:,}")
        if preview.first_image_resolution is None:
            self.resolution_value.setText("—")
        else:
            self.resolution_value.setText(
                f"≈ {preview.first_image_resolution:g} {unit}/pixel"
            )
        self.warning_frame.setVisible(
            preview.step_below_first_image_resolution
        )
        self._update_gap_qc(preview)
        self.open_quality_control_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.export_button.setEnabled(True)

    def _update_gap_qc(self, preview: LasExportPreview) -> None:
        """Show a compact STEP-aware quality-control summary for export."""
        self.gap_qc_frame.show()

        review_count = len(preview.internal_gaps)
        interpolated_count = len(preview.interpolated_gaps)

        if review_count:
            self.gap_qc_frame.setStyleSheet(
                "QFrame { background: #FFF7E6; border: 1px solid #E6B65C; "
                "border-radius: 4px; } QLabel { border: none; color: #7A4E00; }"
            )
            status = (
                f"⚠ Quality Control: {review_count} gap(s) require review"
            )
            if interpolated_count:
                status += (
                    f" · {interpolated_count} small gap(s) automatically "
                    "interpolated"
                )
            self.gap_qc_status_label.setText(status)
            return

        self.gap_qc_frame.setStyleSheet(
            "QFrame { background: #ECFDF3; border: 1px solid #86D49C; "
            "border-radius: 4px; } QLabel { border: none; color: #166534; }"
        )
        if interpolated_count:
            self.gap_qc_status_label.setText(
                "✓ Quality Control: No gaps require review"
                f" · {interpolated_count} small gap(s) automatically interpolated"
            )
        else:
            self.gap_qc_status_label.setText(
                "✓ Quality Control: No gaps requiring review"
            )

    def _clear_summary(self) -> None:
        for label in (
            self.coverage_value,
            self.start_value,
            self.stop_value,
            self.rows_value,
            self.resolution_value,
        ):
            label.setText("—")

    @staticmethod
    def _unit_text(unit: DepthUnit | None) -> str:
        return "m" if unit is DepthUnit.METERS else "ft"

    def _default_export_path(self) -> Path:
        metadata = self.metadata
        project = self.service.project
        stem = safe_project_stem(
            metadata.well
            or (project.name if project is not None else "well")
        )
        if self.service.paths is not None:
            return self.service.paths.root / "export" / f"{stem}.las"
        return Path(f"{stem}.las")

    def _request_quality_control(self) -> None:
        """Close Export LAS and ask the main window to open detailed QC."""
        if self.current_preview is None or not self.selected_curve_ids:
            return
        self.step_spin.interpretText()
        step = self.step_spin.value()
        self.service.set_last_las_step(step)
        self.quality_control_curve_ids = self.selected_curve_ids
        self.quality_control_requested = True
        self.reject()

    def _show_file_preview(self) -> None:
        """Build and display the exact LAS text without writing or persisting it."""
        if self.current_preview is None or not self.selected_curve_ids:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = self.service.prepare_las_export(
                step_depth=self.step_spin.value(),
                curve_ids=self.selected_curve_ids,
            )
            preview_dialog = LasFilePreviewDialog(
                plan,
                self.metadata,
                self,
            )
        except (LasExportError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Cannot preview LAS", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        preview_dialog.exec()

    def _export(self) -> None:
        if self.current_preview is None or not self.selected_curve_ids:
            return
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export LAS",
            str(self._default_export_path()),
            "LAS 2.0 (*.las);;All files (*)",
        )
        if not filename:
            return

        target = Path(filename)
        if target.suffix.lower() != ".las":
            target = target.with_suffix(".las")

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = self.service.export_las(
                target,
                step_depth=self.step_spin.value(),
                curve_ids=self.selected_curve_ids,
                metadata=self.metadata,
            )
        except (LasExportError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Cannot export LAS", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.service.set_las_metadata(self.metadata)
        self.service.set_last_las_step(self.step_spin.value())
        self.exported_path = target.expanduser().resolve()
        self.export_plan = plan
        self.accept()
