from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.gui.las_export_dialog import LasExportDialog, LasFilePreviewDialog


def _service_with_gr_and_empty_sp() -> tuple[
    ProjectService,
    CurveDefinition,
    CurveDefinition,
]:
    service = ProjectService()
    project = LogProject.create(
        name="Historical log",
        well_name="BH-01",
        location="Níjar, Almería",
        description="Digitized historical scan.",
    )
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
    )
    project.add_curve(gr)
    project.add_curve(sp)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="image.png",
    )
    project.add_segment(segment)
    service.project = project
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=13.2)
    service.add_depth_calibration_point(pixel_y=100.0, depth=16.4)
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=gr.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 100.0),
                    )
                )
            ],
        )
    )
    service.dirty = False
    return service, gr, sp


def test_dialog_has_two_tabs_curve_availability_and_prefilled_metadata(
    qtbot,
) -> None:
    service, gr, _sp = _service_with_gr_and_empty_sp()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.tabs.count() == 2
    assert dialog.tabs.tabText(0) == "Data and sampling"
    assert dialog.tabs.tabText(1) == "Well metadata"
    assert dialog.curve_table.rowCount() == 2

    gr_check = dialog.curve_table.item(0, 0)
    sp_check = dialog.curve_table.item(1, 0)
    assert gr_check.data(Qt.ItemDataRole.UserRole) == gr.curve_id
    assert gr_check.checkState() == Qt.CheckState.Checked
    assert bool(gr_check.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert sp_check.checkState() == Qt.CheckState.Unchecked
    assert not bool(sp_check.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert dialog.curve_table.item(1, 4).text() == "No data"

    assert dialog.selected_curve_ids == (gr.curve_id,)
    assert dialog.current_preview is not None
    assert dialog.preview_button.isEnabled()
    assert dialog.export_button.isEnabled()
    assert dialog.well_edit.text() == "BH-01"
    assert dialog.location_edit.text() == "Níjar, Almería"
    assert dialog.other_information_edit.toPlainText() == (
        "Digitized historical scan."
    )


def test_preview_updates_selection_and_pixel_resolution_warning(qtbot) -> None:
    service, gr, _sp = _service_with_gr_and_empty_sp()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)
    dialog.show()

    resolution = service.get_first_image_vertical_resolution()
    assert resolution is not None
    dialog.step_spin.setValue(resolution / 2.0)
    assert not dialog.warning_frame.isHidden()

    dialog.step_spin.setValue(resolution * 2.0)
    assert dialog.warning_frame.isHidden()

    qtbot.mouseClick(
        dialog.clear_selection_button,
        Qt.MouseButton.LeftButton,
    )
    assert dialog.selected_curve_ids == ()
    assert dialog.current_preview is None
    assert not dialog.preview_button.isEnabled()
    assert not dialog.export_button.isEnabled()
    assert "Select at least one" in dialog.preview_error_label.text()

    qtbot.mouseClick(
        dialog.select_all_button,
        Qt.MouseButton.LeftButton,
    )
    assert dialog.selected_curve_ids == (gr.curve_id,)
    assert dialog.preview_button.isEnabled()
    assert dialog.export_button.isEnabled()


def test_file_preview_uses_current_settings_without_writing_or_persisting(
    qtbot,
    monkeypatch,
) -> None:
    service, gr, _sp = _service_with_gr_and_empty_sp()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)

    original_metadata = service.get_las_metadata()
    assert service.get_last_las_step() is None
    assert service.dirty is False

    dialog.country_edit.setText("España")
    dialog.unique_well_id_edit.setText("IGME-001")
    dialog.step_spin.setValue(0.2)

    captured: dict[str, object] = {}

    def fake_exec(preview_dialog):
        captured["text"] = preview_dialog.las_text
        captured["plan"] = preview_dialog.plan
        captured["read_only"] = preview_dialog.text_edit.isReadOnly()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(LasFilePreviewDialog, "exec", fake_exec)

    qtbot.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)

    text = captured["text"]
    plan = captured["plan"]
    assert isinstance(text, str)
    assert "~Version Information\r\n" in text
    assert "VERS. 2.0" in text
    assert "WELL. BH-01 : WELL" in text
    assert "CTRY. Espana : COUNTRY" in text
    assert "UWI. IGME-001 : UNIQUE WELL ID" in text
    assert "GR.API" in text
    assert plan.sampled.curve_ids == (gr.curve_id,)
    assert captured["read_only"] is True

    assert dialog.result() == 0
    assert dialog.exported_path is None
    assert dialog.export_plan is None
    assert service.get_las_metadata() == original_metadata
    assert service.get_last_las_step() is None
    assert service.dirty is False


