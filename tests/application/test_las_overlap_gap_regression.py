from __future__ import annotations

import pytest

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment


@pytest.mark.parametrize("step", [1.0, 0.0763])
@pytest.mark.parametrize("connected", [False, True])
def test_image_overlap_qc_matches_exported_nulls(step, connected):
    """An image overlap never proves that the saved traces actually meet."""
    service = ProjectService()
    project = LogProject.create(name="Erina overlap regression")
    curve = CurveDefinition.create(name="Gamma Ray", mnemonic="GR", unit="API")
    project.add_curve(curve)
    service.project = project
    segments = []
    for order, (start, stop, trace_start, trace_stop) in enumerate(
        [
            (0.0, 380.493, 1.0, 350.0 if connected else 322.0),
            (311.778, 1119.0, 340.0 if connected else 369.0, 500.0),
        ],
        start=1,
    ):
        segment = Segment.create(order=order, name=f"Image {order}", source_path=f"{order}.png")
        project.add_segment(segment)
        segments.append(segment)
        service.active_segment_id = segment.segment_id
        # One metre per pixel, with full extent defined by calibration bounds.
        service.add_depth_calibration_point(pixel_y=0.0, depth=start)
        service.add_depth_calibration_point(pixel_y=stop-start, depth=stop)
        service.add_horizontal_calibration_point(curve.curve_id, pixel_x=0.0, value=0.0)
        service.add_horizontal_calibration_point(curve.curve_id, pixel_x=100.0, value=100.0)
        service.set_digitized_curve(DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[TraceSection.create_manual((
                TracePoint(40.0, trace_start-start),
                TracePoint(40.0, trace_stop-start),
            ))],
        ))

    guides = [service.get_image_overlap_guides(s, curve.curve_id)[0] for s in segments]
    preview = service.preview_las_export(step_depth=step, curve_ids=(curve.curve_id,))
    plan = service.prepare_las_export(step_depth=step, curve_ids=(curve.curve_id,))
    missing = [
        depth for depth, value in zip(plan.sampled.depths, plan.sampled.values_for(curve.curve_id))
        if value is None
    ]
    assert preview.interpolated_gaps == ()
    if connected:
        assert missing == []
        assert not preview.has_internal_gaps
    else:
        assert preview.has_internal_gaps
        assert len(preview.internal_gaps) == 1
        gap = preview.internal_gaps[0]
        assert gap.missing_sample_count == len(missing)
        assert gap.missing_start_depth == missing[0]
        assert gap.missing_stop_depth == missing[-1]
        if step == 1.0:
            assert missing == list(range(323, 369))
    assert guides[0].depth == guides[1].depth == pytest.approx(346.1355)


@pytest.mark.parametrize("missing_count", [1, 5, 6])
def test_nphi_overlap_uses_five_sample_threshold(missing_count):
    service = ProjectService()
    project = LogProject.create(name="NPHI small overlap gap")
    curve = CurveDefinition.create(name="Neutron Porosity", mnemonic="NPHI", unit="V/V")
    project.add_curve(curve)
    service.project = project
    for order, (start, stop, trace_start, trace_stop, value) in enumerate([
        (1000.0, 1119.0, 1000.0, 1113.0, 0.1),
        (1035.0, 1263.0, 1113.0 + (missing_count + 1) * 0.2, 1200.0, 0.3),
    ], start=1):
        segment = Segment.create(order=order, name=f"Image {order}", source_path=f"{order}.png")
        project.add_segment(segment)
        service.active_segment_id = segment.segment_id
        service.add_depth_calibration_point(pixel_y=0.0, depth=start)
        service.add_depth_calibration_point(pixel_y=stop-start, depth=stop)
        service.add_horizontal_calibration_point(curve.curve_id, pixel_x=0.0, value=0.0)
        service.add_horizontal_calibration_point(curve.curve_id, pixel_x=100.0, value=1.0)
        service.set_digitized_curve(DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[TraceSection.create_manual((
                TracePoint(value * 100, trace_start-start),
                TracePoint(value * 100, trace_stop-start),
            ))],
        ))
    preview = service.preview_las_export(step_depth=0.2, curve_ids=(curve.curve_id,))
    plan = service.prepare_las_export(step_depth=0.2, curve_ids=(curve.curve_id,))
    values = plan.sampled.values_for(curve.curve_id)
    at_1113_2 = values[plan.sampled.depths.index(1113.2)]
    if missing_count <= 5:
        assert not preview.has_internal_gaps
        assert len(preview.interpolated_gaps) == 1
        assert preview.interpolated_gaps[0].missing_sample_count == missing_count
        assert at_1113_2 == pytest.approx(0.1 + 0.2 / (missing_count + 1))
        assert all(value is not None for value in values)
    else:
        assert preview.interpolated_gaps == ()
        assert preview.internal_gaps[0].missing_sample_count == missing_count
        assert at_1113_2 is None
        assert sum(value is None for value in values) == missing_count
