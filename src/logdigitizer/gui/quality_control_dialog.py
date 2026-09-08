"""Modeless project-level digitization quality-control window."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import DepthUnit
from logdigitizer.gui.icons import app_icon
from logdigitizer.io.las_export import LasCurveGap, LasExportError, LasExportPreview


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Positive floating-point editor with stable decimal-point parsing.

    Qt normally parses QDoubleSpinBox text using the operating-system locale.
    LogDigitizer deliberately displays STEP with a decimal point, so on locales
    that use a decimal comma a displayed value such as ``0.25 m`` could be
    reinterpreted as ``25`` when ``interpretText()`` was called. Keep display
    and parsing symmetrical and accept either decimal point or decimal comma.
    """

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


class QualityControlDialog(QDialog):
    """Review STEP-aware digitization gaps while keeping the main window usable."""

    navigationRequested = Signal(str, str, float, float, float, bool)
    stepChanged = Signal(float)

    STATUS_REVIEW = "Review"
    STATUS_INTERPOLATE = "Interpolate"

    def __init__(
        self,
        service: ProjectService,
        parent=None,
        *,
        curve_ids: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.current_preview: LasExportPreview | None = None
        self._curve_filter: tuple[str, ...] | None = (
            tuple(str(curve_id) for curve_id in curve_ids)
            if curve_ids is not None
            else None
        )
        self._curve_infos = ()
        self._issues: list[tuple[str, LasCurveGap]] = []
        self._initializing_step = False

        self.setWindowTitle("Quality Control")
        self.setWindowIcon(app_icon("clipboard-check"))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(900, 650)
        self.setMinimumSize(760, 520)

        intro = QLabel(
            "Review digitization continuity on the LAS sampling grid. "
            "Small internal runs of up to 5 missing samples are interpolated "
            "only in the LAS output; longer runs remain for review."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #6B7280;")

        sampling_group = QGroupBox("Sampling")
        sampling_row = QHBoxLayout(sampling_group)
        sampling_row.addWidget(QLabel("STEP"))
        self.step_spin = CompactDoubleSpinBox()
        self.step_spin.setDecimals(12)
        self.step_spin.setRange(0.000000000001, 1_000_000_000.0)
        self.step_spin.setKeyboardTracking(False)
        self.step_spin.setAccelerated(True)
        sampling_row.addWidget(self.step_spin)
        sampling_row.addStretch(1)
        self.refresh_button = QPushButton(
            app_icon("rotate"),
            "Refresh",
        )
        self.refresh_button.setToolTip(
            "Repeat quality-control analysis after editing digitized traces."
        )
        sampling_row.addWidget(self.refresh_button)

        self.status_frame = QFrame()
        self.status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QVBoxLayout(self.status_frame)
        status_layout.setContentsMargins(10, 8, 10, 8)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: 600;")
        status_layout.addWidget(self.status_label)

        summary_group = QGroupBox("Summary by curve")
        summary_layout = QVBoxLayout(summary_group)
        self.summary_table = QTableWidget(0, 4)
        self.summary_table.setHorizontalHeaderLabels(
            ("Curve", "Review", "Interpolate", "Status")
        )
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.summary_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        summary_header = self.summary_table.horizontalHeader()
        summary_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            summary_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.summary_table.setMaximumHeight(190)
        summary_layout.addWidget(self.summary_table)

        issues_group = QGroupBox("Issues")
        issues_layout = QVBoxLayout(issues_group)
        self.issue_table = QTableWidget(0, 5)
        self.issue_table.setHorizontalHeaderLabels(
            ("Status", "Curve", "Missing interval", "Samples", "Bounded by")
        )
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.issue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.issue_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        issue_header = self.issue_table.horizontalHeader()
        issue_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        issue_header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        issue_header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        issue_header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        issue_header.setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        issues_layout.addWidget(self.issue_table, 1)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #B42318;")
        issues_layout.addWidget(self.error_label)

        navigation_row = QHBoxLayout()
        self.primary_navigation_button = QPushButton(
            app_icon("location-crosshairs"),
            "Go to interval",
        )
        self.secondary_navigation_button = QPushButton(
            app_icon("location-crosshairs"),
            "Go to next trace",
        )
        self.primary_navigation_button.setEnabled(False)
        self.secondary_navigation_button.setEnabled(False)
        self.secondary_navigation_button.hide()
        navigation_row.addWidget(self.primary_navigation_button)
        navigation_row.addWidget(self.secondary_navigation_button)
        navigation_row.addStretch(1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )

        root = QVBoxLayout(self)
        root.addWidget(intro)
        root.addWidget(sampling_group)
        root.addWidget(self.status_frame)
        root.addWidget(summary_group)
        root.addWidget(issues_group, 1)
        root.addLayout(navigation_row)
        root.addWidget(self.button_box)

        self.refresh_button.clicked.connect(self.refresh_analysis)
        self.step_spin.valueChanged.connect(self._step_changed)
        self.issue_table.itemSelectionChanged.connect(
            self._update_navigation_buttons
        )
        self.issue_table.cellDoubleClicked.connect(
            lambda *_args: self._navigate_primary()
        )
        self.primary_navigation_button.clicked.connect(
            self._navigate_primary
        )
        self.secondary_navigation_button.clicked.connect(
            self._navigate_secondary
        )
        self.button_box.rejected.connect(self.reject)

        self._reload_curve_infos()
        self._set_initial_step()
        self.refresh_analysis()

    @property
    def curve_ids(self) -> tuple[str, ...]:
        return tuple(info.curve_id for info in self._curve_infos)

    def set_curve_filter(self, curve_ids: tuple[str, ...] | None) -> None:
        """Restrict QC to a curve selection, or use every exportable curve."""
        self._curve_filter = (
            None
            if curve_ids is None
            else tuple(str(curve_id) for curve_id in curve_ids)
        )
        self._reload_curve_infos()
        self.refresh_analysis()

    def sync_step_from_project(self) -> None:
        """Reload a STEP changed by Export LAS while this window was open."""
        try:
            step = self.service.get_last_las_step()
        except RuntimeError:
            return
        if step is None or step == self.step_spin.value():
            return
        previous = self.step_spin.blockSignals(True)
        self.step_spin.setValue(step)
        self.step_spin.blockSignals(previous)
        self.refresh_analysis()

    def refresh_analysis(self) -> None:
        """Repeat STEP-aware QC using current project digitization."""
        self._reload_curve_infos()
        self.current_preview = None
        self.error_label.clear()
        self._issues.clear()
        self.summary_table.setRowCount(0)
        self.issue_table.setRowCount(0)
        self.primary_navigation_button.setEnabled(False)
        self.secondary_navigation_button.setEnabled(False)
        self.secondary_navigation_button.hide()

        curve_ids = self.curve_ids
        if not curve_ids:
            self.status_frame.setStyleSheet("")
            self.status_label.setText(
                "No digitized curves are ready for quality control."
            )
            return

        try:
            preview = self.service.preview_las_export(
                step_depth=self.step_spin.value(),
                curve_ids=curve_ids,
            )
        except (LasExportError, RuntimeError) as exc:
            self.status_frame.setStyleSheet("")
            self.status_label.setText("Quality-control analysis is unavailable.")
            self.error_label.setText(str(exc))
            return

        self.current_preview = preview
        unit = self._unit_text(preview.depth_unit)
        self.step_spin.setSuffix(f" {unit}")
        self._issues = [
            *((self.STATUS_REVIEW, gap) for gap in preview.internal_gaps),
            *(
                (self.STATUS_INTERPOLATE, gap)
                for gap in preview.interpolated_gaps
            ),
        ]
        self._issues.sort(
            key=lambda item: (
                item[1].mnemonic.casefold(),
                item[1].missing_start_depth,
                item[0],
            )
        )

        self._update_status(preview)
        self._populate_summary(preview)
        self._populate_issues(unit)

    def _reload_curve_infos(self) -> None:
        infos = tuple(
            info
            for info in self.service.get_las_export_curve_infos()
            if info.exportable
        )
        if self._curve_filter is not None:
            wanted = set(self._curve_filter)
            infos = tuple(info for info in infos if info.curve_id in wanted)
        self._curve_infos = infos

    def _set_initial_step(self) -> None:
        self._initializing_step = True
        try:
            step = self.service.get_last_las_step()
            if step is None:
                units = {
                    info.depth_unit
                    for info in self._curve_infos
                    if info.depth_unit is not None
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
        finally:
            self._initializing_step = False

    def _step_changed(self, value: float) -> None:
        if self._initializing_step:
            return
        try:
            self.service.set_last_las_step(float(value))
        except (LasExportError, RuntimeError) as exc:
            self.error_label.setText(str(exc))
            return
        self.stepChanged.emit(float(value))
        self.refresh_analysis()

    def _update_status(self, preview: LasExportPreview) -> None:
        review_count = len(preview.internal_gaps)
        review_samples = preview.missing_internal_sample_count
        interpolation_count = len(preview.interpolated_gaps)
        interpolation_samples = preview.interpolated_sample_count

        if review_count:
            self.status_frame.setStyleSheet(
                "QFrame { background: #FFF7E6; border: 1px solid #E6B65C; "
                "border-radius: 4px; } QLabel { border: none; color: #7A4E00; }"
            )
            text = (
                f"⚠ {review_count} gap(s) require review "
                f"({review_samples} missing LAS sample(s))."
            )
            if interpolation_count:
                text += (
                    f" {interpolation_count} small gap(s) "
                    f"({interpolation_samples} sample(s)) will be "
                    "interpolated automatically in LAS."
                )
            self.status_label.setText(text)
            return

        self.status_frame.setStyleSheet(
            "QFrame { background: #ECFDF3; border: 1px solid #86D49C; "
            "border-radius: 4px; } QLabel { border: none; color: #166534; }"
        )
        if interpolation_count:
            self.status_label.setText(
                "✓ No gaps require review. "
                f"{interpolation_count} small gap(s), "
                f"{interpolation_samples} LAS sample(s), will be "
                "interpolated automatically."
            )
        else:
            self.status_label.setText(
                "✓ No internal gaps detected for the current STEP."
            )

    def _populate_summary(self, preview: LasExportPreview) -> None:
        review_by_curve: dict[str, int] = defaultdict(int)
        interpolate_by_curve: dict[str, int] = defaultdict(int)
        for gap in preview.internal_gaps:
            review_by_curve[gap.curve_id] += 1
        for gap in preview.interpolated_gaps:
            interpolate_by_curve[gap.curve_id] += 1

        self.summary_table.setRowCount(len(self._curve_infos))
        for row, info in enumerate(self._curve_infos):
            review = review_by_curve[info.curve_id]
            interpolate = interpolate_by_curve[info.curve_id]
            if review:
                status = self.STATUS_REVIEW
            elif interpolate:
                status = self.STATUS_INTERPOLATE
            else:
                status = "OK"
            values = (
                info.mnemonic,
                str(review),
                str(interpolate),
                status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.summary_table.setItem(row, column, item)

    def _populate_issues(self, unit: str) -> None:
        self.issue_table.setRowCount(len(self._issues))
        for row, (status, gap) in enumerate(self._issues):
            missing_interval = self._range_text(
                gap.missing_start_depth,
                gap.missing_stop_depth,
                unit,
            )
            bounded_by = (
                f"{gap.previous_depth:g} → {gap.next_depth:g} {unit}"
            )
            values = (
                status,
                gap.mnemonic,
                missing_interval,
                str(gap.missing_sample_count),
                bounded_by,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if status == self.STATUS_REVIEW:
                    item.setBackground(
                        QBrush(QColor("#FFF4CC"))
                    )
                self.issue_table.setItem(row, column, item)

        if self._issues:
            first_review = next(
                (
                    index
                    for index, (status, _gap) in enumerate(self._issues)
                    if status == self.STATUS_REVIEW
                ),
                0,
            )
            self.issue_table.selectRow(first_review)

    def _update_navigation_buttons(self) -> None:
        selected = self._selected_issue()
        if selected is None:
            self.primary_navigation_button.setEnabled(False)
            self.secondary_navigation_button.setEnabled(False)
            self.secondary_navigation_button.hide()
            return

        _status, gap = selected
        targets = self._navigation_targets(gap)
        if not targets:
            self.primary_navigation_button.setEnabled(False)
            self.secondary_navigation_button.setEnabled(False)
            self.secondary_navigation_button.hide()
            return

        self.primary_navigation_button.setEnabled(True)
        if len(targets) == 1:
            self.primary_navigation_button.setText("Go to interval")
            self.secondary_navigation_button.hide()
            self.secondary_navigation_button.setEnabled(False)
            return

        self.primary_navigation_button.setText("Go to previous trace")
        self.secondary_navigation_button.setText("Go to next trace")
        self.secondary_navigation_button.show()
        self.secondary_navigation_button.setEnabled(True)

    def _selected_issue(self) -> tuple[str, LasCurveGap] | None:
        row = self.issue_table.currentRow()
        if row < 0 or row >= len(self._issues):
            return None
        return self._issues[row]

    def _navigate_primary(self) -> None:
        selected = self._selected_issue()
        if selected is None:
            return
        status, gap = selected
        targets = self._navigation_targets(gap)
        if not targets:
            return
        segment_id, depth, focus_start, focus_stop = targets[0]
        self.navigationRequested.emit(
            gap.curve_id,
            segment_id,
            depth,
            focus_start,
            focus_stop,
            status == self.STATUS_INTERPOLATE,
        )

    def _navigate_secondary(self) -> None:
        selected = self._selected_issue()
        if selected is None:
            return
        status, gap = selected
        targets = self._navigation_targets(gap)
        if len(targets) < 2:
            return
        segment_id, depth, focus_start, focus_stop = targets[1]
        self.navigationRequested.emit(
            gap.curve_id,
            segment_id,
            depth,
            focus_start,
            focus_stop,
            status == self.STATUS_INTERPOLATE,
        )

    def _navigation_targets(
        self,
        gap: LasCurveGap,
    ) -> tuple[tuple[str, float], ...]:
        try:
            assembly = self.service.get_project_curve_assembly(gap.curve_id)
        except (RuntimeError, ValueError):
            return ()

        previous_source = self._source_for_depth(
            assembly.sources,
            gap.previous_depth,
        )
        next_source = self._source_for_depth(
            assembly.sources,
            gap.next_depth,
        )
        if previous_source is None and next_source is None:
            return ()

        if (
            previous_source is not None
            and next_source is not None
            and previous_source.segment_id == next_source.segment_id
        ):
            target_depth = (
                float(gap.missing_start_depth)
                + float(gap.missing_stop_depth)
            ) / 2.0
            return (
                (
                    previous_source.segment_id,
                    target_depth,
                    float(gap.previous_depth),
                    float(gap.next_depth),
                ),
            )

        targets: list[tuple[str, float, float, float]] = []
        if previous_source is not None:
            targets.append(
                (
                    previous_source.segment_id,
                    float(gap.previous_depth),
                    float(gap.previous_depth),
                    float(gap.next_depth),
                )
            )
        if next_source is not None:
            candidate = (
                next_source.segment_id,
                float(gap.next_depth),
                float(gap.previous_depth),
                float(gap.next_depth),
            )
            if candidate not in targets:
                targets.append(candidate)
        return tuple(targets)

    @staticmethod
    def _source_for_depth(sources, depth: float):
        target = float(depth)
        if not isfinite(target):
            return None
        tolerance = 1e-8 * max(1.0, abs(target))

        for source in sources:
            for start, stop in source.section_depth_ranges:
                low = min(float(start), float(stop))
                high = max(float(start), float(stop))
                if low - tolerance <= target <= high + tolerance:
                    return source

        nearest = None
        nearest_distance = float("inf")
        for source in sources:
            for start, stop in source.section_depth_ranges:
                distance = min(
                    abs(target - float(start)),
                    abs(target - float(stop)),
                )
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest = source
        return nearest

    @staticmethod
    def _range_text(start: float, stop: float, unit: str) -> str:
        if start == stop:
            return f"{start:g} {unit}"
        return f"{start:g}–{stop:g} {unit}"

    @staticmethod
    def _unit_text(unit: DepthUnit | None) -> str:
        return "m" if unit is DepthUnit.METERS else "ft"
