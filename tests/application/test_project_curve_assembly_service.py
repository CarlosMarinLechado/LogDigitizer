from __future__ import annotations

import pytest

from logdigitizer.application.project_service import (
    ImageOverlapRelation,
    ProjectService,
)
from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    CurveDefinition,
    DepthUnit,
)
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment


def _configure_gr(
    service: ProjectService,
    segment: Segment,
    *,
    curve_id: str,
    start_depth: float,
    end_depth: float,
    x_px: float,
) -> None:
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=start_depth)
    service.add_depth_calibration_point(pixel_y=100.0, depth=end_depth)
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(x_px, 0.0),
                        TracePoint(x_px, 100.0),
                    )
                )
            ],
        )
    )


def test_service_assembly_ignores_tree_order_and_excludes_absent_curve():
    service = ProjectService()
    project = LogProject.create(name="P")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)

    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    third = Segment.create(order=3, name="Image 3", source_path="three.png")
    project.add_segment(first)
    project.add_segment(second)
    project.add_segment(third)
    service.project = project

    _configure_gr(
        service,
        first,
        curve_id=curve.curve_id,
        start_depth=0.0,
        end_depth=1010.0,
        x_px=10.0,
    )
    _configure_gr(
        service,
        second,
        curve_id=curve.curve_id,
        start_depth=990.0,
        end_depth=2000.0,
        x_px=90.0,
    )
    service.active_segment_id = third.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=1900.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=3000.0)
    service.set_curve_present(curve.curve_id, False)

    before = service.get_project_curve_assembly(curve.curve_id)
    project.move_segment(second.segment_id, -1)
    after = service.get_project_curve_assembly(curve.curve_id)

    assert [source.segment_id for source in before.sources] == [
        first.segment_id,
        second.segment_id,
    ]
    assert before.joins[0].depth == pytest.approx(1000.0)
    assert after.joins == before.joins
    assert after.sample((500.0, 1000.0, 1500.0)) == pytest.approx(
        before.sample((500.0, 1000.0, 1500.0))
    )


def test_service_exposes_depth_labels_independently_of_list_order():
    service = ProjectService()
    project = LogProject.create(name="P")
    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    project.add_segment(first)
    project.add_segment(second)
    service.project = project

    service.active_segment_id = first.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=1100.0)
    service.active_segment_id = second.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=990.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=2000.0)

    intervals = service.get_project_depth_intervals()

    assert intervals[first.segment_id][:2] == pytest.approx((0.0, 1100.0))
    assert intervals[second.segment_id][:2] == pytest.approx((990.0, 2000.0))


def test_service_builds_image_overlap_guides_from_calibrated_intervals():
    service = ProjectService()
    project = LogProject.create(name="P")
    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    third = Segment.create(order=3, name="Image 3", source_path="three.png")
    project.add_segment(first)
    project.add_segment(second)
    project.add_segment(third)
    service.project = project

    for segment, start, stop in (
        (first, 0.0, 1010.0),
        (second, 990.0, 2000.0),
        (third, 1900.0, 3000.0),
    ):
        service.active_segment_id = segment.segment_id
        service.add_depth_calibration_point(pixel_y=0.0, depth=start)
        service.add_depth_calibration_point(pixel_y=100.0, depth=stop)

    first_guides = service.get_image_overlap_guides(first)
    second_guides = service.get_image_overlap_guides(second)
    third_guides = service.get_image_overlap_guides(third)

    assert [(item.relation, item.depth) for item in first_guides] == [
        (ImageOverlapRelation.NEXT, 990.0),
    ]
    assert [(item.relation, item.depth) for item in second_guides] == [
        (ImageOverlapRelation.PREVIOUS, 1010.0),
        (ImageOverlapRelation.NEXT, 1900.0),
    ]
    assert [(item.relation, item.depth) for item in third_guides] == [
        (ImageOverlapRelation.PREVIOUS, 2000.0),
    ]
    assert first_guides[0].neighbour_segment_id == second.segment_id
    assert second_guides[0].neighbour_segment_name == "Image 1"
    assert second_guides[1].neighbour_segment_name == "Image 3"

    project.move_segment(third.segment_id, -2)

    assert service.get_image_overlap_guides(first) == first_guides
    assert service.get_image_overlap_guides(second) == second_guides
    assert service.get_image_overlap_guides(third) == third_guides