def test_successful_export_persists_metadata_and_step_only_after_write(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    service, gr, _sp = _service_with_gr_and_empty_sp()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)
    output = tmp_path / "BH-01.las"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "LAS 2.0 (*.las)"),
    )
    dialog.country_edit.setText("España")
    dialog.unique_well_id_edit.setText("IGME-001")
    dialog.step_spin.setValue(0.2)

    qtbot.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.exported_path == output.resolve()
    assert dialog.export_plan is not None
    assert dialog.export_plan.sampled.curve_ids == (gr.curve_id,)
    assert output.is_file()
    assert service.get_las_metadata().country == "España"
    assert service.get_las_metadata().unique_well_id == "IGME-001"
    assert service.get_last_las_step() == pytest.approx(0.2)
    assert service.dirty


def test_cancelled_save_picker_does_not_persist_dialog_edits(
    qtbot,
    monkeypatch,
) -> None:
    service, _gr, _sp = _service_with_gr_and_empty_sp()
    original = service.get_las_metadata()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    dialog.country_edit.setText("España")

    qtbot.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == 0
    assert service.get_las_metadata() == original
    assert service.get_last_las_step() is None


def _service_with_gapped_gr() -> tuple[ProjectService, CurveDefinition]:
    service = ProjectService()
    project = LogProject.create(name="Gap QC", well_name="BH-GAP")
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(gr)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="image.png",
    )
    project.add_segment(segment)
    service.project = project
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=10.0)
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=gr.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 40.0),
                    )
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 60.0),
                        TracePoint(25.0, 100.0),
                    )
                ),
            ],
        )
    )
    service.dirty = False
    return service, gr


def test_gap_qc_shows_interpolated_small_gaps_and_warns_only_above_five(
    qtbot,
) -> None:
    service, gr = _service_with_gapped_gr()
    dialog = LasExportDialog(service)
    qtbot.addWidget(dialog)

    # STEP=1 places one LAS sample inside the gap, so it is auto-interpolated.
    dialog.step_spin.setValue(1.0)

    assert dialog.current_preview is not None
    assert dialog.current_preview.internal_gaps == ()
    assert len(dialog.current_preview.interpolated_gaps) == 1
    assert not dialog.gap_qc_frame.isHidden()
    assert not dialog.gap_qc_details.isHidden()
    assert "No gaps requiring review" in dialog.gap_qc_status_label.text()
    assert "automatically interpolated" in dialog.gap_qc_status_label.text()
    assert "GR: interpolated between 4 and 6 m" in (
        dialog.gap_qc_details.toPlainText()
    )
    assert "1 LAS sample interpolated" in dialog.gap_qc_details.toPlainText()

    # STEP=0.25 places seven missing samples, which exceeds the five-sample rule.
    dialog.step_spin.setValue(0.25)

    assert dialog.current_preview is not None
    assert len(dialog.current_preview.internal_gaps) == 1
    assert dialog.current_preview.interpolated_gaps == ()
    assert "Gaps requiring review" in dialog.gap_qc_status_label.text()
    assert "7 missing LAS sample" in dialog.gap_qc_status_label.text()
    assert "GR: gap between 4 and 6 m" in dialog.gap_qc_details.toPlainText()

    # STEP=2 lands directly on 4 and 6, so there is no missing LAS sample.
    dialog.step_spin.setValue(2.0)

    assert dialog.current_preview is not None
    assert dialog.current_preview.internal_gaps == ()
    assert dialog.current_preview.interpolated_gaps == ()
    assert "No internal gaps detected" in dialog.gap_qc_status_label.text()
    assert dialog.gap_qc_details.isHidden()
    assert dialog.preview_button.isEnabled()
    assert dialog.export_button.isEnabled()
