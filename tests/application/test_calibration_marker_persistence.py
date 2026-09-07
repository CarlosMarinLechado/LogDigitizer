from __future__ import annotations

from pathlib import Path

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.segment import Segment


def _add_segment(service: ProjectService, *, order: int, name: str) -> Segment:
    assert service.project is not None
    segment = Segment.create(
        order=order,
        name=name,
        source_path=f"source/{name}",
        checksum=str(order) * 64,
    )
    service.project.add_segment(segment)
    service.active_segment_id = segment.segment_id
    return segment


def test_calibration_marker_ui_positions_round_trip_in_logproj(
    tmp_path: Path,
) -> None:
    service = ProjectService()
    service.new_project(
        name="MarkerState",
        project_directory=tmp_path / "marker_state_project",
    )
    segment = _add_segment(service, order=1, name="image01.png")

    assert service.set_depth_calibration_marker_positions(
        {100.0: 321.125, 200.0: 319.875},
        segment,
    )
    assert service.set_horizontal_calibration_marker_positions(
        "curve-gr",
        {0.0: 412.25, 100.0: 411.75},
        segment,
        scale_id="default",
    )
    assert service.dirty is True

    project_file = service.paths.project_file
    service.save()
    assert service.dirty is False

    reopened = ProjectService()
    reopened.open_project(project_file)
    reopened_segment = reopened.active_segment
    assert reopened_segment is not None

    assert reopened.get_depth_calibration_marker_positions(
        reopened_segment
    ) == {
        100.0: 321.125,
        200.0: 319.875,
    }
    assert reopened.get_horizontal_calibration_marker_positions(
        "curve-gr",
        reopened_segment,
        scale_id="default",
    ) == {
        0.0: 412.25,
        100.0: 411.75,
    }

    # The state is explicitly presentation metadata, not scientific
    # calibration payload stored under segment.extensions["calibration"].
    calibration = reopened_segment.extensions.get("calibration", {})
    assert "ui_state" not in calibration
    assert "ui_state" in reopened.project.metadata


def test_marker_positions_are_isolated_by_image_curve_and_scale(
    tmp_path: Path,
) -> None:
    service = ProjectService()
    service.new_project(
        name="MarkerIsolation",
        project_directory=tmp_path / "marker_isolation_project",
    )
    first = _add_segment(service, order=1, name="image01.png")
    second = _add_segment(service, order=2, name="image02.png")

    service.set_depth_calibration_marker_positions({100.0: 120.0}, first)
    service.set_depth_calibration_marker_positions({100.0: 620.0}, second)

    service.set_horizontal_calibration_marker_positions(
        "curve-gr",
        {0.0: 200.0},
        first,
        scale_id="default",
    )
    service.set_horizontal_calibration_marker_positions(
        "curve-gr",
        {0.0: 300.0},
        first,
        scale_id="secondary",
    )
    service.set_horizontal_calibration_marker_positions(
        "curve-sp",
        {-80.0: 400.0},
        first,
        scale_id="default",
    )

    assert service.get_depth_calibration_marker_positions(first) == {
        100.0: 120.0
    }
    assert service.get_depth_calibration_marker_positions(second) == {
        100.0: 620.0
    }
    assert service.get_horizontal_calibration_marker_positions(
        "curve-gr",
        first,
        scale_id="default",
    ) == {0.0: 200.0}
    assert service.get_horizontal_calibration_marker_positions(
        "curve-gr",
        first,
        scale_id="secondary",
    ) == {0.0: 300.0}
    assert service.get_horizontal_calibration_marker_positions(
        "curve-sp",
        first,
        scale_id="default",
    ) == {-80.0: 400.0}


def test_setting_same_marker_positions_does_not_dirty_project_again(
    tmp_path: Path,
) -> None:
    service = ProjectService()
    service.new_project(
        name="MarkerDirty",
        project_directory=tmp_path / "marker_dirty_project",
    )
    segment = _add_segment(service, order=1, name="image01.png")
    service.set_depth_calibration_marker_positions({100.0: 222.5}, segment)
    service.save()

    assert service.dirty is False
    assert (
        service.set_depth_calibration_marker_positions(
            {100.0: 222.5},
            segment,
        )
        is False
    )
    assert service.dirty is False
