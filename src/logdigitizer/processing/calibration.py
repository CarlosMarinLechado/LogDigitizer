"""Scientific calibration transforms in canonical rectified-image coordinates.

This module deliberately has no PySide6 dependency.
"""

from __future__ import annotations

from bisect import bisect_right
from math import isfinite, log10

from logdigitizer.domain.calibration import (
    CalibrationError,
    DepthCalibration,
    HorizontalCalibration,
    ScaleType,
)


class CalibrationRangeError(CalibrationError):
    """Raised when a requested position would produce an invalid depth."""


def is_pixel_y_digitizable(
    pixel_y: float,
    calibration: DepthCalibration,
) -> bool:
    """Return whether a vertical image position may be digitized.

    Both image ends are extrapolatable. A position is rejected only when its
    calibrated depth would be negative or when ``pixel_y`` is not finite.
    """
    if not isfinite(pixel_y):
        return False

    try:
        pixel_y_to_depth(pixel_y, calibration)
    except CalibrationRangeError:
        return False

    return True


def pixel_y_to_depth(
    pixel_y: float,
    calibration: DepthCalibration,
) -> float:
    """Map rectified-image Y to depth.

    Between depth-control points, conversion is piecewise linear. Above the
    first point, the first calibration segment is extended linearly. Below the
    last point, the final segment is extended linearly. Any result below
    physical depth 0 is rejected.
    """
    if not isfinite(pixel_y):
        raise CalibrationError(
            "pixel_y must be a finite number."
        )

    points = calibration.points

    if pixel_y <= points[0].pixel_y:
        depth = _interpolate_depth(
            pixel_y,
            points[0].pixel_y,
            points[0].depth,
            points[1].pixel_y,
            points[1].depth,
        )
    elif pixel_y >= points[-1].pixel_y:
        depth = _interpolate_depth(
            pixel_y,
            points[-2].pixel_y,
            points[-2].depth,
            points[-1].pixel_y,
            points[-1].depth,
        )
    else:
        axis = [point.pixel_y for point in points]
        index = bisect_right(axis, pixel_y) - 1
        first = points[index]
        second = points[index + 1]
        depth = _interpolate_depth(
            pixel_y,
            first.pixel_y,
            first.depth,
            second.pixel_y,
            second.depth,
        )

    if depth < 0:
        raise CalibrationRangeError(
            "The calibrated position would produce a negative depth; "
            "digitization is not allowed there."
        )

    return depth


def depth_to_pixel_y(
    depth: float,
    calibration: DepthCalibration,
) -> float:
    """Map a non-negative depth back to rectified-image Y.

    The inverse follows the same piecewise/extrapolation policy as
    :func:`pixel_y_to_depth`.
    """
    if not isfinite(depth):
        raise CalibrationError(
            "Depth must be a finite number."
        )
    if depth < 0:
        raise CalibrationRangeError(
            "Negative depths are not digitizable."
        )

    points = calibration.points
    first = points[0]
    last = points[-1]

    if calibration.depth_increases_with_y:
        if depth <= first.depth:
            return _interpolate_pixel_y(
                depth,
                points[0],
                points[1],
            )
        if depth >= last.depth:
            return _interpolate_pixel_y(
                depth,
                points[-2],
                points[-1],
            )
    else:
        if depth >= first.depth:
            return _interpolate_pixel_y(
                depth,
                points[0],
                points[1],
            )
        if depth <= last.depth:
            return _interpolate_pixel_y(
                depth,
                points[-2],
                points[-1],
            )

    for first, second in zip(points, points[1:]):
        low = min(first.depth, second.depth)
        high = max(first.depth, second.depth)
        if low <= depth <= high:
            return _interpolate_pixel_y(
                depth,
                first,
                second,
            )

    raise CalibrationRangeError(
        "Depth cannot be mapped by the current calibration."
    )


