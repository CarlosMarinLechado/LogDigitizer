from __future__ import annotations

import pytest

from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
)
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.processing.curve_assembly import (
    CurveAssemblyInput,
    build_project_curve,
)
from logdigitizer.processing.scientific_sampling import (
    DepthSamplingDefinition,
    ScientificSamplingError,
    sample_assembled_project_curves,
)


def _constant_assembly(
    curve_id: str,
    *,
    start_depth: float,
    stop_depth: float,
    value: float,
    unit: DepthUnit = DepthUnit.METERS,
):
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, start_depth),
            DepthCalibrationPoint(100.0, stop_depth),
        ),
        unit=unit,
    )
    source = CurveAssemblyInput(
        segment_id=f"image-{curve_id}",
        segment_name=f"Image {curve_id}",
        depth_calibration=calibration,
        digitized_curve=DigitizedCurve(
            curve_id=curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(value, 0.0),
                        TracePoint(value, 100.0),
                    )
                )
            ],
        ),
        horizontal_calibrations={
            DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                0.0,
                0.0,
                100.0,
                100.0,
            )
        },
    )
    return build_project_curve(curve_id, (source,))


def test_depth_grid_uses_start_plus_index_times_step_without_drift():
    definition = DepthSamplingDefinition(0.0, 1.0, 0.2)

    assert definition.depths() == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def test_non_aligned_stop_is_not_exceeded():
    definition = DepthSamplingDefinition(0.0, 0.95, 0.2)
    sampled = sample_assembled_project_curves(
        {"gr": _constant_assembly(
            "gr",
            start_depth=0.0,
            stop_depth=1.0,
            value=10.0,
        )},
        definition,
    )

    assert sampled.depths == (0.0, 0.2, 0.4, 0.6, 0.8)
    assert sampled.actual_stop_depth == pytest.approx(0.8)


def test_curves_share_one_grid_and_keep_missing_samples_as_none():
    definition = DepthSamplingDefinition(0.0, 1.0, 0.2)
    sampled = sample_assembled_project_curves(
        {
            "gr": _constant_assembly(
                "gr",
                start_depth=0.0,
                stop_depth=1.0,
                value=10.0,
            ),
            "sp": _constant_assembly(
                "sp",
                start_depth=0.4,
                stop_depth=0.8,
                value=20.0,
            ),
        },
        definition,
    )

    assert sampled.depth_unit is DepthUnit.METERS
    assert sampled.curve_ids == ("gr", "sp")
    assert sampled.values_for("gr") == pytest.approx((10.0,) * 6)
    assert sampled.values_for("sp") == (
        None,
        None,
        20.0,
        20.0,
        20.0,
        None,
    )
    assert sampled.rows()[2] == pytest.approx((0.4, 10.0, 20.0))


def test_one_grid_rejects_mixed_depth_units():
    with pytest.raises(ScientificSamplingError, match="one depth unit"):
        sample_assembled_project_curves(
            {
                "gr": _constant_assembly(
                    "gr",
                    start_depth=0.0,
                    stop_depth=1.0,
                    value=10.0,
                ),
                "sp": _constant_assembly(
                    "sp",
                    start_depth=0.0,
                    stop_depth=3.0,
                    value=20.0,
                    unit=DepthUnit.FEET,
                ),
            },
            DepthSamplingDefinition(0.0, 1.0, 0.2),
        )


@pytest.mark.parametrize(
    ("start", "stop", "step"),
    (
        (-1.0, 1.0, 0.2),
        (2.0, 1.0, 0.2),
        (0.0, 1.0, 0.0),
        (0.0, 1.0, -0.2),
        (0.0, float("inf"), 0.2),
    ),
)
def test_invalid_sampling_definition_is_rejected(start, stop, step):
    with pytest.raises(ScientificSamplingError):
        DepthSamplingDefinition(start, stop, step)


def test_unreasonably_large_grid_is_rejected_before_allocation():
    definition = DepthSamplingDefinition(0.0, 1_000_000.0, 0.000001)

    with pytest.raises(ScientificSamplingError, match="too large"):
        definition.depths()
