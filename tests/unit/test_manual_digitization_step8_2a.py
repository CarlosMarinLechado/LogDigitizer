from __future__ import annotations

import pytest

from logdigitizer.domain.digitization import TracePoint, TraceSource
from logdigitizer.processing.manual_digitization import (
    ManualTraceDirection,
    ManualTraceDraft,
    ManualTraceError,
    sample_pchip_trace,
)


def test_first_point_leaves_creation_direction_undefined() -> None:
    draft = ManualTraceDraft()
    draft.add_point((40.0, 100.0))

    assert draft.points == (TracePoint(40.0, 100.0),)
    assert draft.direction is None
    assert draft.can_finish is False


def test_second_point_sets_downward_direction_and_later_points_continue() -> None:
    draft = ManualTraceDraft()
    draft.add_point((40.0, 100.0))
    draft.add_point((45.0, 120.0))
    draft.add_point((42.0, 180.0))

    assert draft.direction is ManualTraceDirection.DOWN
    assert [point.y_px for point in draft.points] == [100.0, 120.0, 180.0]
    assert draft.can_finish is True


def test_second_point_sets_upward_direction_and_later_points_continue() -> None:
    draft = ManualTraceDraft()
    draft.add_point((42.0, 180.0))
    draft.add_point((45.0, 120.0))
    draft.add_point((40.0, 100.0))

    assert draft.direction is ManualTraceDirection.UP
    assert [point.y_px for point in draft.points] == [180.0, 120.0, 100.0]


def test_reversal_is_rejected_without_changing_existing_draft() -> None:
    draft = ManualTraceDraft()
    draft.add_point((40.0, 100.0))
    draft.add_point((45.0, 120.0))
    before = draft.points

    with pytest.raises(ManualTraceError, match="moving downward"):
        draft.add_point((50.0, 110.0))

    assert draft.points == before
    assert draft.direction is ManualTraceDirection.DOWN


def test_duplicate_vertical_position_is_rejected() -> None:
    draft = ManualTraceDraft()
    draft.add_point((40.0, 100.0))

    with pytest.raises(ManualTraceError, match="different y_px"):
        draft.add_point((80.0, 100.0))

    assert draft.points == (TracePoint(40.0, 100.0),)
    assert draft.direction is None


def test_clear_resets_points_and_creation_direction() -> None:
    draft = ManualTraceDraft(((10.0, 100.0), (20.0, 120.0)))
    assert draft.direction is ManualTraceDirection.DOWN

    draft.clear()

    assert draft.points == ()
    assert draft.direction is None
    assert draft.can_finish is False


def test_finish_requires_two_points() -> None:
    draft = ManualTraceDraft()
    draft.add_point((10.0, 100.0))

    with pytest.raises(ManualTraceError, match="at least two"):
        draft.finish()


def test_upward_created_trace_finishes_as_canonical_sorted_manual_section() -> None:
    draft = ManualTraceDraft()
    draft.add_point((70.0, 300.0))
    draft.add_point((55.0, 200.0))
    draft.add_point((60.0, 100.0))

    section = draft.finish()

    assert section.source is TraceSource.MANUAL
    assert [point.y_px for point in section.control_points_px] == [
        100.0,
        200.0,
        300.0,
    ]
    assert [point.x_px for point in section.control_points_px] == [
        60.0,
        55.0,
        70.0,
    ]
    assert section.raw_samples_px == ()


def test_pchip_preview_contains_all_control_nodes_and_is_sorted_by_y() -> None:
    points = (
        TracePoint(80.0, 300.0),
        TracePoint(20.0, 100.0),
        TracePoint(50.0, 200.0),
    )

    samples = sample_pchip_trace(points, max_step_y_px=30.0)

    assert samples[0] == TracePoint(20.0, 100.0)
    assert samples[-1] == TracePoint(80.0, 300.0)
    assert all(
        first.y_px < second.y_px
        for first, second in zip(samples, samples[1:])
    )

    samples_by_y = {sample.y_px: sample.x_px for sample in samples}
    assert samples_by_y[100.0] == pytest.approx(20.0)
    assert samples_by_y[200.0] == pytest.approx(50.0)
    assert samples_by_y[300.0] == pytest.approx(80.0)


def test_two_point_pchip_preview_is_linear() -> None:
    samples = sample_pchip_trace(
        (TracePoint(10.0, 0.0), TracePoint(30.0, 20.0)),
        max_step_y_px=5.0,
    )

    assert [(point.x_px, point.y_px) for point in samples] == pytest.approx(
        [
            (10.0, 0.0),
            (15.0, 5.0),
            (20.0, 10.0),
            (25.0, 15.0),
            (30.0, 20.0),
        ]
    )


def test_preview_sampling_density_does_not_change_control_points() -> None:
    draft = ManualTraceDraft(
        (
            (10.0, 0.0),
            (40.0, 50.0),
            (25.0, 100.0),
        )
    )
    original = draft.points

    dense = draft.preview_samples(max_step_y_px=1.0)
    sparse = draft.preview_samples(max_step_y_px=20.0)

    assert len(dense) > len(sparse)
    assert draft.points == original


def test_invalid_preview_step_is_rejected() -> None:
    with pytest.raises(ManualTraceError, match="greater than zero"):
        sample_pchip_trace(
            (TracePoint(10.0, 0.0), TracePoint(20.0, 10.0)),
            max_step_y_px=0.0,
        )
