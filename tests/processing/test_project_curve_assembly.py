from __future__ import annotations

import pytest

from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    DepthCalibration,
    DepthCalibrationPoint,
    HorizontalCalibration,
    ScaleType,
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


def _input(
    segment_id: str,
    name: str,
    image_start: float,
    image_end: float,
    *,
    x_px: float,
    curve_start_y: float = 0.0,
    curve_end_y: float = 100.0,
    curve_points: tuple[TracePoint, ...] | None = None,
) -> CurveAssemblyInput:
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, image_start),
            DepthCalibrationPoint(100.0, image_end),
        )
    )
    digitized = DigitizedCurve(
        curve_id="gr",
        sections=[
            TraceSection.create_manual(
                curve_points
                or (
                    TracePoint(x_px, curve_start_y),
                    TracePoint(x_px, curve_end_y),
                )
            )
        ],
    )
    return CurveAssemblyInput(
        segment_id=segment_id,
        segment_name=name,
        depth_calibration=depth,
        digitized_curve=digitized,
        horizontal_calibrations={
            DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                0.0,
                0.0,
                100.0,
                100.0,
            )
        },
    )


def test_midpoint_join_uses_real_curve_extrema_and_upper_owns_equality():
    upper = _input("image-1", "Image 1", 0.0, 1010.0, x_px=10.0)
    lower = _input("image-2", "Image 2", 990.0, 2000.0, x_px=90.0)

    assembly = build_project_curve("gr", (upper, lower))

    assert len(assembly.joins) == 1
    assert assembly.joins[0].overlap_start_depth == pytest.approx(990.0)
    assert assembly.joins[0].overlap_end_depth == pytest.approx(1010.0)
    assert assembly.joins[0].depth == pytest.approx(1000.0)
    assert assembly.source_at_depth(1000.0).segment_id == "image-1"
    assert assembly.source_at_depth(1000.001).segment_id == "image-2"
    assert assembly.value_at_depth(1000.0) == pytest.approx(10.0)
    assert assembly.value_at_depth(1000.001) == pytest.approx(90.0)


def test_project_explorer_order_does_not_change_assembly_or_values():
    first = _input("image-1", "Image 1", 0.0, 1010.0, x_px=10.0)
    second = _input("image-2", "Image 2", 990.0, 2000.0, x_px=90.0)

    ordered = build_project_curve("gr", (first, second))
    shuffled = build_project_curve("gr", (second, first))

    assert [source.segment_id for source in ordered.sources] == [
        "image-1",
        "image-2",
    ]
    assert ordered.joins == shuffled.joins
    assert ordered.sample((500.0, 1000.0, 1500.0)) == pytest.approx(
        shuffled.sample((500.0, 1000.0, 1500.0))
    )


def test_real_digitized_extrema_can_differ_from_image_interval():
    # Image 1 is calibrated to 1100 m but GR stops at 1010 m (y=91.818...).
    first = _input(
        "image-1",
        "Image 1",
        0.0,
        1100.0,
        x_px=10.0,
        curve_end_y=1010.0 / 11.0,
    )
    # Image 2 starts at 900 m, while its GR digitization begins at 990 m.
    second = _input(
        "image-2",
        "Image 2",
        900.0,
        2000.0,
        x_px=90.0,
        curve_start_y=90.0 / 11.0,
    )

    assembly = build_project_curve("gr", (first, second))

    assert assembly.joins[0].overlap_start_depth == pytest.approx(990.0)
    assert assembly.joins[0].overlap_end_depth == pytest.approx(1010.0)
    assert assembly.joins[0].depth == pytest.approx(1000.0)


def test_gap_is_null_and_is_never_interpolated_between_images():
    first = _input("image-1", "Image 1", 0.0, 990.0, x_px=10.0)
    second = _input("image-2", "Image 2", 1010.0, 2000.0, x_px=90.0)

    assembly = build_project_curve("gr", (second, first))

    assert assembly.joins == ()
    assert len(assembly.gaps) == 1
    assert assembly.gaps[0].start_depth == pytest.approx(990.0)
    assert assembly.gaps[0].end_depth == pytest.approx(1010.0)
    assert assembly.source_at_depth(1000.0) is None
    assert assembly.value_at_depth(1000.0) is None


