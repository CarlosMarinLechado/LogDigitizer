"""Downward semi-automatic curve tracker for steps 8.4A-8.4B.

Step 8.4A introduced the minimal local tracker. Step 8.4B adds a conservative
confidence model and automatic loss detection while deliberately remaining
independent from Qt, project persistence, calibration and trace simplification.

The tracker works in canonical image-pixel coordinates and assumes, for now,
a dark curve on a lighter background.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from math import ceil, floor, isfinite
from typing import Sequence

import numpy as np

from logdigitizer.domain.digitization import TracePoint


class AutomaticTraceError(ValueError):
    """Raised when the automatic tracker cannot be configured or started."""


class TrackerStopReason(str, Enum):
    """Reason why a detailed tracking run finished."""

    IMAGE_BOTTOM = "image_bottom"
    LOST_CURVE = "lost_curve"


# Step 8.4D.2: the proven 8.4B pixel settings are treated as values for a
# representative 3-pixel stroke. Once 8.4D.1 has measured the actual curve
# thickness, spatial tolerances scale proportionally. Conservative clamps guard
# against an occasional bad width estimate at a grid/text intersection.
_REFERENCE_LINE_WIDTH_PX = 3.0
_MIN_GEOMETRY_SCALE = 0.50
_MAX_GEOMETRY_SCALE = 8.00


@dataclass(frozen=True, slots=True)
class AutomaticTraceSettings:
    """Configuration for the local downward tracker.

    Parameters
    ----------
    search_half_width_px:
        Horizontal search radius around the last accepted X coordinate.
    min_contrast_ratio:
        Minimum local dark-on-light contrast required for a candidate.
        Contrast is scale-independent: ``(background - curve) / background``.
    max_horizontal_step_px:
        Maximum accepted X displacement between consecutive accepted rows.
    min_confidence:
        Minimum combined confidence. The score combines local contrast (65%)
        and horizontal continuity (35%).
    max_consecutive_misses:
        Number of consecutive unreliable rows tolerated before declaring the
        curve lost. For example, the default value ``2`` stops on the third
        consecutive unreliable row. If the curve is reacquired within this
        budget, its two accepted endpoints are joined by a straight connector.
        An unconfirmed gap at the end of a lost run is never bridged.
    """

    search_half_width_px: int = 12
    min_contrast_ratio: float = 0.08
    max_horizontal_step_px: float = 6.0
    min_confidence: float = 0.45
    max_consecutive_misses: int = 2

    def __post_init__(self) -> None:
        search_width = _positive_int(
            self.search_half_width_px,
            "search_half_width_px",
        )
        min_contrast = _unit_interval(
            self.min_contrast_ratio,
            "min_contrast_ratio",
        )
        max_step = _positive_finite(
            self.max_horizontal_step_px,
            "max_horizontal_step_px",
        )
        min_confidence = _unit_interval(
            self.min_confidence,
            "min_confidence",
        )
        misses = _non_negative_int(
            self.max_consecutive_misses,
            "max_consecutive_misses",
        )
        object.__setattr__(self, "search_half_width_px", search_width)
        object.__setattr__(self, "min_contrast_ratio", min_contrast)
        object.__setattr__(self, "max_horizontal_step_px", max_step)
        object.__setattr__(self, "min_confidence", min_confidence)
        object.__setattr__(self, "max_consecutive_misses", misses)

    def geometry_scale_for_line_width(self, line_width_px: float) -> float:
        """Return the bounded spatial scale implied by an estimated stroke width.

        A 3-pixel curve reproduces the original 8.4B geometry exactly. Thicker
        rasters proportionally enlarge search distance, lateral flexibility and
        tolerated missing-row distance; thinner rasters reduce them.

        Contrast/confidence thresholds are intentionally *not* scaled because
        they are dimensionless grayscale quantities rather than pixel geometry.
        """
        try:
            width = float(line_width_px)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AutomaticTraceError(
                "line_width_px must be a positive finite number."
            ) from exc
        if not isfinite(width) or width <= 0.0:
            raise AutomaticTraceError(
                "line_width_px must be a positive finite number."
            )

        raw_scale = width / _REFERENCE_LINE_WIDTH_PX
        return float(
            np.clip(
                raw_scale,
                _MIN_GEOMETRY_SCALE,
                _MAX_GEOMETRY_SCALE,
            )
        )

    def effective_line_width_px(self, line_width_px: float) -> float:
        """Return the bounded stroke width used by all spatial algorithms.

        The measured width is intentionally kept available for diagnostics, but
        a rare overestimate at text or a grid crossing must not enlarge only
        some tracker windows.  Converting the bounded geometry scale back to
        pixels gives Search, Flexibility, Gap tolerance, cost-map smoothing,
        look-ahead and grid detection one shared spatial reference.
        """
        scale = self.geometry_scale_for_line_width(line_width_px)
        return float(_REFERENCE_LINE_WIDTH_PX * scale)

    def scaled_for_line_width(
        self,
        line_width_px: float,
    ) -> "AutomaticTraceSettings":
        """Resolve pixel-based tracker geometry from the measured curve width."""
        scale = self.geometry_scale_for_line_width(line_width_px)

        search_half_width = max(
            1,
            int(round(self.search_half_width_px * scale)),
        )
        max_horizontal_step = max(
            0.5,
            self.max_horizontal_step_px * scale,
        )

        # ``max_consecutive_misses`` means the run actually stops on the next
        # miss. Scale that full gap budget, then convert it back to the stored
        # "consecutive misses tolerated" representation.
        gap_budget = max(
            1,
            int(round((self.max_consecutive_misses + 1) * scale)),
        )
        max_consecutive_misses = gap_budget - 1

        return replace(
            self,
            search_half_width_px=search_half_width,
            max_horizontal_step_px=max_horizontal_step,
            max_consecutive_misses=max_consecutive_misses,
        )

    def resolved_for_tracking(
        self,
        line_width_px: float | None,
    ) -> "AutomaticTraceSettings":
        """Return the complete effective settings for one tracking run.

        Only raster resolution changes the pixel-based geometry. Keeping that
        operation in one method prevents callers from accidentally scaling only
        part of the tracker configuration.
        """
        if line_width_px is None:
            return self
        return self.scaled_for_line_width(line_width_px)


@dataclass(frozen=True, slots=True)
class TrackingResult:
    """Detailed, non-persistent report produced by one downward tracking run.

    ``raw_samples_px`` contains accepted tracker samples plus straight
    interpolation points across gaps that were successfully reacquired. The
    first item is always the exact user-selected start point. Interpolated rows
    have confidence ``0.0`` so they remain distinguishable from observations.

    ``confidences`` corresponds one-to-one with ``raw_samples_px``. The user
    click is assigned confidence 1.0 because it is an explicit observation,
    not an automatically detected candidate.

    When the curve is lost, ``stop_y_px`` identifies the first unreliable row
    in the final failed run. This is intentionally suitable for a future GUI
    that asks the user to click the correct curve near that location and resume.
    """

    raw_samples_px: tuple[TracePoint, ...]
    confidences: tuple[float, ...]
    stop_reason: TrackerStopReason
    stop_y_px: float | None = None
    stop_confidence: float | None = None
    consecutive_misses: int = 0

    def __post_init__(self) -> None:
        if len(self.raw_samples_px) != len(self.confidences):
            raise AutomaticTraceError(
                "Tracking result samples and confidences must have equal length."
            )


def connect_sample_gaps_linearly(
    samples: Sequence[TracePoint],
    confidences: Sequence[float],
) -> tuple[tuple[TracePoint, ...], tuple[float, ...]]:
    """Fill accepted vertical gaps with straight, one-row connectors.

    This is deliberately a post-processing operation. It cannot change search
    centres, confidence decisions, stop locations or gap-tolerance behaviour.
    Only a gap bounded by two already accepted points is reconstructed; an
    unconfirmed trailing gap remains open when the tracker reports a loss.

    Exact accepted endpoints and their confidences are preserved. Synthetic
    integer-row samples receive confidence ``0.0`` to retain their provenance.
    """
    accepted = tuple(samples)
    accepted_confidences = tuple(float(value) for value in confidences)
    if len(accepted) != len(accepted_confidences):
        raise AutomaticTraceError(
            "Samples and confidences must have equal length before gap "
            "reconstruction."
        )
    if len(accepted) < 2:
        return accepted, accepted_confidences

    connected_samples: list[TracePoint] = [accepted[0]]
    connected_confidences: list[float] = [accepted_confidences[0]]

    for start, end, end_confidence in zip(
        accepted,
        accepted[1:],
        accepted_confidences[1:],
    ):
        if end.y_px <= start.y_px:
            raise AutomaticTraceError(
                "Accepted tracker samples must be strictly increasing in Y."
            )

        delta_x = end.x_px - start.x_px
        delta_y = end.y_px - start.y_px
        for y_px in range(floor(start.y_px) + 1, ceil(end.y_px)):
            if not start.y_px < y_px < end.y_px:
                continue
            fraction = (float(y_px) - start.y_px) / delta_y
            connected_samples.append(
                TracePoint(
                    start.x_px + fraction * delta_x,
                    float(y_px),
                )
            )
            connected_confidences.append(0.0)

        connected_samples.append(end)
        connected_confidences.append(end_confidence)

    return tuple(connected_samples), tuple(connected_confidences)


@dataclass(frozen=True, slots=True)
class _CandidateAssessment:
    x_px: float
    contrast_ratio: float
    horizontal_step_px: float
    confidence: float
    reliable: bool


def track_downward(
    image: np.ndarray,
    start_point: TracePoint | Sequence[float],
    *,
    settings: AutomaticTraceSettings | None = None,
) -> tuple[TracePoint, ...]:
    """Return the continuous reconstructed tracker path.

    Use :func:`track_downward_result` when the caller also needs the stop
    reason/location and confidence values.
    """
    return track_downward_result(
        image,
        start_point,
        settings=settings,
    ).raw_samples_px


def track_downward_result(
    image: np.ndarray,
    start_point: TracePoint | Sequence[float],
    *,
    settings: AutomaticTraceSettings | None = None,
) -> TrackingResult:
    """Track a dark curve downward with confidence-based loss detection.

    The search advances one image row at a time. On each row it identifies the
    nearest darkest contiguous run inside the local search window and scores it
    using:

    * local dark-on-light contrast;
    * horizontal continuity with the previous accepted sample.

    Unreliable rows are not used and do not move the search centre. A small
    number of consecutive misses is tolerated so an isolated raster defect or
    thin horizontal grid line does not necessarily stop the run. When a later
    row is accepted, the gap is reconstructed afterwards with a straight line;
    this post-processing never participates in the search itself.
    """
    cfg = settings or AutomaticTraceSettings()
    gray = _as_grayscale_float(image)
    height, width = gray.shape
    start = _coerce_start_point(start_point)

    if not (0.0 <= start.x_px < width and 0.0 <= start.y_px < height):
        raise AutomaticTraceError(
            "Start point must lie inside the image bounds."
        )

    samples: list[TracePoint] = [start]
    confidences: list[float] = [1.0]
    previous_x = start.x_px
    first_row = floor(start.y_px) + 1

    misses = 0
    failed_run_start_y: float | None = None
    last_failed_confidence: float | None = None

    for y_px in range(first_row, height):
        assessment = _assess_local_candidate(
            gray[y_px],
            previous_x=previous_x,
            settings=cfg,
        )

        if assessment.reliable:
            previous_x = assessment.x_px
            samples.append(TracePoint(previous_x, float(y_px)))
            confidences.append(assessment.confidence)
            misses = 0
            failed_run_start_y = None
            last_failed_confidence = None
            continue

        if misses == 0:
            failed_run_start_y = float(y_px)
        misses += 1
        last_failed_confidence = assessment.confidence

        if misses > cfg.max_consecutive_misses:
            connected, connected_confidences = (
                connect_sample_gaps_linearly(samples, confidences)
            )
            return TrackingResult(
                raw_samples_px=connected,
                confidences=connected_confidences,
                stop_reason=TrackerStopReason.LOST_CURVE,
                stop_y_px=failed_run_start_y,
                stop_confidence=last_failed_confidence,
                consecutive_misses=misses,
            )

    connected, connected_confidences = connect_sample_gaps_linearly(
        samples,
        confidences,
    )
    return TrackingResult(
        raw_samples_px=connected,
        confidences=connected_confidences,
        stop_reason=TrackerStopReason.IMAGE_BOTTOM,
        stop_y_px=None,
        stop_confidence=None,
        consecutive_misses=0,
    )


def _assess_local_candidate(
    row: np.ndarray,
    *,
    previous_x: float,
    settings: AutomaticTraceSettings,
) -> _CandidateAssessment:
    candidate_x, candidate_value, local_background = _darkest_local_run(
        row,
        previous_x=previous_x,
        search_half_width_px=settings.search_half_width_px,
    )

    contrast_ratio = _dark_on_light_contrast(
        candidate_value,
        local_background,
    )
    horizontal_step = abs(candidate_x - previous_x)

    if settings.max_horizontal_step_px > 0.0:
        continuity = max(
            0.0,
            1.0 - horizontal_step / settings.max_horizontal_step_px,
        )
    else:  # defensive; settings validation prevents this path
        continuity = 0.0

    confidence = float(
        np.clip(0.65 * contrast_ratio + 0.35 * continuity, 0.0, 1.0)
    )
    reliable = (
        contrast_ratio >= settings.min_contrast_ratio
        and horizontal_step <= settings.max_horizontal_step_px
        and confidence >= settings.min_confidence
    )

    return _CandidateAssessment(
        x_px=candidate_x,
        contrast_ratio=contrast_ratio,
        horizontal_step_px=horizontal_step,
        confidence=confidence,
        reliable=reliable,
    )


def _coerce_start_point(
    value: TracePoint | Sequence[float],
) -> TracePoint:
    if isinstance(value, TracePoint):
        return value
    try:
        x_px, y_px = value
    except (TypeError, ValueError) as exc:
        raise AutomaticTraceError(
            "Start point must contain exactly two coordinates."
        ) from exc

    try:
        x = float(x_px)
        y = float(y_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            "Start point coordinates must be finite numbers."
        ) from exc

    if not isfinite(x) or not isfinite(y):
        raise AutomaticTraceError(
            "Start point coordinates must be finite numbers."
        )
    return TracePoint(x, y)


def _as_grayscale_float(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)

    if array.size == 0:
        raise AutomaticTraceError("Tracking image cannot be empty.")

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
        raise AutomaticTraceError(
            "Tracking image must be a 2-D grayscale or 3/4-channel RGB(A) array."
        )

    if gray.shape[0] < 1 or gray.shape[1] < 1:
        raise AutomaticTraceError("Tracking image cannot be empty.")
    if not np.isfinite(gray).all():
        raise AutomaticTraceError(
            "Tracking image cannot contain NaN or infinite values."
        )
    return gray


def _darkest_local_run(
    row: np.ndarray,
    *,
    previous_x: float,
    search_half_width_px: int,
) -> tuple[float, float, float]:
    """Return centre, intensity and local background of the best dark run."""
    width = int(row.shape[0])
    centre = int(round(previous_x))
    left = max(0, centre - search_half_width_px)
    right = min(width - 1, centre + search_half_width_px)

    window = row[left : right + 1]
    minimum = float(np.min(window))

    darkest_local = np.flatnonzero(
        np.isclose(window, minimum, rtol=1e-12, atol=1e-12)
    )
    darkest_global = darkest_local + left
    runs = _contiguous_runs(darkest_global)

    best_start, best_end = min(
        runs,
        key=lambda run: abs(0.5 * (run[0] + run[1]) - previous_x),
    )
    candidate_x = 0.5 * (best_start + best_end)

    # Median is intentionally robust to a thin dark curve or isolated noise.
    # A full-width dark grid row therefore produces almost no contrast and is
    # treated conservatively as an unreliable observation.
    local_background = float(np.median(window))
    return candidate_x, minimum, local_background


def _darkest_local_run_center(
    row: np.ndarray,
    *,
    previous_x: float,
    search_half_width_px: int,
) -> float:
    """Compatibility helper retained from step 8.4A."""
    x_px, _candidate, _background = _darkest_local_run(
        row,
        previous_x=previous_x,
        search_half_width_px=search_half_width_px,
    )
    return x_px


def _dark_on_light_contrast(
    candidate_value: float,
    background_value: float,
) -> float:
    """Return a scale-independent local dark-on-light contrast in [0, 1]."""
    if background_value <= 0.0:
        return 0.0
    ratio = (background_value - candidate_value) / background_value
    return float(np.clip(ratio, 0.0, 1.0))


def _contiguous_runs(indices: np.ndarray) -> tuple[tuple[int, int], ...]:
    if indices.size == 0:
        raise AutomaticTraceError(
            "Internal tracker error: no local candidate was found."
        )

    runs: list[tuple[int, int]] = []
    run_start = previous = int(indices[0])

    for raw_index in indices[1:]:
        index = int(raw_index)
        if index != previous + 1:
            runs.append((run_start, previous))
            run_start = index
        previous = index

    runs.append((run_start, previous))
    return tuple(runs)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise AutomaticTraceError(f"{name} must be a positive integer.")
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            f"{name} must be a positive integer."
        ) from exc
    if integer != value or integer < 1:
        raise AutomaticTraceError(f"{name} must be a positive integer.")
    return integer


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise AutomaticTraceError(f"{name} must be a non-negative integer.")
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            f"{name} must be a non-negative integer."
        ) from exc
    if integer != value or integer < 0:
        raise AutomaticTraceError(f"{name} must be a non-negative integer.")
    return integer


def _positive_finite(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            f"{name} must be a positive finite number."
        ) from exc
    if not isfinite(number) or number <= 0.0:
        raise AutomaticTraceError(
            f"{name} must be a positive finite number."
        )
    return number


def _unit_interval(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            f"{name} must be between 0 and 1."
        ) from exc
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise AutomaticTraceError(f"{name} must be between 0 and 1.")
    return number
