from __future__ import annotations

from logdigitizer.application.project_service import (
    ImageOverlapRelation,
    ProjectService,
)
from logdigitizer.domain.calibration import CurveDefinition, DepthUnit
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment


def _add_depth_interval(
    service: ProjectService,
    segment: Segment,
    start: float,
    stop: float,
) -> None:
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=start)
    service.add_depth_calibration_point(pixel_y=100.0, depth=stop)


def test_overlap_guides_follow_curve_presence_not_all_calibrated_images() -> None:
    service = ProjectService()
    project = LogProject.create(name="Parallel image sets")
    gr = CurveDefinition.create(name="Gamma Ray", mnemonic="GR", unit="API")
    dt = CurveDefinition.create(name="Sonic", mnemonic="DT", unit="US/F")
    project.add_curve(gr)
    project.add_curve(dt)

    first = Segment.create(order=1, name="Image 1", source_path="one.png")
    second = Segment.create(order=2, name="Image 2", source_path="two.png")
    third = Segment.create(order=3, name="Image 3", source_path="three.png")
    project.add_segment(first)
    project.add_segment(second)
    project.add_segment(third)
    service.project = project

    _add_depth_interval(service, first, 0.0, 500.0)
    service.set_curve_present(dt.curve_id, False)

    _add_depth_interval(service, second, 450.0, 1000.0)
    service.set_curve_present(dt.curve_id, False)

    _add_depth_interval(service, third, 0.0, 500.0)
    service.set_curve_present(gr.curve_id, False)

    first_gr = service.get_image_overlap_guides(first, gr.curve_id)
    second_gr = service.get_image_overlap_guides(second, gr.curve_id)

    assert [(guide.relation, guide.depth) for guide in first_gr] == [
        (ImageOverlapRelation.NEXT, 450.0),
    ]
    assert [(guide.relation, guide.depth) for guide in second_gr] == [
        (ImageOverlapRelation.PREVIOUS, 500.0),
    ]
    assert first_gr[0].neighbour_segment_id == second.segment_id
    assert second_gr[0].neighbour_segment_id == first.segment_id

    # Image 3 is the only DT source, so it has no DT previous/next guide.
    assert service.get_image_overlap_guides(third, dt.curve_id) == ()

    # A curve explicitly absent from the target image can never show guides.
    assert service.get_image_overlap_guides(third, gr.curve_id) == ()
    assert service.get_image_overlap_guides(first, dt.curve_id) == ()

    # LAS gap-QC exclusions use the same curve-specific guide sequence.
    assert service.get_las_gap_qc_excluded_intervals(
        DepthUnit.METERS,
        gr.curve_id,
    ) == ((450.0, 500.0),)
    assert service.get_las_gap_qc_excluded_intervals(
        DepthUnit.METERS,
        dt.curve_id,
    ) == ()
