from __future__ import annotations

import json
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CalibrationError, DepthUnit
from logdigitizer.domain.states import StageStatus


def make_service(tmp_path: Path) -> tuple[ProjectService, object]:
    image = tmp_path / "segment.png"
    PIL.new("L", (120, 240), 128).save(image)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segment = service.add_images([image])[0]
    return service, segment


def test_one_point_is_persisted_as_draft_and_survives_reload(tmp_path: Path) -> None:
    service, segment = make_service(tmp_path)

    points = service.add_depth_calibration_point(
        pixel_y=100.0,
        depth=0.0,
    )

    assert len(points) == 1
    assert service.get_depth_calibration() is None
    assert segment.calibration_status is StageStatus.IN_PROGRESS

    payload = segment.extensions["calibration"]["depth"]
    assert payload["unit"] == "M"
    assert payload["points"] == [{"pixel_y": 100.0, "depth": 0.0}]
    assert "pixel_x" not in json.dumps(payload)
    json.dumps(segment.extensions, ensure_ascii=False)

    service.save()
    project_file = service.paths.project_file

    reopened = ProjectService()
    reopened.open_project(project_file)

    restored = reopened.get_depth_calibration_points()
    assert len(restored) == 1
    assert restored[0].pixel_y == pytest.approx(100.0)
    assert restored[0].depth == pytest.approx(0.0)
    assert reopened.get_depth_calibration() is None


def test_second_point_activates_scientific_depth_calibration(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    service.add_depth_calibration_point(pixel_y=100.0, depth=200.0)
    service.add_depth_calibration_point(pixel_y=300.0, depth=220.0)

    calibration = service.get_depth_calibration()
    assert calibration is not None
    assert calibration.unit is DepthUnit.METERS
    assert [point.pixel_y for point in calibration.points] == [100.0, 300.0]
    assert [point.depth for point in calibration.points] == [200.0, 220.0]


def test_invalid_move_is_atomic_and_does_not_replace_persisted_points(
    tmp_path: Path,
) -> None:
    service, segment = make_service(tmp_path)
    service.add_depth_calibration_point(pixel_y=100.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=200.0, depth=20.0)
    service.add_depth_calibration_point(pixel_y=300.0, depth=40.0)

    before = json.loads(json.dumps(segment.extensions["calibration"]["depth"]))

    # Moving the middle control below the 40 m control would make depth non-monotonic
    # once points are sorted by pixel_y, so the domain must reject it.
    with pytest.raises(CalibrationError):
        service.update_depth_calibration_point(
            1,
            pixel_y=350.0,
        )

    assert segment.extensions["calibration"]["depth"] == before


def test_unit_is_selected_once_until_points_are_cleared(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    service.set_depth_calibration_unit(DepthUnit.FEET)
    assert service.get_depth_calibration_unit() is DepthUnit.FEET

    service.add_depth_calibration_point(pixel_y=100.0, depth=500.0)

    with pytest.raises(CalibrationError, match="Clear existing depth points"):
        service.set_depth_calibration_unit(DepthUnit.METERS)

    service.clear_depth_calibration_points()
    assert service.get_depth_calibration_points() == ()
    service.set_depth_calibration_unit(DepthUnit.METERS)
    assert service.get_depth_calibration_unit() is DepthUnit.METERS


def test_calibration_change_marks_existing_digitization_for_review(
    tmp_path: Path,
) -> None:
    service, segment = make_service(tmp_path)
    segment.digitization_status = StageStatus.COMPLETE

    service.add_depth_calibration_point(pixel_y=100.0, depth=0.0)

    assert segment.digitization_status is StageStatus.REVIEW_REQUIRED


def test_delete_back_to_one_point_returns_to_draft_state(tmp_path: Path) -> None:
    service, segment = make_service(tmp_path)
    service.add_depth_calibration_point(pixel_y=100.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=300.0, depth=20.0)

    remaining = service.remove_depth_calibration_point(1)

    assert len(remaining) == 1
    assert service.get_depth_calibration() is None
    assert segment.calibration_status is StageStatus.IN_PROGRESS
    assert len(segment.extensions["calibration"]["depth"]["points"]) == 1
