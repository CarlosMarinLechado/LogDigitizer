from __future__ import annotations

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.states import (
    CurveCalibrationReadiness,
    StageStatus,
)


def make_service(tmp_path: Path, count: int = 1):
    images = []
    for index in range(count):
        image = tmp_path / f"segment_{index + 1}.png"
        PIL.new("L", (300, 500), 128).save(image)
        images.append(image)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segments = service.add_images(images)
    return service, segments


def add_gr(service: ProjectService):
    return service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )


def calibrate_depth(service: ProjectService) -> None:
    service.add_depth_calibration_point(pixel_y=20.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=420.0, depth=200.0)


def calibrate_gr(service: ProjectService, curve_id: str) -> None:
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=30.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=260.0,
        value=150.0,
    )


def test_curve_presence_defaults_to_true_for_existing_projects(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    curve = add_gr(service)

    assert service.is_curve_present(curve.curve_id) is True
    calibration = service.active_segment.extensions.get("calibration", {})
    assert "curve_presence" not in calibration


def test_not_present_is_persisted_per_segment_and_survives_reload(
    tmp_path: Path,
) -> None:
    service, segments = make_service(tmp_path, count=2)
    curve = add_gr(service)

    service.select_segment(segments[0].segment_id)
    assert service.set_curve_present(curve.curve_id, False) is True
    assert service.is_curve_present(curve.curve_id) is False

    service.select_segment(segments[1].segment_id)
    assert service.is_curve_present(curve.curve_id) is True

    service.save()
    reopened = ProjectService()
    reopened.open_project(service.paths.project_file)
    restored_curve = reopened.get_curves()[0]

    reopened.select_segment(reopened.project.segments[0].segment_id)
    assert reopened.is_curve_present(restored_curve.curve_id) is False
    reopened.select_segment(reopened.project.segments[1].segment_id)
    assert reopened.is_curve_present(restored_curve.curve_id) is True


def test_marking_curve_absent_is_non_destructive_and_reversible(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    curve = add_gr(service)
    calibrate_depth(service)
    calibrate_gr(service, curve.curve_id)

    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.READY
    )
    saved_points = service.get_horizontal_calibration_points(curve.curve_id)

    service.set_curve_present(curve.curve_id, False)
    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.NOT_PRESENT
    )
    assert service.get_horizontal_calibration_points(curve.curve_id) == saved_points

    service.set_curve_present(curve.curve_id, True)
    assert service.get_horizontal_calibration_points(curve.curve_id) == saved_points
    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.READY
    )


def test_readiness_is_evaluated_per_segment_and_curve(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    curve = add_gr(service)

    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.DEPTH_NOT_CALIBRATED
    )

    calibrate_depth(service)
    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.X_NOT_CALIBRATED
    )

    calibrate_gr(service, curve.curve_id)
    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.READY
    )

    service.set_curve_present(curve.curve_id, False)
    assert (
        service.get_curve_calibration_readiness(curve.curve_id)
        is CurveCalibrationReadiness.NOT_PRESENT
    )


def test_absent_curve_is_not_required_for_segment_complete_status(
    tmp_path: Path,
) -> None:
    service, segments = make_service(tmp_path)
    segment = segments[0]
    gr = add_gr(service)
    sp = service.add_curve(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        scale_type=ScaleType.LINEAR,
    )

    calibrate_depth(service)
    calibrate_gr(service, gr.curve_id)
    assert segment.calibration_status is StageStatus.IN_PROGRESS

    service.set_curve_present(sp.curve_id, False)
    assert segment.calibration_status is StageStatus.COMPLETE
    assert (
        service.get_curve_calibration_readiness(gr.curve_id)
        is CurveCalibrationReadiness.READY
    )
    assert (
        service.get_curve_calibration_readiness(sp.curve_id)
        is CurveCalibrationReadiness.NOT_PRESENT
    )


def test_future_digitization_warning_is_specific_not_generic(tmp_path: Path) -> None:
    service, segments = make_service(tmp_path)
    curve = add_gr(service)
    segment = segments[0]
    calibrate_depth(service)

    reason = service.curve_digitization_block_reason(curve.curve_id)
    assert reason is not None
    assert "GR is not calibrated in Segment 01" in reason
    assert "Calibrate the curve before digitizing this segment" in reason

    service.set_curve_present(curve.curve_id, False)
    reason = service.curve_digitization_block_reason(curve.curve_id)
    assert reason == "GR is marked as not present in Segment 01."

    service.set_curve_present(curve.curve_id, True)
    calibrate_gr(service, curve.curve_id)
    assert service.curve_digitization_block_reason(curve.curve_id) is None


def test_repeating_same_presence_value_does_not_dirty_project(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    curve = add_gr(service)
    service.save()
    assert service.dirty is False

    changed = service.set_curve_present(curve.curve_id, True)
    assert changed is False
    assert service.dirty is False
