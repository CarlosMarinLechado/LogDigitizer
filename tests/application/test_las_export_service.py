from __future__ import annotations

from pathlib import Path

import pytest

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.io.las_export import LasExportError, LasWellMetadata


def _configure_curve(
    service: ProjectService,
    segment: Segment,
    curve: CurveDefinition,
    *,
    start_depth: float,
    stop_depth: float,
    pixel_height: float,
    value: float,
) -> None:
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=start_depth)
    service.add_depth_calibration_point(
        pixel_y=pixel_height,
        depth=stop_depth,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(value, 0.0),
                        TracePoint(value, pixel_height),
                    )
                )
            ],
        )
    )


def test_service_prefills_and_persists_las_metadata():
    service = ProjectService()
    service.project = LogProject.create(
        name="Project A",
        well_name="BH-01",
        location="Níjar",
        description="Historical scan",
    )

    initial = service.get_las_metadata()

    assert initial.well == "BH-01"
    assert initial.location == "Níjar"
    assert initial.other_information == "Historical scan"

    edited = LasWellMetadata(
        well="BH-01",
        country="España",
        unique_well_id="IGME-001",
    )
    service.set_las_metadata(edited)

    assert service.dirty
    assert service.project.metadata["las"] == edited.to_dict()
    assert service.get_las_metadata() == edited


def test_service_uses_shallowest_calibrated_image_for_pixel_resolution():
    service = ProjectService()
    project = LogProject.create(name="P")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)
    deep = Segment.create(order=1, name="Deep", source_path="deep.png")
    shallow = Segment.create(
        order=2,
        name="Shallow",
        source_path="shallow.png",
    )
    project.add_segment(deep)
    project.add_segment(shallow)
    service.project = project

    service.active_segment_id = deep.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=100.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=200.0)
    service.active_segment_id = shallow.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=10.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=20.0)

    assert service.get_first_image_vertical_resolution() == pytest.approx(0.1)


def test_service_default_export_selects_only_curves_with_data(
    tmp_path: Path,
):
    service = ProjectService()
    project = LogProject.create(name="P", well_name="BH-01")
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    empty_sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
    )
    project.add_curve(gr)
    project.add_curve(empty_sp)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="one.png",
    )
    project.add_segment(segment)
    service.project = project
    _configure_curve(
        service,
        segment,
        gr,
        start_depth=13.2,
        stop_depth=14.2,
        pixel_height=100.0,
        value=25.0,
    )

    output = tmp_path / "BH-01.las"
    plan = service.export_las(output, step_depth=0.2)

    assert plan.sampled.curve_ids == (gr.curve_id,)
    assert plan.columns[0].mnemonic == "GR"
    assert plan.first_image_resolution == pytest.approx(0.01)
    assert plan.row_count == 6
    text = output.read_text(encoding="ascii")
    assert "GR.API" in text
    assert "SP.MV" not in text
    assert "WELL. BH-01 : WELL" in text


def test_service_exposes_lightweight_preview_curve_infos_and_last_step():
    service = ProjectService()
    project = LogProject.create(name="P")
    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    empty_sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
    )
    project.add_curve(gr)
    project.add_curve(empty_sp)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="one.png",
    )
    project.add_segment(segment)
    service.project = project
    _configure_curve(
        service,
        segment,
        gr,
        start_depth=13.2,
        stop_depth=16.4,
        pixel_height=100.0,
        value=25.0,
    )

    infos = service.get_las_export_curve_infos()
    preview = service.preview_las_export(
        step_depth=0.5,
        curve_ids=(gr.curve_id,),
    )

    assert infos[0].exportable
    assert infos[0].minimum_depth == pytest.approx(13.2)
    assert not infos[1].exportable
    assert infos[1].unavailable_reason == "No usable digitized data."
    assert service.can_export_las()
    assert preview.start_depth == pytest.approx(13.5)
    assert preview.stop_depth == pytest.approx(16.0)
    assert preview.row_count == 6

    assert service.get_last_las_step() is None
    service.set_last_las_step(0.5)
    assert service.get_last_las_step() == pytest.approx(0.5)
    assert service.project.metadata["las_export"]["last_step_depth"] == 0.5


def test_explicit_selection_of_curve_without_data_is_rejected():
    service = ProjectService()
    project = LogProject.create(name="P")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)
    service.project = project

    with pytest.raises(LasExportError, match="no usable digitized data"):
        service.prepare_las_export(
            step_depth=0.2,
            curve_ids=(curve.curve_id,),
        )
