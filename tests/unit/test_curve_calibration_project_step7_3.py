from __future__ import annotations

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CalibrationError, ScaleType
from logdigitizer.domain.states import StageStatus
from logdigitizer.processing.calibration import pixel_x_to_value


def make_service(tmp_path: Path, count: int = 2):
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


def add_resistivity_curve(service: ProjectService):
    return service.add_curve(
        name="Electrical Resistivity",
        mnemonic="ILD",
        unit="OHMM",
        scale_type=ScaleType.LOGARITHMIC,
        description="Deep resistivity",
    )


def test_curve_is_defined_once_globally_but_x_calibration_is_per_segment(
    tmp_path: Path,
) -> None:
    service, segments = make_service(tmp_path)
    curve = add_resistivity_curve(service)

    assert service.get_curves() == (curve,)
    assert "track_id" not in curve.to_dict()

    service.select_segment(segments[0].segment_id)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=40.0,
        value=0.2,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=240.0,
        value=200.0,
    )

    first = service.get_horizontal_calibration(curve.curve_id)
    assert first is not None

    service.select_segment(segments[1].segment_id)
    assert service.get_horizontal_calibration_points(curve.curve_id) == ()
    assert service.get_horizontal_calibration(curve.curve_id) is None

    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=55.0,
        value=2.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=260.0,
        value=2000.0,
    )
    second = service.get_horizontal_calibration(curve.curve_id)
    assert second is not None

    assert first.pixel_x1 == pytest.approx(40.0)
    assert second.pixel_x1 == pytest.approx(55.0)
    assert first.value1 == pytest.approx(0.2)
    assert second.value1 == pytest.approx(2.0)


def test_one_x_point_is_persisted_as_segment_specific_draft(tmp_path: Path) -> None:
    service, segments = make_service(tmp_path)
    curve = add_resistivity_curve(service)
    service.select_segment(segments[0].segment_id)

    points = service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=50.0,
        value=0.2,
    )
    assert len(points) == 1
    assert service.get_horizontal_calibration(curve.curve_id) is None

    payload = segments[0].extensions["calibration"]["horizontal"][curve.curve_id]
    assert payload == {
        "points": [{"pixel_x": 50.0, "value": 0.2}]
    }
    assert "pixel_y" not in str(payload)

    service.save()
    reopened = ProjectService()
    reopened.open_project(service.paths.project_file)
    restored_curve = reopened.get_curves()[0]
    restored_points = reopened.get_horizontal_calibration_points(
        restored_curve.curve_id
    )
    assert len(restored_points) == 1
    assert restored_points[0].pixel_x == pytest.approx(50.0)
    assert restored_points[0].value == pytest.approx(0.2)


def test_complete_project_and_segment_curve_calibrations_survive_reload(
    tmp_path: Path,
) -> None:
    service, segments = make_service(tmp_path)
    curve = add_resistivity_curve(service)

    for index, segment in enumerate(segments):
        service.select_segment(segment.segment_id)
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=40.0 + 10 * index,
            value=0.2,
        )
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=240.0 + 10 * index,
            value=200.0,
        )

    service.save()
    project_file = service.paths.project_file

    reopened = ProjectService()
    reopened.open_project(project_file)
    restored_curve = reopened.get_curves()[0]
    assert restored_curve.mnemonic == "ILD"
    assert restored_curve.scale_type is ScaleType.LOGARITHMIC

    first_segment = reopened.project.segments[0]
    second_segment = reopened.project.segments[1]
    reopened.select_segment(first_segment.segment_id)
    first = reopened.get_horizontal_calibration(restored_curve.curve_id)
    reopened.select_segment(second_segment.segment_id)
    second = reopened.get_horizontal_calibration(restored_curve.curve_id)

    assert first is not None and second is not None
    assert first.pixel_x1 == pytest.approx(40.0)
    assert second.pixel_x1 == pytest.approx(50.0)


def test_logarithmic_curve_rejects_non_positive_draft_value(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path, count=1)
    curve = add_resistivity_curve(service)

    with pytest.raises(CalibrationError, match="positive"):
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=50.0,
            value=0.0,
        )

    assert service.get_horizontal_calibration_points(curve.curve_id) == ()


def test_horizontal_calibration_has_exactly_two_points(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path, count=1)
    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=20.0, value=0.0
    )
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=220.0, value=150.0
    )

    with pytest.raises(CalibrationError, match="already has two"):
        service.add_horizontal_calibration_point(
            curve.curve_id, pixel_x=150.0, value=75.0
        )


def test_duplicate_curve_mnemonic_is_rejected_case_insensitively(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path, count=1)
    service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )

    with pytest.raises(CalibrationError, match="Duplicate curve mnemonic"):
        service.add_curve(
            name="Another Gamma",
            mnemonic="gr",
            unit="API",
            scale_type=ScaleType.LINEAR,
        )


def test_calibration_status_complete_requires_y_and_every_project_curve_x(
    tmp_path: Path,
) -> None:
    service, segments = make_service(tmp_path, count=1)
    segment = segments[0]
    gr = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    sp = service.add_curve(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        scale_type=ScaleType.LINEAR,
    )

    service.add_depth_calibration_point(pixel_y=20.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=420.0, depth=200.0)
    assert segment.calibration_status is StageStatus.IN_PROGRESS

    for curve, v1, v2 in ((gr, 0.0, 150.0), (sp, -100.0, 100.0)):
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=30.0,
            value=v1,
        )
        service.add_horizontal_calibration_point(
            curve.curve_id,
            pixel_x=260.0,
            value=v2,
        )
        if curve is gr:
            assert segment.calibration_status is StageStatus.IN_PROGRESS

    assert segment.calibration_status is StageStatus.COMPLETE


def test_cursor_value_transform_uses_selected_segment_calibration(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path, count=1)
    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=20.0, value=0.0
    )
    service.add_horizontal_calibration_point(
        curve.curve_id, pixel_x=220.0, value=100.0
    )
    calibration = service.get_horizontal_calibration(curve.curve_id)
    assert calibration is not None
    assert pixel_x_to_value(120.0, calibration) == pytest.approx(50.0)


def test_remove_curve_clears_its_x_calibration_from_all_segments(tmp_path: Path) -> None:
    service, segments = make_service(tmp_path)
    curve = add_resistivity_curve(service)
    for segment in segments:
        service.select_segment(segment.segment_id)
        service.add_horizontal_calibration_point(
            curve.curve_id, pixel_x=20.0, value=0.2
        )

    service.remove_curve(curve.curve_id)
    assert service.get_curves() == ()
    for segment in segments:
        horizontal = segment.extensions.get("calibration", {}).get("horizontal", {})
        assert curve.curve_id not in horizontal


def test_project_written_before_step_7_3_without_curves_key_still_opens(
    tmp_path: Path,
) -> None:
    import json

    service, _ = make_service(tmp_path, count=1)
    service.save()
    project_file = service.paths.project_file
    payload = json.loads(project_file.read_text(encoding="utf-8"))
    payload["project"].pop("curves", None)
    project_file.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    reopened = ProjectService()
    reopened.open_project(project_file)
    assert reopened.get_curves() == ()
    assert len(reopened.project.segments) == 1
