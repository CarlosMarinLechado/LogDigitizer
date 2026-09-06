"""Grayscale minimum-cost tracking for steps 8.4D.3-8.4D.7B.

This module is the first non-greedy tracking engine in the project. Instead of
choosing the best candidate independently on every image row, it evaluates a
short downward raster window and finds the lowest cumulative-cost path through
that whole window.

The implementation deliberately uses only NumPy. The downward-only graph is a
directed acyclic graph, so dynamic programming is sufficient and keeps the
tracker independent from an additional graph-processing dependency. The same
grayscale cost map, movement cost and soft grid penalty are used both for free
downward tracking and for a correction path with two mandatory endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Sequence

import numpy as np

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    TrackerStopReason,
    TrackingResult,
    connect_sample_gaps_linearly,
)

# Internal tracker constants. They are not user-facing controls.
_DEFAULT_LINE_WIDTH_PX = 3.0
_MIN_LOOKAHEAD_ROWS = 8
_MAX_LOOKAHEAD_ROWS = 48
_LOOKAHEAD_LINE_WIDTH_MULTIPLIER = 4.0
_MOVEMENT_COST_WEIGHT = 2.00
_GRID_PENALTY_WEIGHT = 0.65
_ROW_BACKGROUND_PERCENTILE = 80.0


@dataclass(frozen=True, slots=True)
class AnchoredPathResult:
    """Minimum-cost correction path with two exact user-defined endpoints."""

    raw_samples_px: tuple[TracePoint, ...]
    confidences: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.raw_samples_px) != len(self.confidences):
            raise AutomaticTraceError(
                "Anchored path samples and confidences must have equal length."
            )
        if len(self.raw_samples_px) < 2:
            raise AutomaticTraceError(
                "Anchored path must contain both mandatory endpoints."
            )

        y_values = [point.y_px for point in self.raw_samples_px]
        if any(
            second <= first
            for first, second in zip(y_values, y_values[1:])
        ):
            raise AutomaticTraceError(
                "Anchored path samples must be strictly increasing in Y."
            )

        if any(
            not isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
            for confidence in self.confidences
        ):
            raise AutomaticTraceError(
                "Anchored path confidences must lie in the [0, 1] interval."
            )


def track_downward_minimum_cost_result(
    image: np.ndarray,
    start_point: TracePoint | Sequence[float],
    *,
    settings: AutomaticTraceSettings | None = None,
    line_width_px: float | None = None,
    grid_penalty_mask: np.ndarray | None = None,
) -> TrackingResult:
    """Track downward using short-horizon grayscale minimum-cost paths.

    The returned ``TrackingResult`` preserves the existing 8.4B contract:

    * the exact user click is the first raw sample;
    * raw samples are strictly increasing in Y;
    * low-confidence rows never influence the accepted tracking path;
    * successfully reacquired gaps are joined linearly after tracking;
    * repeated unreliable rows still produce ``LOST_CURVE``;
    * the image bottom produces ``IMAGE_BOTTOM``.

    ``line_width_px`` is used only as an internal raster scale when building the
    cost map and choosing look-ahead length. Spatial search/miss tolerances are
    already expected to have been resolved by 8.4D.2 settings.
    """
    cfg = settings or AutomaticTraceSettings()
    gray = _as_grayscale_float(image)
    height, width = gray.shape
    start = _coerce_point(start_point)

    if not (0.0 <= start.x_px < width and 0.0 <= start.y_px < height):
        raise AutomaticTraceError(
            "Start point must lie inside the image bounds."
        )

    stroke_width = _validated_line_width(line_width_px)
    grid_mask = _validated_grid_penalty_mask(
        grid_penalty_mask,
        expected_shape=gray.shape,
    )
    lookahead_rows = _lookahead_rows(stroke_width)

    samples: list[TracePoint] = [start]
    confidences: list[float] = [1.0]

    previous_accepted_x = start.x_px
    y_cursor = floor(start.y_px) + 1

    misses = 0
    failed_run_start_y: float | None = None
    last_failed_confidence: float | None = None

    while y_cursor < height:
        block_stop = min(height, y_cursor + lookahead_rows)
        path_xs = _minimum_cost_path_for_block(
            gray,
            y_start=y_cursor,
            y_stop=block_stop,
            start_x=previous_accepted_x,
            settings=cfg,
            line_width_px=stroke_width,
            grid_penalty_mask=grid_mask,
        )

        if path_xs.size != block_stop - y_cursor:
            raise AutomaticTraceError(
                "Internal minimum-cost tracker returned an invalid path length."
            )

        for offset, candidate_x in enumerate(path_xs):
            y_px = y_cursor + offset
            candidate_x = float(candidate_x)

            contrast_ratio, confidence, reliable = _assess_path_candidate(
                gray[y_px],
                candidate_x=candidate_x,
                previous_accepted_x=previous_accepted_x,
                settings=cfg,
                line_width_px=stroke_width,
            )

            if reliable:
                previous_accepted_x = candidate_x
                samples.append(
                    TracePoint(candidate_x, float(y_px))
                )
                confidences.append(confidence)
                misses = 0
                failed_run_start_y = None
                last_failed_confidence = None
                continue

            if misses == 0:
                failed_run_start_y = float(y_px)
            misses += 1
            last_failed_confidence = confidence

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

        y_cursor = block_stop

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


def track_between_points_minimum_cost_result(
    image: np.ndarray,
    start_point: TracePoint | Sequence[float],
    end_point: TracePoint | Sequence[float],
    *,
    settings: AutomaticTraceSettings | None = None,
    line_width_px: float | None = None,
    grid_penalty_mask: np.ndarray | None = None,
) -> AnchoredPathResult:
    """Reconstruct a downward path whose start and end are both mandatory.

    This is the constrained connector used by guided correction step 8.4D.7B.
    Interior samples are selected jointly with the same grayscale, smoothness
    and grid costs as the ordinary tracker. The two explicit endpoints are
    preserved exactly, including fractional image coordinates.
    """
    cfg = settings or AutomaticTraceSettings()
    gray = _as_grayscale_float(image)
    height, width = gray.shape
    start = _coerce_point(start_point)
    end = _coerce_point(end_point)

    for label, point in (("Start", start), ("End", end)):
        if not (0.0 <= point.x_px < width and 0.0 <= point.y_px < height):
            raise AutomaticTraceError(
                f"{label} point must lie inside the image bounds."
            )

    if end.y_px <= start.y_px:
        raise AutomaticTraceError(
            "Anchored path endpoint must be below its start point."
        )

    horizontal_distance = abs(end.x_px - start.x_px)
    if horizontal_distance > cfg.search_half_width_px + 1e-9:
        raise AutomaticTraceError(
            "Correction point is outside the anchored search width."
        )

    stroke_width = _validated_line_width(line_width_px)
    grid_mask = _validated_grid_penalty_mask(
        grid_penalty_mask,
        expected_shape=gray.shape,
    )

    first_interior_y = floor(start.y_px) + 1
    last_interior_y = ceil(end.y_px) - 1
    interior_ys = np.arange(
        first_interior_y,
        last_interior_y + 1,
        dtype=np.int32,
    )
    if interior_ys.size:
        interior_ys = interior_ys[
            (interior_ys > start.y_px)
            & (interior_ys < end.y_px)
        ]

    transition_count = int(interior_ys.size) + 1
    maximum_reachable_distance = (
        transition_count * cfg.max_horizontal_step_px
    )
    if horizontal_distance > maximum_reachable_distance + 1e-9:
        raise AutomaticTraceError(
            "Correction endpoint cannot be reached with the current flexibility."
        )

    if interior_ys.size == 0:
        return AnchoredPathResult(
            raw_samples_px=(start, end),
            confidences=(1.0, 1.0),
        )

    half_width = int(cfg.search_half_width_px)
    x_left = max(
        0,
        int(floor(start.x_px - half_width)),
    )
    x_right = min(
        width - 1,
        int(ceil(start.x_px + half_width)),
    )
    if x_left > x_right:
        raise AutomaticTraceError(
            "Internal anchored minimum-cost search window is empty."
        )

    y_start = int(interior_ys[0])
    y_stop = int(interior_ys[-1]) + 1
    block = gray[y_start:y_stop, x_left : x_right + 1]
    cost_map = build_grayscale_cost_map(
        block,
        line_width_px=stroke_width,
    )
    if grid_mask is not None:
        grid_block = grid_mask[
            y_start:y_stop,
            x_left : x_right + 1,
        ]
        cost_map = cost_map + _GRID_PENALTY_WEIGHT * grid_block

    local_path = _minimum_cost_path_through_cost_map(
        cost_map,
        start_x=float(start.x_px - x_left),
        settings=cfg,
        required_end_x=float(end.x_px - x_left),
    )
    if local_path is None:
        raise AutomaticTraceError(
            "Correction endpoint cannot be reached with the current flexibility."
        )

    path_xs = local_path + float(x_left)
    samples: list[TracePoint] = [start]
    confidences: list[float] = [1.0]
    previous_x = start.x_px

    for y_px, candidate_x in zip(interior_ys, path_xs):
        candidate_x = float(candidate_x)
        _contrast, confidence, _reliable = _assess_path_candidate(
            gray[int(y_px)],
            candidate_x=candidate_x,
            previous_accepted_x=previous_x,
            settings=cfg,
            line_width_px=stroke_width,
        )
        samples.append(TracePoint(candidate_x, float(y_px)))
        confidences.append(confidence)
        previous_x = candidate_x

    samples.append(end)
    confidences.append(1.0)
    return AnchoredPathResult(
        raw_samples_px=tuple(samples),
        confidences=tuple(confidences),
    )


def build_grayscale_cost_map(
    gray_block: np.ndarray,
    *,
    line_width_px: float,
) -> np.ndarray:
    """Convert a grayscale block into a [0, 1] curve-following cost map.

    Dark line-like structures have low cost; light background has high cost.
    Before normalization each row is smoothed horizontally over approximately
    one detected stroke width. This suppresses isolated one-pixel noise and
    favours the centre of a stroke whose thickness matches the clicked curve.

    No global threshold/binarization is performed.
    """
    block = np.asarray(gray_block, dtype=np.float64)
    if block.ndim != 2 or block.size == 0:
        raise AutomaticTraceError(
            "Cost-map input must be a non-empty grayscale block."
        )
    if not np.isfinite(block).all():
        raise AutomaticTraceError(
            "Cost-map input cannot contain NaN or infinite values."
        )

    smoothed = _horizontal_box_average(
        block,
        width_px=line_width_px,
    )
    cost = np.empty_like(smoothed, dtype=np.float64)

    for row_index in range(smoothed.shape[0]):
        row = smoothed[row_index]
        background = float(
            np.percentile(row, _ROW_BACKGROUND_PERCENTILE)
        )
        darkest = float(np.min(row))
        span = background - darkest

        if span <= max(1e-12, abs(background) * 1e-6):
            cost[row_index].fill(1.0)
            continue

        normalized = (row - darkest) / span
        cost[row_index] = np.clip(normalized, 0.0, 1.0)

    return cost


def _minimum_cost_path_for_block(
    gray: np.ndarray,
    *,
    y_start: int,
    y_stop: int,
    start_x: float,
    settings: AutomaticTraceSettings,
    line_width_px: float,
    grid_penalty_mask: np.ndarray | None,
) -> np.ndarray:
    """Find one downward-only minimum-cost path through a local raster window."""
    image_width = int(gray.shape[1])
    centre = int(round(start_x))
    half_width = int(settings.search_half_width_px)

    x_left = max(0, centre - half_width)
    x_right = min(image_width - 1, centre + half_width)
    if x_left > x_right:
        raise AutomaticTraceError(
            "Internal minimum-cost search window is empty."
        )

    block = gray[y_start:y_stop, x_left : x_right + 1]
    cost_map = build_grayscale_cost_map(
        block,
        line_width_px=line_width_px,
    )
    if grid_penalty_mask is not None:
        grid_block = grid_penalty_mask[
            y_start:y_stop,
            x_left : x_right + 1,
        ]
        cost_map = (
            cost_map
            + _GRID_PENALTY_WEIGHT * grid_block
        )
    rows, columns = cost_map.shape
    if rows == 0 or columns == 0:
        return np.empty((0,), dtype=float)

    local_path = _minimum_cost_path_through_cost_map(
        cost_map,
        start_x=float(start_x - x_left),
        settings=settings,
    )
    if local_path is None:
        # This should be rare because dx=0 is always available, but retain a
        # deterministic fallback rather than leaking an invalid path.
        return np.full(
            rows,
            float(np.clip(round(start_x), x_left, x_right)),
            dtype=float,
        )
    return local_path + float(x_left)


def _minimum_cost_path_through_cost_map(
    cost_map: np.ndarray,
    *,
    start_x: float,
    settings: AutomaticTraceSettings,
    required_end_x: float | None = None,
) -> np.ndarray | None:
    """Solve one downward DAG, optionally forcing a known terminal X."""
    rows, columns = cost_map.shape
    if rows == 0 or columns == 0:
        return np.empty((0,), dtype=float)

    max_step = max(
        1,
        int(ceil(settings.max_horizontal_step_px)),
    )
    previous_cost = np.full(columns, np.inf, dtype=np.float64)
    first_back = np.full(columns, -1, dtype=np.int32)

    x_indices = np.arange(columns, dtype=float)
    first_dx = np.abs(x_indices - start_x)
    reachable = first_dx <= settings.max_horizontal_step_px + 1e-9
    movement = _movement_cost(
        first_dx,
        max_horizontal_step_px=settings.max_horizontal_step_px,
    )
    previous_cost[reachable] = (
        cost_map[0, reachable]
        + movement[reachable]
    )

    backpointers = np.full(
        (rows, columns),
        -1,
        dtype=np.int32,
    )
    backpointers[0] = first_back

    for row_index in range(1, rows):
        current_cost = np.full(
            columns,
            np.inf,
            dtype=np.float64,
        )
        current_back = np.full(
            columns,
            -1,
            dtype=np.int32,
        )

        for dx in range(-max_step, max_step + 1):
            if abs(dx) > settings.max_horizontal_step_px + 1e-9:
                continue

            movement_penalty = float(
                _movement_cost(
                    np.asarray([abs(dx)], dtype=float),
                    max_horizontal_step_px=settings.max_horizontal_step_px,
                )[0]
            )

            if dx < 0:
                # current x = previous x + dx
                previous_slice = slice(-dx, columns)
                current_slice = slice(0, columns + dx)
            elif dx > 0:
                previous_slice = slice(0, columns - dx)
                current_slice = slice(dx, columns)
            else:
                previous_slice = slice(0, columns)
                current_slice = slice(0, columns)

            candidate = previous_cost[previous_slice] + movement_penalty
            existing = current_cost[current_slice]
            better = candidate < existing
            if not np.any(better):
                continue

            previous_positions = np.arange(columns)[previous_slice]
            existing[better] = candidate[better]
            current_cost[current_slice] = existing

            back_values = current_back[current_slice]
            back_values[better] = previous_positions[better]
            current_back[current_slice] = back_values

        current_cost += cost_map[row_index]
        backpointers[row_index] = current_back
        previous_cost = current_cost

    if required_end_x is None:
        terminal_cost = previous_cost
    else:
        end_dx = np.abs(x_indices - required_end_x)
        terminal_cost = np.full(columns, np.inf, dtype=np.float64)
        reachable_end = (
            end_dx <= settings.max_horizontal_step_px + 1e-9
        )
        terminal_movement = _movement_cost(
            end_dx,
            max_horizontal_step_px=settings.max_horizontal_step_px,
        )
        terminal_cost[reachable_end] = (
            previous_cost[reachable_end]
            + terminal_movement[reachable_end]
        )

    if not np.isfinite(terminal_cost).any():
        return None

    end_local = int(np.nanargmin(terminal_cost))
    local_path = np.empty(rows, dtype=np.int32)
    local_path[-1] = end_local

    for row_index in range(rows - 1, 0, -1):
        previous_index = int(
            backpointers[row_index, local_path[row_index]]
        )
        if previous_index < 0:
            return None
        local_path[row_index - 1] = previous_index

    return local_path.astype(float)


def _movement_cost(
    distance_px: np.ndarray,
    *,
    max_horizontal_step_px: float,
) -> np.ndarray:
    """Small smoothness cost; future Flexibility will tune this term."""
    if max_horizontal_step_px <= 0.0:
        return np.full_like(distance_px, np.inf, dtype=float)
    normalized = np.asarray(distance_px, dtype=float) / max_horizontal_step_px
    return _MOVEMENT_COST_WEIGHT * normalized * normalized


def _assess_path_candidate(
    row: np.ndarray,
    *,
    candidate_x: float,
    previous_accepted_x: float,
    settings: AutomaticTraceSettings,
    line_width_px: float,
) -> tuple[float, float, bool]:
    """Map the selected path pixel back to the existing confidence contract."""
    width = int(row.shape[0])
    centre = int(round(candidate_x))
    background_half_width = max(
        3,
        int(round(2.5 * line_width_px)),
    )
    left = max(0, centre - background_half_width)
    right = min(width - 1, centre + background_half_width)
    local = np.asarray(row[left : right + 1], dtype=float)

    background = float(
        np.percentile(local, _ROW_BACKGROUND_PERCENTILE)
    )

    line_half_width = max(
        0,
        int(round(0.5 * line_width_px)),
    )
    line_left = max(0, centre - line_half_width)
    line_right = min(width - 1, centre + line_half_width)
    line_patch = np.asarray(
        row[line_left : line_right + 1],
        dtype=float,
    )
    candidate_value = float(np.min(line_patch))

    contrast_ratio = _dark_on_light_contrast(
        candidate_value,
        background,
    )
    horizontal_step = abs(
        candidate_x - previous_accepted_x
    )

    allowed_horizontal_step = settings.max_horizontal_step_px

    continuity = max(
        0.0,
        1.0
        - horizontal_step / allowed_horizontal_step,
    )
    confidence = float(
        np.clip(
            0.65 * contrast_ratio
            + 0.35 * continuity,
            0.0,
            1.0,
        )
    )
    reliable = (
        contrast_ratio >= settings.min_contrast_ratio
        and horizontal_step
        <= allowed_horizontal_step + 1e-9
        and confidence >= settings.min_confidence
    )

    return contrast_ratio, confidence, reliable


def _horizontal_box_average(
    block: np.ndarray,
    *,
    width_px: float,
) -> np.ndarray:
    kernel_width = max(
        1,
        int(round(width_px)),
    )
    if kernel_width % 2 == 0:
        kernel_width += 1
    if kernel_width == 1:
        return block.astype(np.float64, copy=True)

    pad = kernel_width // 2
    padded = np.pad(
        block.astype(np.float64, copy=False),
        ((0, 0), (pad, pad)),
        mode="edge",
    )
    cumulative = np.cumsum(
        padded,
        axis=1,
        dtype=np.float64,
    )
    cumulative = np.pad(
        cumulative,
        ((0, 0), (1, 0)),
        mode="constant",
        constant_values=0.0,
    )
    return (
        cumulative[:, kernel_width:]
        - cumulative[:, :-kernel_width]
    ) / float(kernel_width)


def _lookahead_rows(
    line_width_px: float,
) -> int:
    base = int(
        round(
            _LOOKAHEAD_LINE_WIDTH_MULTIPLIER
            * line_width_px
        )
    )

    return int(
        np.clip(
            base,
            _MIN_LOOKAHEAD_ROWS,
            _MAX_LOOKAHEAD_ROWS,
        )
    )


def _validated_line_width(
    line_width_px: float | None,
) -> float:
    if line_width_px is None:
        return _DEFAULT_LINE_WIDTH_PX
    try:
        value = float(line_width_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            "line_width_px must be a positive finite number."
        ) from exc
    if not isfinite(value) or value <= 0.0:
        raise AutomaticTraceError(
            "line_width_px must be a positive finite number."
        )
    return value



def _validated_grid_penalty_mask(
    mask: np.ndarray | None,
    *,
    expected_shape: tuple[int, int],
) -> np.ndarray | None:
    if mask is None:
        return None

    array = np.asarray(mask, dtype=np.float64)
    if array.shape != expected_shape:
        raise AutomaticTraceError(
            "grid_penalty_mask must match the tracking image shape."
        )
    if not np.isfinite(array).all():
        raise AutomaticTraceError(
            "grid_penalty_mask cannot contain NaN or infinite values."
        )
    return np.clip(array, 0.0, 1.0)



def _dark_on_light_contrast(
    candidate_value: float,
    background_value: float,
) -> float:
    if background_value <= 0.0:
        return 0.0
    ratio = (
        background_value - candidate_value
    ) / background_value
    return float(np.clip(ratio, 0.0, 1.0))


def _coerce_point(
    value: TracePoint | Sequence[float],
) -> TracePoint:
    if isinstance(value, TracePoint):
        return value

    try:
        x_px, y_px = value
        x = float(x_px)
        y = float(y_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTraceError(
            "Start point must contain exactly two finite coordinates."
        ) from exc

    if not isfinite(x) or not isfinite(y):
        raise AutomaticTraceError(
            "Start point must contain exactly two finite coordinates."
        )
    return TracePoint(x, y)


def _as_grayscale_float(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)

    if array.size == 0:
        raise AutomaticTraceError(
            "Tracking image cannot be empty."
        )

    if array.ndim == 2:
        gray = array.astype(np.float64, copy=False)
    elif array.ndim == 3 and array.shape[2] in (1, 3, 4):
        if array.shape[2] == 1:
            gray = array[..., 0].astype(
                np.float64,
                copy=False,
            )
        else:
            rgb = array[..., :3].astype(
                np.float64,
                copy=False,
            )
            gray = (
                0.2126 * rgb[..., 0]
                + 0.7152 * rgb[..., 1]
                + 0.0722 * rgb[..., 2]
            )
    else:
        raise AutomaticTraceError(
            "Tracking image must be a 2-D grayscale or 3/4-channel RGB(A) array."
        )

    if not np.isfinite(gray).all():
        raise AutomaticTraceError(
            "Tracking image cannot contain NaN or infinite values."
        )
    return gray
