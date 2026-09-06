import pytest

from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.domain.states import StageStatus


def make_segment(order: int, name: str) -> Segment:
    return Segment.create(order=order, name=name, source_path=f"source/{name}")


def test_add_remove_and_reorder_segments_preserve_ids() -> None:
    project = LogProject.create(name="BH")
    first = make_segment(1, "a.png")
    second = make_segment(2, "b.png")
    third = make_segment(3, "c.png")

    project.add_segment(first)
    project.add_segment(second)
    project.add_segment(third)
    ids = [segment.segment_id for segment in project.segments]

    assert [segment.order for segment in project.segments] == [1, 2, 3]
    assert project.move_segment(third.segment_id, -2)
    assert [segment.segment_id for segment in project.segments] == [ids[2], ids[0], ids[1]]
    assert [segment.order for segment in project.segments] == [1, 2, 3]

    removed = project.remove_segment(first.segment_id)
    assert removed.segment_id == first.segment_id
    assert [segment.segment_id for segment in project.segments] == [ids[2], ids[1]]
    assert [segment.order for segment in project.segments] == [1, 2]


def test_duplicate_segment_id_is_rejected() -> None:
    project = LogProject.create(name="BH")
    segment = make_segment(1, "a.png")
    project.add_segment(segment)
    with pytest.raises(ValueError):
        project.add_segment(segment)


def test_downstream_invalidation_is_prepared() -> None:
    segment = make_segment(1, "a.png")
    segment.calibration_status = StageStatus.COMPLETE
    segment.digitization_status = StageStatus.COMPLETE

    segment.mark_downstream_review_required("preprocess")

    assert segment.calibration_status is StageStatus.REVIEW_REQUIRED
    assert segment.digitization_status is StageStatus.REVIEW_REQUIRED
