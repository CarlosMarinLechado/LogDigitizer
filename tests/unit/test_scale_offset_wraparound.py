from __future__ import annotations

import pytest

from logdigitizer.domain.calibration import (
    CalibrationError,
    HorizontalCalibration,
    ScaleType,
)
from logdigitizer.domain.digitization import (
    DigitizationError,
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.processing.calibration import (
    CalibrationRangeError,
    linear_transition_value,
    pixel_x_to_value,
)


def test_scale_offset_round_trip_and_legacy_default() -> None:
    manual = TraceSection.create_manual(
        (TracePoint(10.0, 10.0), TracePoint(20.0, 20.0)),
        scale_offset=1,
    )
    automatic = TraceSection.create_automatic(
        raw_samples=(TracePoint(30.0, 30.0), TracePoint(40.0, 40.0)),
        scale_offset=-2,
    )

    restored = DigitizedCurve.from_dict(
        DigitizedCurve("curve-gr", [manual, automatic]).to_dict()
    )
    assert [section.scale_offset for section in restored.sections] == [1, -2]

    legacy = manual.to_dict()
    legacy.pop("scale_offset")
    assert TraceSection.from_dict(legacy).scale_offset == 0


@pytest.mark.parametrize("invalid", [True, 1.5, float("inf"), "one"])
def test_scale_offset_must_be_a_finite_integer(invalid) -> None:
    with pytest.raises(DigitizationError, match="integer"):
        TraceSection.create_manual(
            (TracePoint(10.0, 10.0), TracePoint(20.0, 20.0)),
            scale_offset=invalid,
        )


def test_override_with_different_offsets_keeps_image_boundaries_separate() -> None:
    raw = tuple(TracePoint(100.0 + y, y) for y in range(0, 11))
    automatic = TraceSection.create_automatic(
        raw_samples=raw,
        scale_offset=0,
    )
    backup = TraceSection.create_manual(
        (
            TracePoint(10.0, 4.0),
            TracePoint(12.0, 5.0),
            TracePoint(14.0, 6.0),
        ),
        scale_offset=1,
    )
    curve = DigitizedCurve("curve-gr", [automatic])

    curve.apply_manual_override(backup)

    assert [section.source for section in curve.sections] == [
        TraceSource.AUTOMATIC,
        TraceSource.MANUAL,
        TraceSource.AUTOMATIC,
    ]
    upper, saved_backup, lower = curve.sections
    assert upper.scale_offset == 0
    assert saved_backup.scale_offset == 1
    assert lower.scale_offset == 0
    assert upper.raw_samples_px[-1].y_px < saved_backup.start_y_px
    assert lower.raw_samples_px[0].y_px > saved_backup.end_y_px
    assert upper.raw_samples_px[-1].x_px != saved_backup.control_points_px[0].x_px
    assert lower.raw_samples_px[0].x_px != saved_backup.control_points_px[-1].x_px
    assert saved_backup.control_points_px[0] not in upper.raw_samples_px
    assert saved_backup.control_points_px[-1] not in lower.raw_samples_px


def test_different_offsets_cannot_share_the_same_y() -> None:
    normal = TraceSection.create_manual(
        (TracePoint(90.0, 0.0), TracePoint(110.0, 10.0)),
        scale_offset=0,
    )
    backup = TraceSection.create_manual(
        (TracePoint(110.0, 10.0), TracePoint(25.0, 20.0)),
        scale_offset=1,
    )
    curve = DigitizedCurve("curve-gr", [normal])

    with pytest.raises(DigitizationError, match="same Y"):
        curve.add_section(backup)


def test_automatic_override_preserves_adjacent_different_offset_sections() -> None:
    normal = TraceSection.create_automatic(
        raw_samples=tuple(TracePoint(90.0 + y, y) for y in range(5)),
        scale_offset=0,
    )
    backup = TraceSection.create_automatic(
        raw_samples=tuple(TracePoint(10.0 + y, y) for y in range(5, 10)),
        scale_offset=1,
    )
    curve = DigitizedCurve("curve-gr", [normal])

    curve.apply_automatic_override(backup)

    assert len(curve.sections) == 2
    assert [section.scale_offset for section in curve.sections] == [0, 1]
    assert curve.sections[0].raw_samples_px[-1].y_px == 4.0
    assert curve.sections[1].raw_samples_px[0].y_px == 5.0


def test_saved_scale_offset_lookup_is_unambiguous_by_image_y() -> None:
    normal = TraceSection.create_manual(
        (TracePoint(90.0, 0.0), TracePoint(110.0, 10.0)),
        scale_offset=0,
    )
    backup = TraceSection.create_manual(
        (TracePoint(10.0, 11.0), TracePoint(25.0, 20.0)),
        scale_offset=1,
    )
    curve = DigitizedCurve("curve-gr", [normal, backup])

    assert curve.scale_offset_at_y(5.0) == 0
    assert curve.scale_offset_at_y(15.0) == 1
    assert curve.scale_offset_at_y(10.5) is None


def test_linear_pixel_conversion_applies_complete_scale_width() -> None:
    increasing = HorizontalCalibration(
        pixel_x1=10.0,
        value1=0.0,
        pixel_x2=110.0,
        value2=150.0,
        scale_type=ScaleType.LINEAR,
    )
    decreasing = HorizontalCalibration(
        pixel_x1=10.0,
        value1=150.0,
        pixel_x2=110.0,
        value2=0.0,
        scale_type=ScaleType.LINEAR,
    )

    assert pixel_x_to_value(10.0, increasing, scale_offset=1) == pytest.approx(
        150.0
    )
    assert pixel_x_to_value(60.0, increasing, scale_offset=1) == pytest.approx(
        225.0
    )
    assert pixel_x_to_value(60.0, increasing, scale_offset=-1) == pytest.approx(
        -75.0
    )
    # +1 always means one numerically higher scale, even when X is reversed.
    assert pixel_x_to_value(110.0, decreasing, scale_offset=1) == pytest.approx(
        150.0
    )


def test_logarithmic_scale_offset_is_explicitly_not_implemented() -> None:
    calibration = HorizontalCalibration(
        pixel_x1=10.0,
        value1=1.0,
        pixel_x2=110.0,
        value2=1000.0,
        scale_type=ScaleType.LOGARITHMIC,
    )

    assert pixel_x_to_value(10.0, calibration) == pytest.approx(1.0)
    with pytest.raises(CalibrationError, match="only for linear"):
        pixel_x_to_value(10.0, calibration, scale_offset=1)


def test_linear_transition_uses_already_offset_physical_values() -> None:
    assert linear_transition_value(
        100.5,
        start_depth=100.0,
        start_value=149.0,
        end_depth=101.0,
        end_value=151.0,
    ) == pytest.approx(150.0)

    with pytest.raises(CalibrationRangeError, match="outside"):
        linear_transition_value(
            102.0,
            start_depth=100.0,
            start_value=149.0,
            end_depth=101.0,
            end_value=151.0,
        )