def test_curve_can_end_while_another_project_curve_continues_elsewhere():
    # Only the images that actually contain GR are supplied to its assembly.
    first = _input("image-1", "Image 1", 0.0, 1100.0, x_px=10.0)
    second = _input(
        "image-2",
        "Image 2",
        990.0,
        2000.0,
        x_px=20.0,
        curve_end_y=(1300.0 - 990.0) / (2000.0 - 990.0) * 100.0,
    )

    assembly = build_project_curve("gr", (first, second))

    assert assembly.maximum_depth == pytest.approx(1300.0)
    assert assembly.value_at_depth(1300.0) == pytest.approx(20.0)
    assert assembly.value_at_depth(1300.1) is None


def test_point_above_zero_depth_is_clipped_only_from_physical_support():
    invalid_boundary_point = TracePoint(10.0, -0.01)
    source = _input(
        "image-1",
        "Image 1",
        0.0,
        100.0,
        x_px=10.0,
        curve_points=(
            invalid_boundary_point,
            TracePoint(10.0, 0.0),
            TracePoint(20.0, 100.0),
        ),
    )

    assembly = build_project_curve("gr", (source,))

    assert assembly.minimum_depth == pytest.approx(0.0)
    assert assembly.maximum_depth == pytest.approx(100.0)
    assert assembly.value_at_depth(0.0) == pytest.approx(10.0)
    assert source.digitized_curve.sections[0].control_points_px[0] == (
        invalid_boundary_point
    )


def test_raw_automatic_trace_is_intersected_as_a_linear_polyline():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 100.0),
        )
    )
    source = CurveAssemblyInput(
        segment_id="image-1",
        segment_name="Image 1",
        depth_calibration=depth,
        digitized_curve=DigitizedCurve(
            curve_id="gr",
            sections=[
                TraceSection.create_automatic(
                    raw_samples=(
                        TracePoint(0.0, 0.0),
                        TracePoint(100.0, 50.0),
                        TracePoint(0.0, 100.0),
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

    assembly = build_project_curve("gr", (source,))

    # At y=25 the straight automatic segment crosses x=50. A PCHIP through
    # the same three samples would cross x=75 and is deliberately not used.
    assert assembly.value_at_depth(25.0) == pytest.approx(50.0)


def test_manual_pchip_is_evaluated_in_pixels_before_log_calibration():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 100.0),
        )
    )
    source = CurveAssemblyInput(
        segment_id="image-1",
        segment_name="Image 1",
        depth_calibration=depth,
        digitized_curve=DigitizedCurve(
            curve_id="gr",
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(0.0, 0.0),
                        TracePoint(100.0, 50.0),
                        TracePoint(0.0, 100.0),
                    )
                )
            ],
        ),
        horizontal_calibrations={
            DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                0.0,
                1.0,
                100.0,
                100.0,
                ScaleType.LOGARITHMIC,
            )
        },
    )

    assembly = build_project_curve("gr", (source,))

    # The displayed PCHIP crosses x=75 at y=25. Converting that intersection
    # through the log calibration gives 10**1.5, not a PCHIP of [1, 100, 1].
    assert assembly.value_at_depth(25.0) == pytest.approx(10.0**1.5)


def test_manual_pchip_uses_inverse_piecewise_depth_calibration():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(50.0, 25.0),
            DepthCalibrationPoint(100.0, 100.0),
        )
    )
    source = CurveAssemblyInput(
        segment_id="image-1",
        segment_name="Image 1",
        depth_calibration=depth,
        digitized_curve=DigitizedCurve(
            curve_id="gr",
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(0.0, 0.0),
                        TracePoint(100.0, 50.0),
                        TracePoint(0.0, 100.0),
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

    assembly = build_project_curve("gr", (source,))

    # Depth 12.5 maps to y=25 in the first vertical calibration interval. The
    # exact displayed PCHIP crosses x=75 there.
    assert assembly.value_at_depth(12.5) == pytest.approx(75.0)


def test_automatic_control_geometry_takes_precedence_over_archived_raw_trace():
    depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 100.0),
        )
    )
    source = CurveAssemblyInput(
        segment_id="image-1",
        segment_name="Image 1",
        depth_calibration=depth,
        digitized_curve=DigitizedCurve(
            curve_id="gr",
            sections=[
                TraceSection.create_automatic(
                    raw_samples=(
                        TracePoint(0.0, 0.0),
                        TracePoint(0.0, 50.0),
                        TracePoint(0.0, 100.0),
                    ),
                    control_points=(
                        TracePoint(0.0, 0.0),
                        TracePoint(100.0, 50.0),
                        TracePoint(0.0, 100.0),
                    ),
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

    assembly = build_project_curve("gr", (source,))

    assert assembly.value_at_depth(25.0) == pytest.approx(75.0)