def pixel_x_to_value(
    pixel_x: float,
    calibration: HorizontalCalibration,
    *,
    scale_offset: int = 0,
) -> float:
    """Map image X to a physical value, including a backup-scale offset.

    ``scale_offset`` counts complete widths of a *linear* horizontal scale.
    The image coordinate itself is never moved.  Logarithmic backup scales are
    deliberately not implemented; a non-zero offset on one is rejected.
    """
    if isinstance(scale_offset, bool):
        raise CalibrationError("Scale offset must be an integer.")
    try:
        numeric_offset = float(scale_offset)
    except (TypeError, ValueError) as exc:
        raise CalibrationError("Scale offset must be an integer.") from exc
    if not isfinite(numeric_offset) or not numeric_offset.is_integer():
        raise CalibrationError("Scale offset must be an integer.")
    offset = int(numeric_offset)

    ratio = (
        pixel_x - calibration.pixel_x1
    ) / (
        calibration.pixel_x2 - calibration.pixel_x1
    )

    if calibration.scale_type is ScaleType.LINEAR:
        base_value = float(
            calibration.value1
            + ratio * (calibration.value2 - calibration.value1)
        )
        scale_width = abs(calibration.value2 - calibration.value1)
        return float(base_value + offset * scale_width)

    if offset:
        raise CalibrationError(
            "Scale offset is currently supported only for linear "
            "horizontal calibrations."
        )

    log1 = log10(calibration.value1)
    log2 = log10(calibration.value2)
    return float(10.0 ** (log1 + ratio * (log2 - log1)))


def linear_transition_value(
    depth: float,
    *,
    start_depth: float,
    start_value: float,
    end_depth: float,
    end_value: float,
) -> float:
    """Interpolate one LAS value along a straight section transition.

    Callers remain responsible for deciding whether two neighbouring sections
    are close enough to bridge.  Values supplied here must already include
    their respective scale offsets.
    """
    try:
        supplied = (
            float(depth),
            float(start_depth),
            float(start_value),
            float(end_depth),
            float(end_value),
        )
    except (TypeError, ValueError) as exc:
        raise CalibrationError(
            "Linear transition coordinates must be finite numbers."
        ) from exc
    if not all(isfinite(value) for value in supplied):
        raise CalibrationError(
            "Linear transition coordinates must be finite."
        )

    sample_depth, first_depth, first_value, last_depth, last_value = supplied
    if first_depth == last_depth:
        raise CalibrationError(
            "A linear transition requires different endpoint depths."
        )
    minimum_depth, maximum_depth = sorted((first_depth, last_depth))
    if not minimum_depth <= sample_depth <= maximum_depth:
        raise CalibrationRangeError(
            "Requested depth lies outside the linear transition."
        )

    ratio = (sample_depth - first_depth) / (last_depth - first_depth)
    return float(first_value + ratio * (last_value - first_value))


def value_to_pixel_x(
    value: float,
    calibration: HorizontalCalibration,
) -> float:
    """Map physical curve value back to horizontal image X."""
    if (
        calibration.scale_type is ScaleType.LOGARITHMIC
        and value <= 0
    ):
        raise CalibrationError(
            "Logarithmic calibration requires positive "
            "physical values."
        )

    if calibration.scale_type is ScaleType.LINEAR:
        ratio = (
            value - calibration.value1
        ) / (
            calibration.value2 - calibration.value1
        )
    else:
        log1 = log10(calibration.value1)
        log2 = log10(calibration.value2)
        ratio = (log10(value) - log1) / (log2 - log1)

    return float(
        calibration.pixel_x1
        + ratio * (calibration.pixel_x2 - calibration.pixel_x1)
    )


def _interpolate_depth(
    pixel_y: float,
    pixel_y1: float,
    depth1: float,
    pixel_y2: float,
    depth2: float,
) -> float:
    ratio = (pixel_y - pixel_y1) / (pixel_y2 - pixel_y1)
    return float(depth1 + ratio * (depth2 - depth1))


def _interpolate_pixel_y(
    depth: float,
    first,
    second,
) -> float:
    ratio = (
        depth - first.depth
    ) / (
        second.depth - first.depth
    )
    return float(
        first.pixel_y
        + ratio * (second.pixel_y - first.pixel_y)
    )
