from __future__ import annotations

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import (
    CalibrationError,
    DEFAULT_HORIZONTAL_SCALE_ID,
    ScaleType,
)
from logdigitizer.domain.digitization import (
    DigitizationError,
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.processing.calibration import pixel_x_to_value


def make_service(tmp_path: Path) -> tuple[ProjectService, str]:
    image = tmp_path / "segment.png"
    PIL.new("L", (300, 500), 128).save(image)
    service = ProjectService()
    service.new_project(
        name="Dual scale",
        project_directory=tmp_path / "project",
    )
    service.add_images([image])
    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    return service, curve.curve_id


def calibrate_default(service: ProjectService, curve_id: str) -> None:
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=110.0,
        value=100.0,
    )


def test_additional_scale_preserves_legacy_default_payload_and_round_trips(
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    calibrate_default(service, curve_id)

    legacy = service.active_segment.extensions["calibration"]["horizontal"][
        curve_id
    ]
    assert legacy == {
        "points": [
            {"pixel_x": 10.0, "value": 0.0},
            {"pixel_x": 110.0, "value": 100.0},
        ]
    }

    extended = service.add_horizontal_scale(curve_id)
    assert extended.scale_id != DEFAULT_HORIZONTAL_SCALE_ID
    assert extended.points == ()
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=10.0,
        value=0.0,
        scale_id=extended.scale_id,
    )
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=110.0,
        value=1000.0,
        scale_id=extended.scale_id,
    )

    curve = service.get_curve(curve_id)
    profiles = service.get_horizontal_scale_profiles(curve_id)
    assert [profile.display_name(curve) for profile in profiles] == [
        "GR: 0–100 API",
        "GR: 0–1000 API",
    ]
    fine = service.get_horizontal_calibration(
        curve_id,
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    wide = service.get_horizontal_calibration(
        curve_id,
        scale_id=extended.scale_id,
    )
    assert fine is not None and wide is not None
    assert pixel_x_to_value(60.0, fine) == pytest.approx(50.0)
    assert pixel_x_to_value(60.0, wide) == pytest.approx(500.0)

    service.save()
    reopened = ProjectService()
    reopened.open_project(service.paths.project_file)
    restored = reopened.get_horizontal_scale_profiles(curve_id)
    assert [(item.scale_id, item.points) for item in restored] == [
        (item.scale_id, item.points) for item in profiles
    ]


def test_new_scale_requires_all_existing_scales_to_be_complete(
    tmp_path: Path,
) -> None:
    service, curve_id = make_service(tmp_path)
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=10.0,
        value=0.0,
    )

    with pytest.raises(CalibrationError, match="Complete"):
        service.add_horizontal_scale(curve_id)


def test_section_scale_id_round_trip_and_legacy_default() -> None:
    section = TraceSection.create_manual(
        (TracePoint(10.0, 10.0), TracePoint(20.0, 20.0)),
        scale_id="wide-scale",
        scale_offset=1,
    )
    restored = TraceSection.from_dict(section.to_dict())
    assert (restored.scale_id, restored.scale_offset) == ("wide-scale", 1)

    legacy = section.to_dict()
    legacy.pop("scale_id")
    assert TraceSection.from_dict(legacy).scale_id == DEFAULT_HORIZONTAL_SCALE_ID


def test_different_scales_cannot_share_an_image_y() -> None:
    fine = TraceSection.create_manual(
        (TracePoint(20.0, 0.0), TracePoint(30.0, 10.0)),
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    wide = TraceSection.create_manual(
        (TracePoint(30.0, 10.0), TracePoint(40.0, 20.0)),
        scale_id="wide-scale",
    )
    digitized = DigitizedCurve("curve-gr", [fine])

    with pytest.raises(DigitizationError, match="same Y"):
        digitized.add_section(wide)


def test_used_additional_scale_cannot_be_removed(tmp_path: Path) -> None:
    service, curve_id = make_service(tmp_path)
    calibrate_default(service, curve_id)
    wide = service.add_horizontal_scale(curve_id)
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=10.0,
        value=0.0,
        scale_id=wide.scale_id,
    )
    service.add_horizontal_calibration_point(
        curve_id,
        pixel_x=110.0,
        value=1000.0,
        scale_id=wide.scale_id,
    )
    section = TraceSection.create_manual(
        (TracePoint(20.0, 20.0), TracePoint(30.0, 40.0)),
        scale_id=wide.scale_id,
    )
    service.set_digitized_curve(DigitizedCurve(curve_id, [section]))

    with pytest.raises(CalibrationError, match="used"):
        service.remove_horizontal_scale(curve_id, wide.scale_id)

