"""Automatic raster line-width estimation for tracker step 8.4D.1.

The estimator is deliberately independent from Qt and from the tracking
algorithm itself. It works directly on grayscale intensity (or RGB/RGBA
converted internally to luminance) and estimates the local stroke thickness
around an explicit user click.

The result is intended to become the tracker's internal spatial scale in later
8.4D steps, so search distance, flexibility and gap tolerance can be expressed
as multiples of the detected curve thickness rather than fixed raster pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pi

import numpy as np

from logdigitizer.domain.digitization import TracePoint


class LineWidthEstimationError(ValueError):
    """Raised when a reliable local stroke width cannot be estimated."""


@dataclass(frozen=True, slots=True)
class LineWidthEstimate:
    """Diagnostic result from one local line-width measurement."""

    width_px: float
    sample_point_px: TracePoint
    contrast_ratio: float
    line_intensity: float
    background_intensity: float

    def __post_init__(self) -> None:
        values = (
            self.width_px,
            self.contrast_ratio,
            self.line_intensity,
            self.background_intensity,
        )
        if not all(isfinite(float(value)) for value in values):
            raise LineWidthEstimationError(
                "Line-width estimate values must be finite."
            )
        if self.width_px <= 0.0:
            raise LineWidthEstimationError(
                "Estimated line width must be positive."
            )


# Internal defaults. They are intentionally not exposed to the user as tracker
# controls. Later steps will expose only simple scientific controls such as
# Sensitivity / Search width / Flexibility / Gap tolerance.
_SNAP_RADIUS_PX = 3
_BACKGROUND_RADIUS_PX = 12
_PROBE_RADIUS_PX = 24.0
_PROFILE_STEP_PX = 0.5
_ANGLE_COUNT = 16
_THRESHOLD_FRACTION = 0.55
_MIN_CONTRAST_RATIO = 0.05
_WIDTH_PERCENTILE = 25.0


def estimate_line_width(
    image: np.ndarray,
    point: TracePoint | tuple[float, float],
) -> LineWidthEstimate:
    """Estimate the local stroke thickness around a user-selected curve point.

    The method is grayscale-native and does not binarize the full raster.

    1. A small neighbourhood snaps the click to a nearby dark stroke pixel.
    2. A larger local neighbourhood estimates the background intensity.
    3. Multiple intensity profiles are sampled through the stroke at different
       angles.
    4. The lower quartile of the measured profile widths is used as the stroke
       thickness. Using multiple directions avoids assuming whether the curve is
       locally vertical, horizontal or diagonal and makes grid intersections
       less likely to dominate the estimate.

    This function does not modify the image.
    """
    gray = _as_grayscale_float(image)
    start = _coerce_point(point)
    height, width = gray.shape

    if not (0.0 <= start.x_px < width and 0.0 <= start.y_px < height):
        raise LineWidthEstimationError(
            "Line-width point must lie inside the image bounds."
        )

    sample = _snap_to_local_dark_stroke(gray, start)
    background = _local_background(gray, sample)
    line_intensity = _bilinear_sample(
        gray,
        np.asarray([sample.x_px]),
        np.asarray([sample.y_px]),
    )[0]

    contrast_ratio = _dark_on_light_contrast(
        line_intensity,
        background,
    )
    if contrast_ratio < _MIN_CONTRAST_RATIO:
        raise LineWidthEstimationError(
            "Insufficient local contrast to estimate line width."
        )

    threshold = (
        line_intensity
        + _THRESHOLD_FRACTION * (background - line_intensity)
    )

    widths = _directional_profile_widths(
        gray,
        sample,
        threshold=float(threshold),
    )
    if not widths:
        raise LineWidthEstimationError(
            "Could not measure a local stroke width."
        )

    estimated = float(np.percentile(widths, _WIDTH_PERCENTILE))
    # Half-pixel sampling intentionally produces subpixel estimates. Do not
    # round here: later tracker geometry can scale continuously from this value.
    estimated = max(_PROFILE_STEP_PX, estimated)

    return LineWidthEstimate(
        width_px=estimated,
        sample_point_px=sample,
        contrast_ratio=float(contrast_ratio),
        line_intensity=float(line_intensity),
        background_intensity=float(background),
    )


def _snap_to_local_dark_stroke(
    gray: np.ndarray,
    point: TracePoint,
) -> TracePoint:
    """Snap slight click imprecision to the nearest sufficiently dark pixel."""
    height, width = gray.shape
    cx = int(round(point.x_px))
    cy = int(round(point.y_px))

    # Estimate background from a larger patch first. This still works when the
    # curve itself is wider than the small snap neighbourhood.
    left_bg = max(0, cx - _BACKGROUND_RADIUS_PX)
    right_bg = min(width - 1, cx + _BACKGROUND_RADIUS_PX)
    top_bg = max(0, cy - _BACKGROUND_RADIUS_PX)
    bottom_bg = min(height - 1, cy + _BACKGROUND_RADIUS_PX)
    background_patch = gray[
        top_bg : bottom_bg + 1,
        left_bg : right_bg + 1,
    ]
    background = float(np.percentile(background_patch, 80.0))

    left = max(0, cx - _SNAP_RADIUS_PX)
    right = min(width - 1, cx + _SNAP_RADIUS_PX)
    top = max(0, cy - _SNAP_RADIUS_PX)
    bottom = min(height - 1, cy + _SNAP_RADIUS_PX)
    patch = gray[top : bottom + 1, left : right + 1]

    darkest = float(np.min(patch))
    if _dark_on_light_contrast(darkest, background) < _MIN_CONTRAST_RATIO:
        raise LineWidthEstimationError(
            "Insufficient local contrast near the selected point."
        )

    # Do not blindly choose the global darkest pixel: at a grid crossing that
    # could move the click onto the grid. Select the nearest pixel belonging to
    # the darkest local intensity band instead.
    candidate_limit = darkest + 0.30 * (background - darkest)
    ys, xs = np.where(patch <= candidate_limit)
    if xs.size == 0:
        raise LineWidthEstimationError(
            "No dark stroke candidate was found near the selected point."
        )

    global_x = xs.astype(float) + left
    global_y = ys.astype(float) + top
    distances_sq = (
        (global_x - point.x_px) ** 2
        + (global_y - point.y_px) ** 2
    )
    best = int(np.argmin(distances_sq))
    return TracePoint(
        float(global_x[best]),
        float(global_y[best]),
    )


def _local_background(
    gray: np.ndarray,
    point: TracePoint,
) -> float:
    height, width = gray.shape
    cx = int(round(point.x_px))
    cy = int(round(point.y_px))
    left = max(0, cx - _BACKGROUND_RADIUS_PX)
    right = min(width - 1, cx + _BACKGROUND_RADIUS_PX)
    top = max(0, cy - _BACKGROUND_RADIUS_PX)
    bottom = min(height - 1, cy + _BACKGROUND_RADIUS_PX)
    patch = gray[top : bottom + 1, left : right + 1]
    return float(np.percentile(patch, 80.0))


def _directional_profile_widths(
    gray: np.ndarray,
    point: TracePoint,
    *,
    threshold: float,
) -> tuple[float, ...]:
    """Measure contiguous dark width through the point at many orientations."""
    height, width = gray.shape
    sample_distances = np.arange(
        -_PROBE_RADIUS_PX,
        _PROBE_RADIUS_PX + 0.5 * _PROFILE_STEP_PX,
        _PROFILE_STEP_PX,
        dtype=float,
    )
    centre_index = int(np.argmin(np.abs(sample_distances)))
    widths: list[float] = []

    for angle in np.linspace(0.0, pi, _ANGLE_COUNT, endpoint=False):
        xs = point.x_px + sample_distances * np.cos(angle)
        ys = point.y_px + sample_distances * np.sin(angle)
        valid = (
            (xs >= 0.0)
            & (xs <= width - 1)
            & (ys >= 0.0)
            & (ys <= height - 1)
        )

        values = np.full(sample_distances.shape, np.inf, dtype=float)
        if np.any(valid):
            values[valid] = _bilinear_sample(
                gray,
                xs[valid],
                ys[valid],
            )

        dark = values <= threshold
        if not dark[centre_index]:
            continue

        first = centre_index
        while first > 0 and dark[first - 1]:
            first -= 1

        last = centre_index
        while last + 1 < dark.size and dark[last + 1]:
            last += 1

        width_px = (last - first + 1) * _PROFILE_STEP_PX
        if width_px > 0.0:
            widths.append(float(width_px))

    return tuple(widths)


def _bilinear_sample(
    gray: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
) -> np.ndarray:
    """Vectorized bilinear interpolation within valid image coordinates."""
    height, width = gray.shape

    x = np.clip(np.asarray(xs, dtype=float), 0.0, width - 1.0)
    y = np.clip(np.asarray(ys, dtype=float), 0.0, height - 1.0)

    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)

    wx = x - x0
    wy = y - y0

    return (
        (1.0 - wx) * (1.0 - wy) * gray[y0, x0]
        + wx * (1.0 - wy) * gray[y0, x1]
        + (1.0 - wx) * wy * gray[y1, x0]
        + wx * wy * gray[y1, x1]
    )


def _dark_on_light_contrast(
    line_intensity: float,
    background_intensity: float,
) -> float:
    if background_intensity <= 0.0:
        return 0.0
    value = (
        background_intensity - line_intensity
    ) / background_intensity
    return float(np.clip(value, 0.0, 1.0))


def _coerce_point(
    value: TracePoint | tuple[float, float],
) -> TracePoint:
    if isinstance(value, TracePoint):
        return value

    try:
        x_px, y_px = value
        x = float(x_px)
        y = float(y_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LineWidthEstimationError(
            "Line-width point must contain two finite coordinates."
        ) from exc

    if not isfinite(x) or not isfinite(y):
        raise LineWidthEstimationError(
            "Line-width point must contain two finite coordinates."
        )
    return TracePoint(x, y)


def _as_grayscale_float(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)

    if array.size == 0:
        raise LineWidthEstimationError(
            "Line-width image cannot be empty."
        )

    if array.ndim == 2:
        gray = array.astype(np.float64, copy=False)
    elif array.ndim == 3 and array.shape[2] in (1, 3, 4):
        if array.shape[2] == 1:
            gray = array[..., 0].astype(np.float64, copy=False)
        else:
            rgb = array[..., :3].astype(np.float64, copy=False)
            gray = (
                0.2126 * rgb[..., 0]
                + 0.7152 * rgb[..., 1]
                + 0.0722 * rgb[..., 2]
            )
    else:
        raise LineWidthEstimationError(
            "Line-width image must be grayscale or RGB(A)."
        )

    if not np.isfinite(gray).all():
        raise LineWidthEstimationError(
            "Line-width image cannot contain NaN or infinite values."
        )
    return gray
