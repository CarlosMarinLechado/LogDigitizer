from __future__ import annotations

import json
import math

import pytest

from logdigitizer.domain.calibration import (
    CalibrationError,
    CalibrationModel,
    CurveDefinition,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
    ScaleType,
)
from logdigitizer.processing.calibration import (
    CalibrationRangeError,
    depth_to_pixel_y,
    is_pixel_y_digitizable,
    pixel_x_to_value,
    pixel_y_to_depth,
    value_to_pixel_x,
)


def test_depth_piecewise_linear_and_sorting():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(300.0, 120.0),
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(200.0, 40.0),
        )
    )

    assert [p.pixel_y for p in calibration.points] == [
        100.0,
        200.0,
        300.0,
    ]
    assert pixel_y_to_depth(150.0, calibration) == pytest.approx(20.0)
    assert pixel_y_to_depth(250.0, calibration) == pytest.approx(80.0)


def test_depth_inverse_round_trip_inside_controlled_range():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(250.0, 60.0),
            DepthCalibrationPoint(500.0, 200.0),
        )
    )

    for pixel_y in (100.0, 175.0, 250.0, 400.0, 500.0):
        depth = pixel_y_to_depth(pixel_y, calibration)
        restored = depth_to_pixel_y(depth, calibration)
        assert restored == pytest.approx(pixel_y)


def test_first_point_at_zero_forbids_digitizing_above_it():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(300.0, 100.0),
        )
    )

    assert is_pixel_y_digitizable(100.0, calibration) is True
    assert is_pixel_y_digitizable(99.999, calibration) is False

    with pytest.raises(
        CalibrationRangeError,
        match="negative depth",
    ):
        pixel_y_to_depth(99.0, calibration)


def test_upper_extrapolation_is_allowed_when_depth_stays_non_negative():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 200.0),
            DepthCalibrationPoint(300.0, 220.0),
        )
    )

    assert pixel_y_to_depth(50.0, calibration) == pytest.approx(195.0)
    assert pixel_y_to_depth(0.0, calibration) == pytest.approx(190.0)
    assert is_pixel_y_digitizable(0.0, calibration) is True


def test_upper_extrapolation_can_reach_exactly_zero_depth():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 20.0),
            DepthCalibrationPoint(300.0, 40.0),
        )
    )

    assert pixel_y_to_depth(-100.0, calibration) == pytest.approx(0.0)
    assert is_pixel_y_digitizable(-100.0, calibration) is True

    with pytest.raises(CalibrationRangeError):
        pixel_y_to_depth(-101.0, calibration)

    assert is_pixel_y_digitizable(-101.0, calibration) is False


def test_below_last_point_uses_last_segment_extrapolation():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 0.0),
            DepthCalibrationPoint(300.0, 80.0),
            DepthCalibrationPoint(500.0, 140.0),
        )
    )

    # Last segment: 60 m / 200 px = 0.3 m/px.
    assert pixel_y_to_depth(600.0, calibration) == pytest.approx(170.0)
    assert depth_to_pixel_y(170.0, calibration) == pytest.approx(600.0)
    assert is_pixel_y_digitizable(600.0, calibration) is True


def test_segment_starting_at_200_m_can_digitize_above_first_point():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 200.0),
            DepthCalibrationPoint(300.0, 220.0),
            DepthCalibrationPoint(500.0, 250.0),
        )
    )

    assert pixel_y_to_depth(50.0, calibration) == pytest.approx(195.0)
    assert pixel_y_to_depth(0.0, calibration) == pytest.approx(190.0)
    assert pixel_y_to_depth(600.0, calibration) == pytest.approx(265.0)


def test_depth_to_pixel_y_supports_both_extrapolation_sides():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 20.0),
            DepthCalibrationPoint(300.0, 40.0),
            DepthCalibrationPoint(500.0, 80.0),
        )
    )

    assert depth_to_pixel_y(10.0, calibration) == pytest.approx(0.0)
    assert depth_to_pixel_y(100.0, calibration) == pytest.approx(600.0)
    assert depth_to_pixel_y(0.0, calibration) == pytest.approx(-100.0)

    with pytest.raises(CalibrationRangeError):
        depth_to_pixel_y(-0.01, calibration)


def test_depth_can_decrease_with_y_and_use_feet():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 2000.0),
            DepthCalibrationPoint(100.0, 1900.0),
            DepthCalibrationPoint(200.0, 1750.0),
        ),
        unit=DepthUnit.FEET,
    )

    assert calibration.depth_increases_with_y is False
    assert pixel_y_to_depth(150.0, calibration) == pytest.approx(1825.0)

    # Extrapolation after the last pixel keeps the final slope.
    assert pixel_y_to_depth(300.0, calibration) == pytest.approx(1600.0)
    assert depth_to_pixel_y(1600.0, calibration) == pytest.approx(300.0)

    # Extrapolation before the first point uses the first slope.
    assert pixel_y_to_depth(-100.0, calibration) == pytest.approx(2100.0)