def test_overlap_guides_extrapolate_calibration_to_full_image_edges():
    service = ProjectService()
    project = LogProject.create(name="P")
    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    project.add_segment(first)
    project.add_segment(second)
    service.project = project

    for segment, first_depth, last_depth in (
        (first, 100.0, 200.0),
        (second, 190.0, 290.0),
    ):
        segment.extensions["preprocess"] = {
            "output_size": [200, 121],
        }
        service.active_segment_id = segment.segment_id
        service.add_depth_calibration_point(
            pixel_y=10.0,
            depth=first_depth,
        )
        service.add_depth_calibration_point(
            pixel_y=110.0,
            depth=last_depth,
        )

    intervals = service.get_project_depth_intervals()
    assert intervals[first.segment_id][:2] == pytest.approx((90.0, 210.0))
    assert intervals[second.segment_id][:2] == pytest.approx((180.0, 300.0))

    first_guides = service.get_image_overlap_guides(first)
    second_guides = service.get_image_overlap_guides(second)

    assert [(item.relation, item.depth) for item in first_guides] == [
        (ImageOverlapRelation.NEXT, 180.0),
    ]
    assert [(item.relation, item.depth) for item in second_guides] == [
        (ImageOverlapRelation.PREVIOUS, 210.0),
    ]
    assert first_guides[0].depth != 190.0
    assert second_guides[0].depth != 200.0


@pytest.mark.parametrize("second_start", (990.0, 1010.0))
def test_service_does_not_create_overlap_guides_for_a_gap_or_touch(
    second_start: float,
):
    service = ProjectService()
    project = LogProject.create(name="P")
    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    project.add_segment(first)
    project.add_segment(second)
    service.project = project

    service.active_segment_id = first.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=990.0)
    service.active_segment_id = second.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=second_start)
    service.add_depth_calibration_point(pixel_y=100.0, depth=2000.0)

    assert service.get_image_overlap_guides(first) == ()
    assert service.get_image_overlap_guides(second) == ()


def test_service_builds_common_scientific_sampling_grid():
    service = ProjectService()
    project = LogProject.create(name="P")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="one.png",
    )
    project.add_segment(segment)
    service.project = project
    _configure_gr(
        service,
        segment,
        curve_id=curve.curve_id,
        start_depth=0.0,
        end_depth=1.0,
        x_px=25.0,
    )

    sampled = service.sample_project_curves(
        start_depth=0.0,
        stop_depth=1.0,
        step_depth=0.2,
    )

    assert sampled.depths == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    assert sampled.curve_ids == (curve.curve_id,)
    assert sampled.values_for(curve.curve_id) == pytest.approx((25.0,) * 6)


def test_las_gap_qc_uses_exact_displayed_guide_interval_from_raster_edges():
    service = ProjectService()
    project = LogProject.create(name="P")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)
    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    project.add_segment(first)
    project.add_segment(second)
    service.project = project

    # Control-point calibration bounds are 100-200 and 190-290 m, but the
    # actual 121-row raster edges extrapolate to 90-210 and 180-300 m. Those
    # raster-edge limits are exactly what the visible red guides use.
    for segment, first_depth, last_depth in (
        (first, 100.0, 200.0),
        (second, 190.0, 290.0),
    ):
        segment.extensions["preprocess"] = {
            "output_size": [200, 121],
        }
        service.active_segment_id = segment.segment_id
        service.add_depth_calibration_point(pixel_y=10.0, depth=first_depth)
        service.add_depth_calibration_point(pixel_y=110.0, depth=last_depth)
        service.add_horizontal_calibration_point(
            curve.curve_id, pixel_x=0.0, value=0.0
        )
        service.add_horizontal_calibration_point(
            curve.curve_id, pixel_x=100.0, value=100.0
        )

    service.active_segment_id = first.segment_id
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (TracePoint(25.0, 0.0), TracePoint(25.0, 113.0))
                )
            ],
        )
    )
    service.active_segment_id = second.segment_id
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (TracePoint(25.0, 27.0), TracePoint(25.0, 120.0))
                )
            ],
        )
    )

    # The exact visible guide pair is NEXT=180 and PREVIOUS=210. The real
    # digitization gap (203-207) lies wholly inside that UI-defined zone and is
    # only three STEP=1 samples wide. It must remain excluded from both QC and
    # the generic small-gap interpolation rule.
    assert service.get_las_gap_qc_excluded_intervals(DepthUnit.METERS) == (
        (180.0, 210.0),
    )

    preview = service.preview_las_export(
        step_depth=1.0,
        curve_ids=(curve.curve_id,),
    )
    assert preview.internal_gaps == ()
    assert preview.interpolated_gaps == ()

    plan = service.prepare_las_export(
        step_depth=1.0,
        curve_ids=(curve.curve_id,),
    )
    values = plan.sampled.values_for(curve.curve_id)
    for depth in (204.0, 205.0, 206.0):
        assert values[plan.sampled.depths.index(depth)] is None