def test_decreasing_depth_extrapolation_still_rejects_negative_values():
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 20.0),
            DepthCalibrationPoint(100.0, 10.0),
        )
    )

    assert pixel_y_to_depth(200.0, calibration) == pytest.approx(0.0)

    with pytest.raises(CalibrationRangeError):
        pixel_y_to_depth(201.0, calibration)


def test_depth_validation_rules():
    with pytest.raises(CalibrationError, match="at least two"):
        DepthCalibration(
            points=(DepthCalibrationPoint(0.0, 0.0),)
        )

    with pytest.raises(CalibrationError, match="duplicate pixel_y"):
        DepthCalibration(
            points=(
                DepthCalibrationPoint(0.0, 0.0),
                DepthCalibrationPoint(0.0, 100.0),
            )
        )

    with pytest.raises(CalibrationError, match="strictly monotonic"):
        DepthCalibration(
            points=(
                DepthCalibrationPoint(0.0, 0.0),
                DepthCalibrationPoint(100.0, 120.0),
                DepthCalibrationPoint(200.0, 110.0),
            )
        )

    with pytest.raises(CalibrationError, match="negative depths"):
        DepthCalibrationPoint(100.0, -1.0)


def test_depth_unit_is_restricted_to_m_or_ft():
    meters = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 10.0),
        ),
        unit="M",
    )
    feet = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 10.0),
        ),
        unit="FT",
    )

    assert meters.unit is DepthUnit.METERS
    assert feet.unit is DepthUnit.FEET

    with pytest.raises(ValueError):
        DepthCalibration(
            points=(
                DepthCalibrationPoint(0.0, 0.0),
                DepthCalibrationPoint(100.0, 10.0),
            ),
            unit="CM",
        )


def test_linear_and_inverted_horizontal_calibration():
    calibration = HorizontalCalibration(
        100.0,
        0.0,
        500.0,
        200.0,
    )

    assert pixel_x_to_value(300.0, calibration) == pytest.approx(100.0)
    assert value_to_pixel_x(100.0, calibration) == pytest.approx(300.0)

    inverted = HorizontalCalibration(
        100.0,
        200.0,
        500.0,
        0.0,
    )

    assert pixel_x_to_value(300.0, inverted) == pytest.approx(100.0)


def test_logarithmic_horizontal_calibration_and_inverse():
    calibration = HorizontalCalibration(
        100.0,
        0.2,
        700.0,
        200.0,
        ScaleType.LOGARITHMIC,
    )

    expected = math.sqrt(40.0)

    assert pixel_x_to_value(400.0, calibration) == pytest.approx(expected)
    assert value_to_pixel_x(expected, calibration) == pytest.approx(400.0)

    inverted = HorizontalCalibration(
        100.0,
        200.0,
        700.0,
        0.2,
        ScaleType.LOGARITHMIC,
    )

    assert pixel_x_to_value(400.0, inverted) == pytest.approx(expected)


def test_horizontal_validation():
    with pytest.raises(CalibrationError):
        HorizontalCalibration(10.0, 1.0, 10.0, 2.0)

    with pytest.raises(CalibrationError):
        HorizontalCalibration(10.0, 2.0, 20.0, 2.0)

    with pytest.raises(CalibrationError):
        HorizontalCalibration(
            0.0,
            0.0,
            100.0,
            10.0,
            ScaleType.LOGARITHMIC,
        )


def test_model_json_round_trip_preserves_depth_policy_without_tracks():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(100.0, 20.0),
            DepthCalibrationPoint(300.0, 100.0),
        )
    )

    curve = CurveDefinition(
        "curve-1",
        "Deep Resistivity",
        "ILD",
        "OHMM",
        ScaleType.LOGARITHMIC,
        "Deep formation resistivity",
    )

    horizontal = HorizontalCalibration(
        420.0,
        0.2,
        880.0,
        200.0,
        ScaleType.LOGARITHMIC,
    )

    model = CalibrationModel(
        depth=depth,
        curves=(curve,),
        horizontal_calibrations={
            "curve-1": horizontal,
        },
    )

    payload = model.to_dict()
    json.dumps(payload, ensure_ascii=False)

    assert payload["depth"]["extrapolation"] == {
        "before_first_pixel_y": "first_segment_linear",
        "after_last_pixel_y": "last_segment_linear",
        "negative_depth": "forbidden",
    }
    assert "tracks" not in payload
    assert "track_id" not in json.dumps(payload)
    assert CalibrationModel.from_dict(payload) == model
    assert model.is_complete() is True


def test_curve_definition_is_project_level_and_has_scale_type():
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        description="Natural gamma ray",
    )

    payload = curve.to_dict()
    assert "track_id" not in payload
    assert payload["mnemonic"] == "GR"
    assert payload["scale_type"] == "linear"
    assert CurveDefinition.from_dict(payload) == curve


def test_model_rejects_horizontal_calibration_for_unknown_curve():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 100.0),
        )
    )

    with pytest.raises(CalibrationError, match="unknown curve"):
        CalibrationModel(
            depth=depth,
            horizontal_calibrations={
                "missing": HorizontalCalibration(0.0, 0.0, 100.0, 10.0)
            },
        )