def _service_with_las_scale_transition(
    *,
    use_second_scale: bool,
    lower_offset: int = 0,
    gap_y_px: float = 4.0,
) -> tuple[ProjectService, CurveDefinition]:
    service = ProjectService()
    project = LogProject.create(name="Scale transition")
    curve = CurveDefinition.create(
        name="Electrical Resistivity",
        mnemonic="RES",
        unit="OHMM",
    )
    project.add_curve(curve)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="one.png",
    )
    project.add_segment(segment)
    service.project = project
    service.active_segment_id = segment.segment_id

    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=10.0)
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

    lower_scale_id = DEFAULT_HORIZONTAL_SCALE_ID
    if use_second_scale:
        profile = service.add_horizontal_scale(curve.curve_id)
        lower_scale_id = profile.scale_id
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=0.0,
            value=100.0,
            scale_id=lower_scale_id,
        )
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=100.0,
            value=200.0,
            scale_id=lower_scale_id,
        )

    lower_y = 40.0 + float(gap_y_px)
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(80.0 if use_second_scale else 95.0, 0.0),
                        TracePoint(80.0 if use_second_scale else 95.0, 40.0),
                    ),
                    scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
                    scale_offset=0,
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(10.0 if use_second_scale else 5.0, lower_y),
                        TracePoint(10.0 if use_second_scale else 5.0, 100.0),
                    ),
                    scale_id=lower_scale_id,
                    scale_offset=lower_offset,
                ),
            ],
        )
    )
    return service, curve


def test_las_scale_id_transition_interpolates_small_seam_without_gap_warning():
    service, curve = _service_with_las_scale_transition(
        use_second_scale=True,
        gap_y_px=4.0,
    )

    preview = service.preview_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    assert preview.internal_gaps == ()

    plan = service.prepare_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    values = plan.sampled.values_for(curve.curve_id)
    index = plan.sampled.depths.index(4.2)
    # Endpoints are 80 at 4.0 m and 110 at 4.4 m.
    assert values[index] == pytest.approx(95.0)


def test_las_scale_offset_transition_uses_the_same_interpolation_rule():
    service, curve = _service_with_las_scale_transition(
        use_second_scale=False,
        lower_offset=1,
        gap_y_px=4.0,
    )

    preview = service.preview_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    assert preview.internal_gaps == ()

    plan = service.prepare_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    values = plan.sampled.values_for(curve.curve_id)
    index = plan.sampled.depths.index(4.2)
    # Endpoints are 95 at 4.0 m and 105 at 4.4 m after offset +1.
    assert values[index] == pytest.approx(100.0)


def test_large_scale_change_gap_is_not_interpolated_automatically():
    service, curve = _service_with_las_scale_transition(
        use_second_scale=True,
        gap_y_px=10.0,
    )

    preview = service.preview_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    assert preview.has_internal_gaps

    plan = service.prepare_las_export(
        step_depth=0.1,
        curve_ids=(curve.curve_id,),
    )
    values = plan.sampled.values_for(curve.curve_id)
    index = plan.sampled.depths.index(4.5)
    assert values[index] is None
